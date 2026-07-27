#!/usr/bin/env python3
"""Long-lived MCP transport for one-argument shell quoting."""

from __future__ import annotations

import json
import sys
import time
from typing import Any

import safe_shell as core

from . import __version__


MAX_REQUEST_BYTES = 8 * 1024 * 1024
SERVER_NAME = "safe-shell"
SERVER_VERSION = __version__
SUPPORTED_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)


class ToolInputError(Exception):
    """Raised when a direct MCP tool request violates its schema."""


def _require_arguments(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ToolInputError("arguments must be an object")
    unknown = sorted(set(value) - {"shell", "text"})
    if unknown:
        raise ToolInputError(f"unknown argument field(s): {', '.join(unknown)}")
    return value


def execute_tool(name: str, raw_arguments: Any) -> dict[str, Any]:
    """Execute one already-decoded structured quote request."""
    if name != "safe_shell_quote":
        raise ToolInputError(f"unknown tool: {name}")
    arguments = _require_arguments(raw_arguments)
    started = time.perf_counter_ns()
    result = dict(core.process_request(arguments))
    result["transport"] = "mcp-structured"
    result["elapsedMs"] = round(
        (time.perf_counter_ns() - started) / 1_000_000, 3
    )
    return result


def _tool_result(summary: dict[str, Any]) -> dict[str, Any]:
    if summary.get("ok"):
        text = (
            "safe-shell quoted one "
            f"{summary.get('shell')} argument in {summary.get('elapsedMs')} ms"
        )
        return {
            "content": [{"type": "text", "text": text}],
            "structuredContent": summary,
        }
    return {
        "content": [
            {
                "type": "text",
                "text": (
                    f"safe-shell {summary.get('failureClass')}: "
                    f"{summary.get('message')}"
                ),
            }
        ],
        "structuredContent": summary,
        "isError": True,
    }


def _tool_failure(error: Exception) -> dict[str, Any]:
    summary = {
        "ok": False,
        "failureClass": "INVALID_TOOL_ARGUMENT",
        "message": str(error),
        "transport": "mcp-structured",
    }
    return _tool_result(summary)


TOOLS = [
    {
        "name": "safe_shell_quote",
        "title": "Quote one shell argument",
        "description": (
            "Quote exactly one raw data argument for insertion into shell "
            "source text. Use only when an argv-array execution API is not "
            "available. Never use the result as command code, eval input, "
            "pipeline syntax, or a bash -c / PowerShell -Command operand."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "shell": {
                    "type": "string",
                    "enum": [
                        "bash",
                        "zsh",
                        "fish",
                        "powershell",
                        "cmd",
                        "msys2",
                    ],
                },
                "text": {
                    "type": "string",
                    "description": "The raw value of exactly one argument.",
                },
            },
            "required": ["shell", "text"],
            "additionalProperties": False,
        },
        "annotations": {
            "readOnlyHint": True,
            "destructiveHint": False,
            "idempotentHint": True,
            "openWorldHint": False,
        },
    }
]


def _rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(request_id: Any, code: int, message: str) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": request_id,
        "error": {"code": code, "message": message},
    }


def _negotiate_protocol_version(requested: Any) -> str:
    if requested in SUPPORTED_PROTOCOL_VERSIONS:
        return requested
    return SUPPORTED_PROTOCOL_VERSIONS[0]


def handle_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid Request")

    request_id = message.get("id")
    method = message.get("method")
    params = message.get("params")
    if params is None:
        params = {}
    elif not isinstance(params, dict):
        return _rpc_error(request_id, -32602, "Invalid params")

    if method == "initialize":
        return _rpc_result(
            request_id,
            {
                "protocolVersion": _negotiate_protocol_version(
                    params.get("protocolVersion")
                ),
                "capabilities": {"tools": {}},
                "serverInfo": {
                    "name": SERVER_NAME,
                    "version": SERVER_VERSION,
                },
                "instructions": (
                    "Use safe_shell_quote only to form exactly one dynamic "
                    "data argument inside shell source text. Prefer raw argv "
                    "arrays when available, and never use output as code."
                ),
            },
        )
    if method == "ping":
        return _rpc_result(request_id, {})
    if method == "tools/list":
        return _rpc_result(request_id, {"tools": TOOLS})
    if method == "tools/call":
        try:
            result = _tool_result(
                execute_tool(
                    params.get("name", ""), params.get("arguments")
                )
            )
        except Exception as error:
            result = _tool_failure(error)
        return _rpc_result(request_id, result)
    if method in ("notifications/initialized", "notifications/cancelled"):
        return None
    if request_id is None:
        return None
    return _rpc_error(request_id, -32601, f"Method not found: {method}")


def _write_message(stream: Any, message: dict[str, Any]) -> None:
    data = json.dumps(
        message, ensure_ascii=False, separators=(",", ":")
    ).encode("utf-8")
    stream.write(data + b"\n")
    stream.flush()


def serve(input_stream: Any = None, output_stream: Any = None) -> None:
    source = input_stream or sys.stdin.buffer
    target = output_stream or sys.stdout.buffer

    while True:
        line = source.readline(MAX_REQUEST_BYTES + 1)
        if not line:
            return
        if len(line) > MAX_REQUEST_BYTES:
            while line and not line.endswith(b"\n"):
                line = source.readline(MAX_REQUEST_BYTES + 1)
            _write_message(
                target,
                _rpc_error(None, -32600, "Request exceeds 8 MiB limit"),
            )
            continue
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except (UnicodeDecodeError, json.JSONDecodeError):
            _write_message(target, _rpc_error(None, -32700, "Parse error"))
            continue
        response = handle_message(message)
        if response is not None:
            _write_message(target, response)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if args:
        if args[0] == "install":
            from .installer import main as install_main

            return install_main(args[1:])
        if args == ["--version"]:
            print(SERVER_VERSION)
            return 0
        print(
            "usage: safe-shell-mcp [--version] | "
            "safe-shell-mcp install [options]",
            file=sys.stderr,
        )
        return 2
    serve()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
