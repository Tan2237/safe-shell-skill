"""Manage safe-shell's stdio MCP server across supported AI clients."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Optional, Sequence


CLIENTS = ("claude-code", "cursor", "opencode", "vscode")
SERVER_KEY = "safe-shell"
LEGACY_PROTOCOL_VERSION = "2025-11-25"
DEFAULT_DOCTOR_TIMEOUT = 5.0


class InstallError(Exception):
    """Report a safe, structured configuration-management failure."""

    def __init__(
        self,
        message: str,
        *,
        failure_class: str = "INSTALL_ERROR",
        rollback_failures: Optional[list[dict[str, Any]]] = None,
    ) -> None:
        super().__init__(message)
        self.failure_class = failure_class
        self.rollback_failures = list(rollback_failures or [])

    def as_response(self) -> dict[str, Any]:
        response: dict[str, Any] = {
            "ok": False,
            "failureClass": self.failure_class,
            "error": str(self),
        }
        if self.rollback_failures:
            response["rollbackFailures"] = self.rollback_failures
        return response


class ConcurrentModificationError(OSError):
    """Raised when a configuration changed after its action was planned."""


@dataclass(frozen=True)
class Invocation:
    command: str
    args: tuple[str, ...] = ()


@dataclass(frozen=True)
class FileAction:
    client: str
    path: Path
    content: bytes
    changed: bool
    original: Optional[bytes]


@dataclass(frozen=True)
class CommandAction:
    client: str
    argv: tuple[str, ...]


@dataclass(frozen=True)
class AppliedFile:
    action: FileAction
    backup: Optional[Path]


def _server_invocation() -> Invocation:
    override = os.environ.get("SAFE_SHELL_MCP_EXECUTABLE")
    if override:
        return Invocation(str(Path(override).expanduser().resolve()))
    source_root = Path(__file__).resolve().parents[3]
    repository_launcher = source_root / "mcp" / "safe_shell_server.py"
    if repository_launcher.is_file():
        return Invocation(sys.executable, (str(repository_launcher.resolve()),))
    return Invocation(sys.executable, ("-m", "safe_shell_mcp.server"))


def _server_entry(client: str, invocation: Invocation) -> dict[str, Any]:
    if client == "opencode":
        return {
            "type": "local",
            "command": [invocation.command, *invocation.args],
            "enabled": True,
        }
    entry: dict[str, Any] = {
        "command": invocation.command,
        "args": list(invocation.args),
    }
    if client == "vscode":
        entry["type"] = "stdio"
    return entry


def _target_path(
    client: str, scope: str, home: Path, project_dir: Path
) -> Optional[Path]:
    if scope == "project":
        return {
            "claude-code": project_dir / ".mcp.json",
            "cursor": project_dir / ".cursor" / "mcp.json",
            "opencode": project_dir / "opencode.json",
            "vscode": project_dir / ".vscode" / "mcp.json",
        }[client]
    if client == "vscode":
        return None
    return {
        "claude-code": home / ".claude.json",
        "cursor": home / ".cursor" / "mcp.json",
        "opencode": home / ".config" / "opencode" / "opencode.json",
    }[client]


def _section_name(client: str) -> str:
    if client == "opencode":
        return "mcp"
    if client == "vscode":
        return "servers"
    return "mcpServers"


def _expand_clients(values: Iterable[str]) -> list[str]:
    requested: list[str] = []
    for value in values:
        names = CLIENTS if value == "all" else (value,)
        for name in names:
            if name not in requested:
                requested.append(name)
    return requested


def _validate_selection(clients: Sequence[str], scope: str) -> list[str]:
    if scope not in ("user", "project"):
        raise InstallError(f"unsupported scope: {scope}")
    selected = _expand_clients(clients)
    if not selected:
        raise InstallError("at least one client is required")
    unsupported = [client for client in selected if client not in CLIENTS]
    if unsupported:
        raise InstallError(f"unsupported client: {unsupported[0]}")
    return selected


def _snapshot(path: Path) -> Optional[bytes]:
    try:
        return path.read_bytes()
    except FileNotFoundError:
        return None


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


def _strict_json_loads(content: str) -> Any:
    return json.loads(content, parse_constant=_reject_json_constant)


def _decode_object(path: Path, raw: bytes) -> dict[str, Any]:
    try:
        value = _strict_json_loads(raw.decode("utf-8-sig"))
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        raise InstallError(
            f"refusing to rewrite non-JSON config {path}: {error}"
        ) from error
    if not isinstance(value, dict):
        raise InstallError(f"config root must be an object: {path}")
    return value


def _load_object(path: Path) -> tuple[dict[str, Any], Optional[bytes]]:
    try:
        raw = _snapshot(path)
    except OSError as error:
        raise InstallError(f"cannot read {path}: {error}") from error
    if raw is None:
        return {}, None
    return _decode_object(path, raw), raw


def _serialize_config(root: dict[str, Any]) -> bytes:
    return (json.dumps(root, ensure_ascii=False, indent=2) + "\n").encode(
        "utf-8"
    )


def _render_config(
    client: str, path: Path, invocation: Invocation
) -> FileAction:
    root, original = _load_object(path)
    section_name = _section_name(client)
    section = root.get(section_name, {})
    if not isinstance(section, dict):
        raise InstallError(f"{section_name} must be an object in {path}")
    updated_section = dict(section)
    updated_section[SERVER_KEY] = _server_entry(client, invocation)
    updated = dict(root)
    updated[section_name] = updated_section
    content = _serialize_config(updated)
    return FileAction(
        client=client,
        path=path,
        content=content,
        changed=content != original,
        original=original,
    )


def _render_uninstall_config(client: str, path: Path) -> FileAction:
    root, original = _load_object(path)
    if original is None:
        return FileAction(client, path, b"", False, None)
    section_name = _section_name(client)
    section = root.get(section_name)
    if section is None:
        return FileAction(client, path, original, False, original)
    if not isinstance(section, dict):
        raise InstallError(f"{section_name} must be an object in {path}")
    if SERVER_KEY not in section:
        return FileAction(client, path, original, False, original)
    updated_section = dict(section)
    del updated_section[SERVER_KEY]
    updated = dict(root)
    updated[section_name] = updated_section
    content = _serialize_config(updated)
    return FileAction(client, path, content, content != original, original)


def _next_backup(path: Path) -> Path:
    candidate = path.with_name(path.name + ".safe-shell.bak")
    counter = 1
    while candidate.exists():
        candidate = path.with_name(
            f"{path.name}.safe-shell.bak.{counter}"
        )
        counter += 1
    return candidate


def _assert_unchanged(path: Path, expected: Optional[bytes]) -> None:
    current = _snapshot(path)
    if current != expected:
        raise ConcurrentModificationError(
            f"configuration changed after planning; refusing to overwrite {path}"
        )


def _create_backup(action: FileAction) -> Optional[Path]:
    if action.original is None:
        return None
    while True:
        backup = _next_backup(action.path)
        try:
            with backup.open("xb") as stream:
                stream.write(action.original)
                stream.flush()
                os.fsync(stream.fileno())
            if action.path.exists():
                os.chmod(backup, action.path.stat().st_mode)
            return backup
        except FileExistsError:
            continue


def _write_atomic(
    action: FileAction, *, create_backup: bool = True
) -> Optional[Path]:
    if not action.changed:
        return None
    action.path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary_name = tempfile.mkstemp(
        prefix=f".{action.path.name}.",
        suffix=".tmp",
        dir=str(action.path.parent),
    )
    temporary = Path(temporary_name)
    backup: Optional[Path] = None
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(action.content)
            stream.flush()
            os.fsync(stream.fileno())

        _assert_unchanged(action.path, action.original)
        if create_backup:
            backup = _create_backup(action)
        _assert_unchanged(action.path, action.original)

        if action.path.exists():
            os.chmod(temporary, action.path.stat().st_mode)
        os.replace(str(temporary), str(action.path))
    finally:
        if temporary.exists():
            temporary.unlink()
    return backup


def _restore_file(action: FileAction) -> None:
    _assert_unchanged(action.path, action.content)
    if action.original is None:
        action.path.unlink()
        return
    rollback = FileAction(
        client=action.client,
        path=action.path,
        content=action.original,
        changed=True,
        original=action.content,
    )
    _write_atomic(rollback, create_backup=False)


def _rollback_applied(
    applied: Sequence[AppliedFile],
) -> list[dict[str, Any]]:
    failures: list[dict[str, Any]] = []
    for item in reversed(applied):
        try:
            _restore_file(item.action)
        except Exception as error:
            failures.append(
                {
                    "path": str(item.action.path),
                    "reason": f"{type(error).__name__}: {error}",
                    "backup": str(item.backup) if item.backup else None,
                }
            )
    return failures


def _raise_apply_error(
    error: Exception, applied: Sequence[AppliedFile]
) -> None:
    rollback_failures = _rollback_applied(applied)
    if rollback_failures:
        paths = ", ".join(item["path"] for item in rollback_failures)
        message = (
            f"configuration failed: {error}; rollback also failed for: {paths}"
        )
    else:
        message = (
            "configuration failed; completed file writes were rolled back: "
            f"{error}"
        )
    raise InstallError(
        message,
        failure_class="CONFIGURATION_FAILED",
        rollback_failures=rollback_failures,
    ) from error


def _vscode_user_action(
    invocation: Invocation, code_cli: Optional[str]
) -> CommandAction:
    executable = code_cli or shutil.which("code")
    if not executable:
        raise InstallError(
            "VS Code user install requires the 'code' CLI; "
            "use --scope project or enable the shell command first"
        )
    payload = {
        "name": SERVER_KEY,
        "type": "stdio",
        "command": invocation.command,
        "args": list(invocation.args),
    }
    return CommandAction(
        client="vscode",
        argv=(
            executable,
            "--add-mcp",
            json.dumps(payload, ensure_ascii=False, separators=(",", ":")),
        ),
    )


def _action_result(
    action: FileAction, backup: Optional[Path]
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "client": action.client,
        "kind": "file",
        "path": str(action.path),
        "changed": action.changed,
        "backup": str(backup) if backup else None,
    }
    if action.content:
        result["config"] = json.loads(action.content.decode("utf-8"))
    return result


def _apply_actions(
    *,
    operation: str,
    scope: str,
    files: Sequence[FileAction],
    commands: Sequence[CommandAction],
    dry_run: bool,
    server: Optional[Invocation] = None,
) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    applied: list[AppliedFile] = []
    try:
        for action in files:
            backup = None if dry_run else _write_atomic(action)
            if not dry_run and action.changed:
                applied.append(AppliedFile(action, backup))
            results.append(_action_result(action, backup))

        for action in commands:
            if not dry_run:
                subprocess.run(list(action.argv), check=True)
            results.append(
                {
                    "client": action.client,
                    "kind": "command",
                    "argv": list(action.argv),
                    "changed": not dry_run,
                    "backup": None,
                }
            )
    except (OSError, subprocess.CalledProcessError) as error:
        _raise_apply_error(error, applied)

    summary: dict[str, Any] = {
        "ok": True,
        "operation": operation,
        "dryRun": dry_run,
        "scope": scope,
        "results": results,
    }
    if server is not None:
        summary["server"] = {
            "command": server.command,
            "args": list(server.args),
        }
    return summary


def install_configs(
    clients: Sequence[str],
    scope: str,
    home: Path,
    project_dir: Path,
    dry_run: bool = False,
    invocation: Optional[Invocation] = None,
    code_cli: Optional[str] = None,
) -> dict[str, Any]:
    selected = _validate_selection(clients, scope)
    server = invocation or _server_invocation()
    files: list[FileAction] = []
    commands: list[CommandAction] = []

    for client in selected:
        target = _target_path(client, scope, home, project_dir)
        if target is None:
            commands.append(_vscode_user_action(server, code_cli))
        else:
            files.append(_render_config(client, target, server))

    return _apply_actions(
        operation="install",
        scope=scope,
        files=files,
        commands=commands,
        dry_run=dry_run,
        server=server,
    )


def uninstall_configs(
    clients: Sequence[str],
    scope: str,
    home: Path,
    project_dir: Path,
    dry_run: bool = False,
) -> dict[str, Any]:
    selected = _validate_selection(clients, scope)
    files: list[FileAction] = []

    for client in selected:
        target = _target_path(client, scope, home, project_dir)
        if target is None:
            raise InstallError(
                "VS Code user uninstall is unavailable because the code CLI "
                "does not provide a safe remove-MCP operation; remove the "
                "safe-shell entry in VS Code explicitly"
            )
        files.append(_render_uninstall_config(client, target))

    return _apply_actions(
        operation="uninstall",
        scope=scope,
        files=files,
        commands=[],
        dry_run=dry_run,
    )


def status_configs(
    clients: Sequence[str],
    scope: str,
    home: Path,
    project_dir: Path,
) -> dict[str, Any]:
    selected = _validate_selection(clients, scope)
    results: list[dict[str, Any]] = []
    all_known = True

    for client in selected:
        target = _target_path(client, scope, home, project_dir)
        if target is None:
            all_known = False
            results.append(
                {
                    "client": client,
                    "kind": "command",
                    "configured": None,
                    "status": "unknown",
                    "message": (
                        "VS Code user MCP state cannot be inspected safely "
                        "through the code CLI"
                    ),
                }
            )
            continue

        result: dict[str, Any] = {
            "client": client,
            "kind": "file",
            "path": str(target),
        }
        try:
            root, original = _load_object(target)
            if original is None:
                configured = False
                entry = None
            else:
                section = root.get(_section_name(client), {})
                if not isinstance(section, dict):
                    raise InstallError(
                        f"{_section_name(client)} must be an object in {target}"
                    )
                entry = section.get(SERVER_KEY)
                configured = SERVER_KEY in section
            result.update(
                {
                    "configured": configured,
                    "status": "configured" if configured else "not-configured",
                    "entry": entry,
                }
            )
        except InstallError as error:
            all_known = False
            result.update(
                {
                    "configured": None,
                    "status": "error",
                    "error": str(error),
                }
            )
        results.append(result)

    return {
        "ok": all_known,
        "operation": "status",
        "scope": scope,
        "allKnown": all_known,
        "results": results,
    }


def _doctor_failure(
    failure_class: str,
    message: str,
    invocation: Invocation,
    started: float,
    **extra: Any,
) -> dict[str, Any]:
    result: dict[str, Any] = {
        "ok": False,
        "operation": "doctor",
        "failureClass": failure_class,
        "message": message,
        "elapsedMs": round((time.perf_counter() - started) * 1000, 3),
        "server": {
            "command": invocation.command,
            "args": list(invocation.args),
        },
    }
    result.update(extra)
    return result


def doctor_server(
    invocation: Optional[Invocation] = None,
    timeout: float = DEFAULT_DOCTOR_TIMEOUT,
) -> dict[str, Any]:
    server = invocation or _server_invocation()
    if timeout <= 0:
        raise InstallError("doctor timeout must be greater than zero")
    messages = [
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "initialize",
            "params": {
                "protocolVersion": LEGACY_PROTOCOL_VERSION,
                "capabilities": {},
                "clientInfo": {"name": "safe-shell-doctor", "version": "1"},
            },
        },
        {
            "jsonrpc": "2.0",
            "method": "notifications/initialized",
            "params": {},
        },
        {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}},
    ]
    payload = (
        "\n".join(json.dumps(message) for message in messages) + "\n"
    ).encode("utf-8")
    started = time.perf_counter()
    argv = [server.command, *server.args]

    try:
        completed = subprocess.run(
            argv,
            input=payload,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
            check=False,
        )
    except subprocess.TimeoutExpired:
        return _doctor_failure(
            "TIMEOUT",
            f"server probe exceeded {timeout:g} seconds",
            server,
            started,
        )
    except OSError as error:
        return _doctor_failure(
            "SERVER_START_FAILED", str(error), server, started
        )

    stderr = completed.stderr.decode("utf-8", errors="replace").strip()
    if completed.returncode != 0:
        return _doctor_failure(
            "SERVER_EXITED",
            f"server exited with status {completed.returncode}",
            server,
            started,
            stderr=stderr,
        )

    responses: dict[Any, dict[str, Any]] = {}
    try:
        output = completed.stdout.decode("utf-8")
        for line in output.splitlines():
            if not line.strip():
                continue
            response = _strict_json_loads(line)
            if not isinstance(response, dict):
                raise ValueError("response must be a JSON object")
            responses[response.get("id")] = response
    except (UnicodeDecodeError, ValueError, RecursionError) as error:
        return _doctor_failure(
            "PROTOCOL_ERROR",
            f"invalid JSON-RPC response: {error}",
            server,
            started,
            stderr=stderr,
        )

    initialize = responses.get(1)
    tools_response = responses.get(2)
    if not initialize or "error" in initialize:
        return _doctor_failure(
            "PROTOCOL_ERROR",
            "initialize did not return a successful response",
            server,
            started,
            stderr=stderr,
        )
    if not tools_response or "error" in tools_response:
        return _doctor_failure(
            "PROTOCOL_ERROR",
            "tools/list did not return a successful response",
            server,
            started,
            stderr=stderr,
        )

    initialize_result = initialize.get("result", {})
    tools_result = tools_response.get("result", {})
    if not isinstance(initialize_result, dict) or not isinstance(
        tools_result, dict
    ):
        return _doctor_failure(
            "PROTOCOL_ERROR",
            "JSON-RPC result must be an object",
            server,
            started,
            stderr=stderr,
        )
    tools = tools_result.get("tools")
    if not isinstance(tools, list):
        return _doctor_failure(
            "PROTOCOL_ERROR",
            "tools/list result is missing tools",
            server,
            started,
            stderr=stderr,
        )
    tool_names = [
        tool.get("name")
        for tool in tools
        if isinstance(tool, dict) and isinstance(tool.get("name"), str)
    ]
    required_tools = {"safe_shell_quote", "safe_shell_quote_many"}
    missing_tools = sorted(required_tools.difference(tool_names))
    if missing_tools:
        return _doctor_failure(
            "TOOL_NOT_FOUND",
            "required safe-shell tools were not advertised",
            server,
            started,
            tools=tool_names,
            stderr=stderr,
        )

    return {
        "ok": True,
        "operation": "doctor",
        "elapsedMs": round((time.perf_counter() - started) * 1000, 3),
        "server": {
            "command": server.command,
            "args": list(server.args),
        },
        "protocolVersion": initialize_result.get("protocolVersion"),
        "serverInfo": initialize_result.get("serverInfo"),
        "tools": tool_names,
        "stderr": stderr,
    }


def _add_client_arguments(
    parser: argparse.ArgumentParser, *, include_dry_run: bool = False
) -> None:
    parser.add_argument(
        "--client",
        action="append",
        required=True,
        choices=(*CLIENTS, "all"),
        help="client to inspect or configure; repeat or use 'all'",
    )
    parser.add_argument(
        "--scope", choices=("user", "project"), default="user"
    )
    parser.add_argument("--project-dir", type=Path, default=Path.cwd())
    parser.add_argument("--home", type=Path, default=Path.home())
    if include_dry_run:
        parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--json", action="store_true")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="safe-shell-mcp",
        description="Manage and diagnose the persistent safe-shell MCP server.",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    install_parser = subparsers.add_parser(
        "install",
        help="merge safe-shell into supported client configuration",
    )
    _add_client_arguments(install_parser, include_dry_run=True)

    uninstall_parser = subparsers.add_parser(
        "uninstall",
        help="remove only the safe-shell client entry",
    )
    _add_client_arguments(uninstall_parser, include_dry_run=True)

    status_parser = subparsers.add_parser(
        "status",
        help="inspect whether client configuration contains safe-shell",
    )
    _add_client_arguments(status_parser)

    doctor_parser = subparsers.add_parser(
        "doctor",
        help="start the configured server and probe initialize plus tools/list",
    )
    doctor_parser.add_argument(
        "--timeout",
        type=float,
        default=DEFAULT_DOCTOR_TIMEOUT,
        help="probe timeout in seconds",
    )
    doctor_parser.add_argument("--json", action="store_true")
    return parser


def _resolved_paths(args: argparse.Namespace) -> tuple[Path, Path]:
    return (
        args.home.expanduser().resolve(),
        args.project_dir.expanduser().resolve(),
    )


def _print_summary(summary: dict[str, Any]) -> None:
    operation = summary["operation"]
    if operation in ("install", "uninstall"):
        dry_run = summary["dryRun"]
        for result in summary["results"]:
            destination = result.get("path") or " ".join(result["argv"])
            if operation == "install":
                if result["changed"]:
                    state = "would configure" if dry_run else "configured"
                else:
                    state = "already configured"
            else:
                if result["changed"]:
                    state = "would remove" if dry_run else "removed"
                else:
                    state = "not configured"
            print(f"{result['client']}: {state} {destination}")
            if result.get("backup"):
                print(f"  backup: {result['backup']}")
        return

    if operation == "status":
        for result in summary["results"]:
            destination = result.get("path", "user configuration")
            print(f"{result['client']}: {result['status']} {destination}")
            if result.get("error") or result.get("message"):
                print(f"  {result.get('error') or result.get('message')}")
        return

    if summary["ok"]:
        print(
            "doctor: ok "
            f"protocol={summary.get('protocolVersion')} "
            f"tools={','.join(summary.get('tools', []))}"
        )
    else:
        print(
            f"doctor: {summary.get('failureClass')}: "
            f"{summary.get('message')}",
            file=sys.stderr,
        )


def main(argv: Optional[Sequence[str]] = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    commands = {"install", "uninstall", "status", "doctor"}
    if raw_args and raw_args[0] not in commands and raw_args[0].startswith("-"):
        raw_args.insert(0, "install")

    parser = _build_parser()
    args = parser.parse_args(raw_args)

    try:
        if args.command == "doctor":
            summary = doctor_server(timeout=args.timeout)
        else:
            home, project_dir = _resolved_paths(args)
            if args.command == "install":
                summary = install_configs(
                    clients=args.client,
                    scope=args.scope,
                    home=home,
                    project_dir=project_dir,
                    dry_run=args.dry_run,
                )
            elif args.command == "uninstall":
                summary = uninstall_configs(
                    clients=args.client,
                    scope=args.scope,
                    home=home,
                    project_dir=project_dir,
                    dry_run=args.dry_run,
                )
            else:
                summary = status_configs(
                    clients=args.client,
                    scope=args.scope,
                    home=home,
                    project_dir=project_dir,
                )
    except InstallError as error:
        if getattr(args, "json", False):
            print(json.dumps(error.as_response(), ensure_ascii=False))
        else:
            print(f"safe-shell-mcp: {error}", file=sys.stderr)
            for failure in error.rollback_failures:
                print(
                    "  rollback failed: "
                    f"{failure['path']}: {failure['reason']}; "
                    f"backup={failure['backup']}",
                    file=sys.stderr,
                )
        return 1

    if getattr(args, "json", False):
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    else:
        _print_summary(summary)
    return 0 if summary.get("ok") else 1
