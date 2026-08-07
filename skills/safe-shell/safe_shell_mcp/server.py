#!/usr/bin/env python3
"""Long-lived dual-era MCP transport for safe shell quoting."""

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
MODERN_PROTOCOL_VERSION = "2026-07-28"
LEGACY_PROTOCOL_VERSIONS = (
    "2025-11-25",
    "2025-06-18",
    "2025-03-26",
    "2024-11-05",
)
SUPPORTED_PROTOCOL_VERSIONS = (
    MODERN_PROTOCOL_VERSION,
    *LEGACY_PROTOCOL_VERSIONS,
)
SHELLS = tuple(sorted(core.SUPPORTED_SHELLS))
CACHE_TTL_MS = 3_600_000
CACHE_SCOPE = "public"
SERVER_INFO = {"name": SERVER_NAME, "version": SERVER_VERSION}
SERVER_RESULT_META = {
    "io.modelcontextprotocol/serverInfo": SERVER_INFO,
}
INSTRUCTIONS = (
    "Use safe_shell_quote or safe_shell_quote_many only to form dynamic "
    "data arguments inside shell source text. Prefer raw argv arrays when "
    "available, and never use quoted output as command code or eval input."
)
MODERN_META_KEYS = frozenset(
    {
        "io.modelcontextprotocol/protocolVersion",
        "io.modelcontextprotocol/clientCapabilities",
        "io.modelcontextprotocol/clientInfo",
        "io.modelcontextprotocol/logLevel",
    }
)


class ToolInputError(Exception):
    """Raised when an MCP method or tool input violates its schema."""


class UnsupportedProtocolVersion(Exception):
    """Raised when per-request metadata selects an unsupported version."""

    def __init__(self, requested: str) -> None:
        self.requested = requested
        super().__init__(requested)


WARNING_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
    },
    "required": ["code", "message"],
    "additionalProperties": False,
}
BATCH_WARNING_SCHEMA = {
    "type": "object",
    "properties": {
        "code": {"type": "string"},
        "message": {"type": "string"},
        "index": {"type": "integer", "minimum": 0},
    },
    "required": ["code", "message", "index"],
    "additionalProperties": False,
}
FAILURE_PROPERTIES = {
    "ok": {"const": False},
    "failureClass": {"type": "string"},
    "message": {"type": "string"},
    "index": {"type": "integer", "minimum": 0},
    "transport": {"const": "mcp-structured"},
    "elapsedMs": {"type": "number", "minimum": 0},
}
SINGLE_OUTPUT_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "ok": {"const": True},
                "shell": {"type": "string", "enum": list(SHELLS)},
                "quoted": {"type": "string"},
                "warnings": {
                    "type": "array",
                    "items": WARNING_SCHEMA,
                },
                "transport": {"const": "mcp-structured"},
                "elapsedMs": {"type": "number", "minimum": 0},
            },
            "required": [
                "ok",
                "shell",
                "quoted",
                "transport",
                "elapsedMs",
            ],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": FAILURE_PROPERTIES,
            "required": [
                "ok",
                "failureClass",
                "message",
                "transport",
                "elapsedMs",
            ],
            "additionalProperties": False,
        },
    ]
}
MANY_OUTPUT_SCHEMA = {
    "oneOf": [
        {
            "type": "object",
            "properties": {
                "ok": {"const": True},
                "shell": {"type": "string", "enum": list(SHELLS)},
                "count": {
                    "type": "integer",
                    "minimum": 1,
                    "maximum": core.MAX_BATCH_ITEMS,
                },
                "quoted": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": core.MAX_BATCH_ITEMS,
                },
                "warnings": {
                    "type": "array",
                    "items": BATCH_WARNING_SCHEMA,
                },
                "transport": {"const": "mcp-structured"},
                "elapsedMs": {"type": "number", "minimum": 0},
            },
            "required": [
                "ok",
                "shell",
                "count",
                "quoted",
                "transport",
                "elapsedMs",
            ],
            "additionalProperties": False,
        },
        {
            "type": "object",
            "properties": FAILURE_PROPERTIES,
            "required": [
                "ok",
                "failureClass",
                "message",
                "transport",
                "elapsedMs",
            ],
            "additionalProperties": False,
        },
    ]
}
TOOL_ANNOTATIONS = {
    "readOnlyHint": True,
    "destructiveHint": False,
    "idempotentHint": True,
    "openWorldHint": False,
}
TOOLS = [
    {
        "name": "safe_shell_quote",
        "title": "Quote one shell argument",
        "description": (
            "Quote exactly one raw data argument for insertion into shell "
            "source text. Use only when an argv-array execution API is not "
            "available. Never use the result as command code, eval input, "
            "pipeline syntax, or a shell command operand."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "shell": {
                    "type": "string",
                    "enum": list(SHELLS),
                },
                "text": {
                    "type": "string",
                    "description": "The raw value of exactly one argument.",
                },
            },
            "required": ["shell", "text"],
            "additionalProperties": False,
        },
        "outputSchema": SINGLE_OUTPUT_SCHEMA,
        "annotations": TOOL_ANNOTATIONS,
    },
    {
        "name": "safe_shell_quote_many",
        "title": "Quote multiple shell arguments",
        "description": (
            "Quote an ordered array of independent data arguments for one "
            "shell. Each returned element is one argument; never join the "
            "results into command code without preserving argument boundaries."
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "shell": {
                    "type": "string",
                    "enum": list(SHELLS),
                },
                "texts": {
                    "type": "array",
                    "items": {"type": "string"},
                    "minItems": 1,
                    "maxItems": core.MAX_BATCH_ITEMS,
                    "description": "Raw values of independent arguments.",
                },
            },
            "required": ["shell", "texts"],
            "additionalProperties": False,
        },
        "outputSchema": MANY_OUTPUT_SCHEMA,
        "annotations": TOOL_ANNOTATIONS,
    },
]


def _unknown_fields(
    value: dict[str, Any], allowed: set[str], label: str
) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ToolInputError(
            f"{label} has unknown field(s): {', '.join(unknown)}"
        )


def _required_fields(
    value: dict[str, Any], required: tuple[str, ...], label: str
) -> None:
    missing = [field for field in required if field not in value]
    if missing:
        raise ToolInputError(
            f"{label} is missing required field(s): {', '.join(missing)}"
        )


def _validate_tool_arguments(
    name: str, raw_arguments: Any
) -> dict[str, Any]:
    if name not in {"safe_shell_quote", "safe_shell_quote_many"}:
        raise ToolInputError(f"unknown tool: {name}")
    if not isinstance(raw_arguments, dict):
        raise ToolInputError("arguments must be an object")

    value_field = "text" if name == "safe_shell_quote" else "texts"
    _unknown_fields(raw_arguments, {"shell", value_field}, "arguments")
    _required_fields(raw_arguments, ("shell", value_field), "arguments")

    shell = raw_arguments["shell"]
    if not isinstance(shell, str):
        raise ToolInputError("shell must be a string")
    if shell not in SHELLS:
        raise ToolInputError(f"shell must be one of: {', '.join(SHELLS)}")

    if name == "safe_shell_quote":
        if not isinstance(raw_arguments["text"], str):
            raise ToolInputError("text must be a string")
    else:
        texts = raw_arguments["texts"]
        if not isinstance(texts, list):
            raise ToolInputError("texts must be an array")
        if not 1 <= len(texts) <= core.MAX_BATCH_ITEMS:
            raise ToolInputError(
                f"texts must contain 1..{core.MAX_BATCH_ITEMS} items"
            )
        for index, text in enumerate(texts):
            if not isinstance(text, str):
                raise ToolInputError(f"texts[{index}] must be a string")
    return raw_arguments


def execute_tool(name: str, raw_arguments: Any) -> dict[str, Any]:
    """Execute one already-decoded structured quote request."""
    arguments = _validate_tool_arguments(name, raw_arguments)
    started = time.perf_counter_ns()
    if name == "safe_shell_quote":
        processed = core.process_request(arguments)
    else:
        processed = core.process_batch_request(arguments)
    if not isinstance(processed, dict):
        raise RuntimeError("core returned a non-object result")
    result = dict(processed)
    result["transport"] = "mcp-structured"
    result["elapsedMs"] = round(
        (time.perf_counter_ns() - started) / 1_000_000, 3
    )
    return result


def _tool_result(summary: dict[str, Any]) -> dict[str, Any]:
    text = json.dumps(
        summary, ensure_ascii=False, separators=(",", ":")
    )
    result = {
        "content": [{"type": "text", "text": text}],
        "structuredContent": summary,
    }
    if not summary.get("ok"):
        result["isError"] = True
    return result


def _tool_failure(error: Exception) -> dict[str, Any]:
    summary = {
        "ok": False,
        "failureClass": "INTERNAL_ERROR",
        "message": f"unexpected error: {type(error).__name__}",
        "transport": "mcp-structured",
        "elapsedMs": 0.0,
    }
    return _tool_result(summary)


def _rpc_result(request_id: Any, result: Any) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": request_id, "result": result}


def _rpc_error(
    request_id: Any,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    error = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "id": request_id, "error": error}


def _invalid_params(request_id: Any, error: Exception) -> dict[str, Any]:
    return _rpc_error(
        request_id,
        -32602,
        "Invalid params",
        {"detail": str(error)},
    )


def _negotiate_protocol_version(requested: Any) -> str:
    if requested in LEGACY_PROTOCOL_VERSIONS:
        return requested
    return LEGACY_PROTOCOL_VERSIONS[0]


def _validate_implementation(value: Any, label: str) -> None:
    if not isinstance(value, dict):
        raise ToolInputError(f"{label} must be an object")
    _required_fields(value, ("name", "version"), label)
    for field in ("name", "version"):
        if not isinstance(value[field], str):
            raise ToolInputError(f"{label}.{field} must be a string")


def _validate_progress_token(meta: dict[str, Any], label: str) -> None:
    if "progressToken" not in meta:
        return
    token = meta["progressToken"]
    if isinstance(token, bool) or not isinstance(token, (str, int, float)):
        raise ToolInputError(
            f"{label}.progressToken must be a string or number"
        )


def _validate_modern_meta(params: dict[str, Any]) -> None:
    if "_meta" not in params:
        raise ToolInputError("params._meta is required")
    meta = params["_meta"]
    if not isinstance(meta, dict):
        raise ToolInputError("params._meta must be an object")
    version_key = "io.modelcontextprotocol/protocolVersion"
    capabilities_key = "io.modelcontextprotocol/clientCapabilities"
    _required_fields(meta, (version_key, capabilities_key), "params._meta")

    requested = meta[version_key]
    if not isinstance(requested, str):
        raise ToolInputError(f"params._meta.{version_key} must be a string")
    capabilities = meta[capabilities_key]
    if not isinstance(capabilities, dict):
        raise ToolInputError(
            f"params._meta.{capabilities_key} must be an object"
        )
    client_info_key = "io.modelcontextprotocol/clientInfo"
    if client_info_key in meta:
        _validate_implementation(
            meta[client_info_key],
            f"params._meta.{client_info_key}",
        )
    log_level_key = "io.modelcontextprotocol/logLevel"
    if log_level_key in meta:
        log_level = meta[log_level_key]
        levels = {
            "debug",
            "info",
            "notice",
            "warning",
            "error",
            "critical",
            "alert",
            "emergency",
        }
        if not isinstance(log_level, str) or log_level not in levels:
            raise ToolInputError(
                f"params._meta.{log_level_key} has an invalid value"
            )
    _validate_progress_token(meta, "params._meta")
    if requested != MODERN_PROTOCOL_VERSION:
        raise UnsupportedProtocolVersion(requested)


def _validate_initialize_params(params: dict[str, Any]) -> None:
    allowed = {"protocolVersion", "capabilities", "clientInfo", "_meta"}
    _unknown_fields(params, allowed, "initialize params")
    _required_fields(
        params,
        ("protocolVersion", "capabilities", "clientInfo"),
        "initialize params",
    )
    if not isinstance(params["protocolVersion"], str):
        raise ToolInputError("protocolVersion must be a string")
    if not isinstance(params["capabilities"], dict):
        raise ToolInputError("capabilities must be an object")
    if "_meta" in params:
        meta = params["_meta"]
        if not isinstance(meta, dict):
            raise ToolInputError("initialize params._meta must be an object")
        _validate_progress_token(meta, "initialize params._meta")
    _validate_implementation(params["clientInfo"], "clientInfo")


def _validate_method_params(method: str, params: dict[str, Any]) -> None:
    if "_meta" in params:
        meta = params["_meta"]
        if not isinstance(meta, dict):
            raise ToolInputError("params._meta must be an object")
        _validate_progress_token(meta, "params._meta")
    if method == "server/discover":
        _unknown_fields(params, {"_meta"}, "server/discover params")
        return
    if method == "ping":
        _unknown_fields(params, {"_meta"}, "ping params")
        return
    if method == "tools/list":
        _unknown_fields(params, {"_meta", "cursor"}, "tools/list params")
        if "cursor" in params and not isinstance(params["cursor"], str):
            raise ToolInputError("cursor must be a string")
        return
    if method == "tools/call":
        allowed = {
            "_meta",
            "name",
            "arguments",
            "inputResponses",
            "requestState",
        }
        _unknown_fields(params, allowed, "tools/call params")
        _required_fields(params, ("name",), "tools/call params")
        if not isinstance(params["name"], str):
            raise ToolInputError("tool name must be a string")
        if (
            "inputResponses" in params
            and not isinstance(params["inputResponses"], dict)
        ):
            raise ToolInputError("inputResponses must be an object")
        if (
            "requestState" in params
            and not isinstance(params["requestState"], str)
        ):
            raise ToolInputError("requestState must be a string")


def _uses_modern_protocol(
    method: str, params: dict[str, Any]
) -> bool:
    if method == "server/discover":
        return True
    meta = params.get("_meta")
    return (
        isinstance(meta, dict)
        and bool(MODERN_META_KEYS.intersection(meta))
    )


def _complete_result(
    result: dict[str, Any], modern: bool
) -> dict[str, Any]:
    if not modern:
        return result
    complete = {"resultType": "complete", **result}
    complete["_meta"] = dict(SERVER_RESULT_META)
    return complete


def _request_id_is_valid(value: Any) -> bool:
    return (
        not isinstance(value, bool)
        and isinstance(value, (str, int))
    )


def handle_message(message: Any) -> dict[str, Any] | None:
    if not isinstance(message, dict):
        return _rpc_error(None, -32600, "Invalid Request")
    if message.get("jsonrpc") != "2.0":
        return _rpc_error(None, -32600, "Invalid Request")

    method = message.get("method")
    if not isinstance(method, str):
        return _rpc_error(None, -32600, "Invalid Request")

    has_id = "id" in message
    if has_id and not _request_id_is_valid(message["id"]):
        return _rpc_error(None, -32600, "Invalid Request")
    request_id = message.get("id")
    is_notification = not has_id

    if "params" in message:
        params = message["params"]
        if not isinstance(params, dict):
            if is_notification:
                return None
            return _rpc_error(request_id, -32602, "Invalid params")
    else:
        params = {}

    if is_notification:
        return None

    try:
        modern = (
            method != "initialize"
            and _uses_modern_protocol(method, params)
        )
        if modern:
            _validate_modern_meta(params)
            if method == "ping":
                return _rpc_error(
                    request_id,
                    -32601,
                    "Method not found: ping",
                )

        if method == "initialize":
            _validate_initialize_params(params)
            result = {
                "protocolVersion": _negotiate_protocol_version(
                    params["protocolVersion"]
                ),
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
                "instructions": INSTRUCTIONS,
            }
            return _rpc_result(request_id, result)

        _validate_method_params(method, params)

        if method == "server/discover":
            result = {
                "resultType": "complete",
                "supportedVersions": list(SUPPORTED_PROTOCOL_VERSIONS),
                "capabilities": {"tools": {}},
                "_meta": dict(SERVER_RESULT_META),
                "instructions": INSTRUCTIONS,
                "ttlMs": CACHE_TTL_MS,
                "cacheScope": CACHE_SCOPE,
            }
            return _rpc_result(request_id, result)
        if method == "ping":
            return _rpc_result(request_id, {})
        if method == "tools/list":
            result = {"tools": TOOLS}
            if modern:
                result["ttlMs"] = CACHE_TTL_MS
                result["cacheScope"] = CACHE_SCOPE
            return _rpc_result(
                request_id,
                _complete_result(result, modern),
            )
        if method == "tools/call":
            name = params["name"]
            try:
                summary = execute_tool(name, params.get("arguments"))
            except ToolInputError:
                raise
            except Exception as error:
                result = _tool_failure(error)
            else:
                result = _tool_result(summary)
            return _rpc_result(
                request_id,
                _complete_result(result, modern),
            )
    except UnsupportedProtocolVersion as error:
        return _rpc_error(
            request_id,
            -32022,
            "Unsupported protocol version",
            {
                "supported": list(SUPPORTED_PROTOCOL_VERSIONS),
                "requested": error.requested,
            },
        )
    except ToolInputError as error:
        return _invalid_params(request_id, error)

    return _rpc_error(request_id, -32601, f"Method not found: {method}")


def _write_message(stream: Any, message: dict[str, Any]) -> None:
    data = json.dumps(
        message, ensure_ascii=True, separators=(",", ":")
    ).encode("utf-8")
    stream.write(data + b"\n")
    stream.flush()


def _reject_json_constant(value: str) -> None:
    raise ValueError(f"non-JSON numeric constant: {value}")


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
        if line.isspace():
            continue
        try:
            message = json.loads(
                line,
                parse_constant=_reject_json_constant,
            )
        except (
            UnicodeDecodeError,
            json.JSONDecodeError,
            RecursionError,
            ValueError,
        ):
            _write_message(target, _rpc_error(None, -32700, "Parse error"))
            continue
        response = handle_message(message)
        if response is not None:
            _write_message(target, response)


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if not args:
        serve()
        return 0
    if args == ["--version"]:
        print(SERVER_VERSION)
        return 0
    if args[0] in {"install", "uninstall", "status", "doctor"}:
        from .installer import main as installer_main

        return installer_main(args)
    print(
        "usage: safe-shell-mcp [--version] | "
        "safe-shell-mcp {install,uninstall,status,doctor} [options]",
        file=sys.stderr,
    )
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
