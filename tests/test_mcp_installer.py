"""Cross-client MCP installer and lifecycle tests."""

import io
import json
import subprocess
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_ROOT = REPO_ROOT / "skills" / "safe-shell"
if str(SKILL_ROOT) not in sys.path:
    sys.path.insert(0, str(SKILL_ROOT))

from safe_shell_mcp import installer  # noqa: E402


NON_JSON_NUMBERS = ("NaN", "Infinity", "-Infinity")


class SafeShellMcpInstallerTests(unittest.TestCase):
    def setUp(self):
        self.tempdir = tempfile.TemporaryDirectory(
            prefix="safe-shell-mcp-installer-test-"
        )
        self.root = Path(self.tempdir.name)
        self.home = self.root / "home"
        self.project = self.root / "project"
        self.home.mkdir()
        self.project.mkdir()
        self.invocation = installer.Invocation("safe-shell-mcp")

    def tearDown(self):
        self.tempdir.cleanup()

    def install(self, clients=("claude-code",), **kwargs):
        return installer.install_configs(
            clients=list(clients),
            scope=kwargs.pop("scope", "project"),
            home=self.home,
            project_dir=self.project,
            invocation=kwargs.pop("invocation", self.invocation),
            **kwargs,
        )

    def test_project_install_writes_all_client_shapes(self):
        cursor_config = self.project / ".cursor" / "mcp.json"
        cursor_config.parent.mkdir()
        cursor_config.write_text(
            json.dumps({"mcpServers": {"other": {"command": "other"}}}),
            encoding="utf-8",
        )
        summary = self.install(clients=("all",))
        self.assertTrue(summary["ok"])
        claude = json.loads(
            (self.project / ".mcp.json").read_text(encoding="utf-8")
        )
        cursor = json.loads(cursor_config.read_text(encoding="utf-8"))
        opencode = json.loads(
            (self.project / "opencode.json").read_text(encoding="utf-8")
        )
        vscode = json.loads(
            (self.project / ".vscode" / "mcp.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(
            claude["mcpServers"]["safe-shell"]["command"], "safe-shell-mcp"
        )
        self.assertIn("other", cursor["mcpServers"])
        self.assertEqual(
            opencode["mcp"]["safe-shell"]["command"], ["safe-shell-mcp"]
        )
        self.assertEqual(vscode["servers"]["safe-shell"]["type"], "stdio")

    def test_user_install_uses_documented_locations_and_code_cli(self):
        with patch.object(installer.subprocess, "run") as run_mock:
            summary = installer.install_configs(
                clients=["all"],
                scope="user",
                home=self.home,
                project_dir=self.project,
                invocation=self.invocation,
                code_cli="code-test",
            )
        self.assertTrue((self.home / ".claude.json").is_file())
        self.assertTrue((self.home / ".cursor" / "mcp.json").is_file())
        self.assertTrue(
            (
                self.home
                / ".config"
                / "opencode"
                / "opencode.json"
            ).is_file()
        )
        run_mock.assert_called_once()
        argv = run_mock.call_args.args[0]
        self.assertEqual(argv[:2], ["code-test", "--add-mcp"])
        self.assertEqual(json.loads(argv[2])["name"], "safe-shell")
        self.assertEqual(len(summary["results"]), 4)

    def test_dry_run_does_not_write(self):
        summary = self.install(
            clients=("cursor", "opencode"), dry_run=True
        )
        self.assertFalse((self.project / ".cursor").exists())
        self.assertFalse((self.project / "opencode.json").exists())
        self.assertTrue(all(item["changed"] for item in summary["results"]))

    def test_invalid_json_prevents_all_file_writes(self):
        cursor_config = self.project / ".cursor" / "mcp.json"
        cursor_config.parent.mkdir()
        cursor_config.write_text("{ // jsonc\n}", encoding="utf-8")
        with self.assertRaises(installer.InstallError):
            self.install(clients=("claude-code", "cursor"))
        self.assertFalse((self.project / ".mcp.json").exists())
        self.assertEqual(
            cursor_config.read_text(encoding="utf-8"), "{ // jsonc\n}"
        )

    def test_install_rejects_non_json_numbers_without_file_writes(self):
        target = self.project / ".cursor" / "mcp.json"
        target.parent.mkdir()
        untouched = self.project / ".mcp.json"

        for constant in NON_JSON_NUMBERS:
            with self.subTest(constant=constant):
                raw = (
                    '{"mcpServers":{},"value":' + constant + "}\n"
                ).encode()
                target.write_bytes(raw)
                with self.assertRaises(installer.InstallError):
                    self.install(clients=("claude-code", "cursor"))
                self.assertEqual(target.read_bytes(), raw)
                self.assertFalse(untouched.exists())

    def test_status_rejects_non_json_numbers_without_modifying_config(self):
        target = self.project / ".mcp.json"

        for constant in NON_JSON_NUMBERS:
            with self.subTest(constant=constant):
                raw = (
                    '{"mcpServers":{},"value":' + constant + "}\n"
                ).encode()
                target.write_bytes(raw)
                result = installer.status_configs(
                    clients=["claude-code"],
                    scope="project",
                    home=self.home,
                    project_dir=self.project,
                )
                self.assertFalse(result["ok"])
                self.assertEqual(result["results"][0]["status"], "error")
                self.assertEqual(target.read_bytes(), raw)

    def test_uninstall_rejects_non_json_numbers_without_modifying_config(
        self,
    ):
        target = self.project / ".mcp.json"

        for constant in NON_JSON_NUMBERS:
            with self.subTest(constant=constant):
                raw = (
                    '{"mcpServers":{"safe-shell":{"command":"x"}},'
                    '"value":' + constant + "}\n"
                ).encode()
                target.write_bytes(raw)
                with self.assertRaises(installer.InstallError):
                    installer.uninstall_configs(
                        clients=["claude-code"],
                        scope="project",
                        home=self.home,
                        project_dir=self.project,
                    )
                self.assertEqual(target.read_bytes(), raw)

    def test_existing_config_gets_recoverable_backup(self):
        target = self.project / ".mcp.json"
        original = b'{"mcpServers":{"other":{"command":"x"}}}\n'
        target.write_bytes(original)
        summary = self.install()
        backup = Path(summary["results"][0]["backup"])
        self.assertEqual(backup.read_bytes(), original)
        updated = json.loads(target.read_text(encoding="utf-8"))
        self.assertIn("other", updated["mcpServers"])
        self.assertIn("safe-shell", updated["mcpServers"])

    def test_write_failure_rolls_back_completed_file_actions(self):
        real_write = installer._write_atomic

        def fail_on_cursor(action, **kwargs):
            if action.client == "cursor":
                raise OSError("simulated write failure")
            return real_write(action, **kwargs)

        with patch.object(
            installer, "_write_atomic", side_effect=fail_on_cursor
        ), self.assertRaises(installer.InstallError):
            self.install(clients=("claude-code", "cursor"))
        self.assertFalse((self.project / ".mcp.json").exists())

    def test_concurrent_change_aborts_without_overwriting_target(self):
        target = self.project / ".cursor" / "mcp.json"
        real_write = installer._write_atomic
        external = b'{"mcpServers":{"external":{"command":"new"}}}\n'

        def change_before_write(action, **kwargs):
            action.path.parent.mkdir(parents=True, exist_ok=True)
            action.path.write_bytes(external)
            return real_write(action, **kwargs)

        with patch.object(
            installer, "_write_atomic", side_effect=change_before_write
        ), self.assertRaises(installer.InstallError) as caught:
            self.install(clients=("cursor",))
        self.assertEqual(
            caught.exception.failure_class, "CONFIGURATION_FAILED"
        )
        self.assertEqual(target.read_bytes(), external)

    def test_rollback_preserves_newer_content_and_reports_failure(self):
        target = self.project / ".mcp.json"
        original = b'{"mcpServers":{"other":{"command":"old"}}}\n'
        external = b'{"mcpServers":{"external":{"command":"new"}}}\n'
        target.write_bytes(original)
        real_write = installer._write_atomic

        def fail_after_external_change(action, **kwargs):
            if action.client == "cursor":
                target.write_bytes(external)
                raise OSError("second write failed")
            return real_write(action, **kwargs)

        with patch.object(
            installer,
            "_write_atomic",
            side_effect=fail_after_external_change,
        ), self.assertRaises(installer.InstallError) as caught:
            self.install(clients=("claude-code", "cursor"))

        error = caught.exception
        self.assertEqual(target.read_bytes(), external)
        self.assertEqual(len(error.rollback_failures), 1)
        failure = error.rollback_failures[0]
        self.assertEqual(failure["path"], str(target))
        self.assertIn("configuration changed", failure["reason"])
        self.assertIsNotNone(failure["backup"])
        self.assertTrue(Path(failure["backup"]).is_file())
        self.assertIn(str(target), str(error))

    def test_uninstall_removes_only_safe_shell_and_creates_backup(self):
        target = self.project / ".mcp.json"
        target.write_text(
            json.dumps(
                {
                    "mcpServers": {
                        "safe-shell": {"command": "safe-shell-mcp"},
                        "other": {"command": "other"},
                    },
                    "unrelated": True,
                }
            ),
            encoding="utf-8",
        )
        original = target.read_bytes()
        summary = installer.uninstall_configs(
            clients=["claude-code"],
            scope="project",
            home=self.home,
            project_dir=self.project,
        )
        updated = json.loads(target.read_text(encoding="utf-8"))
        self.assertNotIn("safe-shell", updated["mcpServers"])
        self.assertIn("other", updated["mcpServers"])
        self.assertTrue(updated["unrelated"])
        backup = Path(summary["results"][0]["backup"])
        self.assertEqual(backup.read_bytes(), original)

    def test_uninstall_dry_run_and_missing_entry_are_non_destructive(self):
        target = self.project / ".mcp.json"
        original = b'{"mcpServers":{"other":{"command":"x"}}}\n'
        target.write_bytes(original)
        summary = installer.uninstall_configs(
            clients=["claude-code"],
            scope="project",
            home=self.home,
            project_dir=self.project,
            dry_run=True,
        )
        self.assertFalse(summary["results"][0]["changed"])
        self.assertEqual(target.read_bytes(), original)
        self.assertIsNone(summary["results"][0]["backup"])

    def test_vscode_user_uninstall_fails_before_other_clients_change(self):
        target = self.home / ".claude.json"
        original = b'{"mcpServers":{"safe-shell":{"command":"x"}}}\n'
        target.write_bytes(original)
        with self.assertRaises(installer.InstallError):
            installer.uninstall_configs(
                clients=["claude-code", "vscode"],
                scope="user",
                home=self.home,
                project_dir=self.project,
            )
        self.assertEqual(target.read_bytes(), original)

    def test_status_reports_configured_missing_and_unknown(self):
        target = self.project / ".mcp.json"
        target.write_text(
            '{"mcpServers":{"safe-shell":{"command":"x"}}}\n',
            encoding="utf-8",
        )
        project_status = installer.status_configs(
            clients=["claude-code", "cursor"],
            scope="project",
            home=self.home,
            project_dir=self.project,
        )
        self.assertTrue(project_status["ok"])
        self.assertTrue(project_status["results"][0]["configured"])
        self.assertFalse(project_status["results"][1]["configured"])

        user_status = installer.status_configs(
            clients=["vscode"],
            scope="user",
            home=self.home,
            project_dir=self.project,
        )
        self.assertFalse(user_status["ok"])
        self.assertIsNone(user_status["results"][0]["configured"])
        self.assertEqual(user_status["results"][0]["status"], "unknown")

    def test_server_invocation_ignores_an_unrelated_path_launcher(self):
        with patch.dict(
            installer.os.environ,
            {"SAFE_SHELL_MCP_EXECUTABLE": ""},
        ):
            with patch.object(installer.Path, "is_file", return_value=False):
                with patch.object(
                    installer.shutil,
                    "which",
                    return_value=r"C:\stale\safe-shell-mcp.exe",
                ) as which_mock:
                    invocation = installer._server_invocation()

        self.assertEqual(invocation.command, sys.executable)
        self.assertEqual(invocation.args, ("-m", "safe_shell_mcp.server"))
        which_mock.assert_not_called()

    def test_doctor_probes_real_stdio_server(self):
        invocation = installer.Invocation(
            sys.executable,
            (str(REPO_ROOT / "mcp" / "safe_shell_server.py"),),
        )
        result = installer.doctor_server(invocation=invocation, timeout=10)
        self.assertTrue(result["ok"], result)
        self.assertIn("safe_shell_quote", result["tools"])
        self.assertIn("safe_shell_quote_many", result["tools"])
        self.assertEqual(result["protocolVersion"], "2025-11-25")

    def test_doctor_timeout_is_structured(self):
        with patch.object(
            installer.subprocess,
            "run",
            side_effect=subprocess.TimeoutExpired(["server"], 0.01),
        ):
            result = installer.doctor_server(
                invocation=self.invocation, timeout=0.01
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["failureClass"], "TIMEOUT")

    def test_doctor_rejects_non_json_numbers(self):
        tools = (
            '{"jsonrpc":"2.0","id":2,"result":{"tools":['
            '{"name":"safe_shell_quote"},'
            '{"name":"safe_shell_quote_many"}]}}\n'
        )

        for constant in NON_JSON_NUMBERS:
            with self.subTest(constant=constant):
                initialize = (
                    '{"jsonrpc":"2.0","id":1,"result":'
                    '{"protocolVersion":"2025-11-25",'
                    '"serverInfo":{"value":'
                    + constant
                    + "}}}\n"
                )
                completed = subprocess.CompletedProcess(
                    args=["server"],
                    returncode=0,
                    stdout=(initialize + tools).encode(),
                    stderr=b"",
                )
                with patch.object(
                    installer.subprocess,
                    "run",
                    return_value=completed,
                ):
                    result = installer.doctor_server(
                        invocation=self.invocation
                    )
                self.assertFalse(result["ok"])
                self.assertEqual(result["failureClass"], "PROTOCOL_ERROR")
                self.assertIn("invalid JSON-RPC response", result["message"])
                self.assertIn(constant, result["message"])

    def test_main_accepts_root_and_legacy_install_parsing(self):
        for argv in (
            [
                "install",
                "--client",
                "cursor",
                "--scope",
                "project",
                "--project-dir",
                str(self.project),
                "--dry-run",
                "--json",
            ],
            [
                "--client",
                "cursor",
                "--scope",
                "project",
                "--project-dir",
                str(self.project),
                "--dry-run",
                "--json",
            ],
        ):
            output = io.StringIO()
            with redirect_stdout(output):
                return_code = installer.main(argv)
            self.assertEqual(return_code, 0)
            self.assertEqual(json.loads(output.getvalue())["operation"], "install")


if __name__ == "__main__":
    unittest.main()
