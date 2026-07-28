"""MCP transport tests."""

import importlib.util
import json
import subprocess
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


REPO_ROOT = Path(__file__).resolve().parents[1]
SERVER_PATH = REPO_ROOT / "mcp" / "safe_shell_server.py"


def _load_server():
    spec = importlib.util.spec_from_file_location(
        "safe_shell_mcp_server_test", SERVER_PATH
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load MCP server")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


server = _load_server()


class SafeShellMcpTests(unittest.TestCase):
    def test_tools_list_exposes_single_argument_fast_path(self):
        response = server.handle_message(
            {"jsonrpc": "2.0", "id": 1, "method": "tools/list"}
        )
        tools = response["result"]["tools"]
        self.assertEqual([tool["name"] for tool in tools], ["safe_shell_quote"])
        self.assertTrue(tools[0]["annotations"]["readOnlyHint"])

    def test_raw_structured_request_needs_no_json_or_base64(self):
        result = server.execute_tool(
            "safe_shell_quote",
            {"shell": "bash", "text": "foo'bar $HOME"},
        )
        self.assertTrue(result["ok"])
        self.assertEqual(result["quoted"], "'foo'\\''bar $HOME'")
        self.assertEqual(result["transport"], "mcp-structured")

    def test_validation_failure_is_an_mcp_tool_error(self):
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "safe_shell_quote",
                    "arguments": {"shell": "cmd", "text": "%PATH%"},
                },
            }
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["failureClass"],
            "UNQUOTABLE_CHARACTER",
        )

    def test_missing_arguments_match_cli_failure_class(self):
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "safe_shell_quote"},
            }
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["failureClass"],
            "MISSING_REQUIRED_FIELD",
        )

    def test_unknown_argument_field_is_invalid_tool_argument(self):
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {
                    "name": "safe_shell_quote",
                    "arguments": {"shell": "bash", "text": "x", "extra": 1},
                },
            }
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["failureClass"],
            "INVALID_TOOL_ARGUMENT",
        )
        self.assertEqual(
            result["structuredContent"]["tool"], "safe_shell_quote"
        )

    def test_unknown_tool_reports_tool_name(self):
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "nope", "arguments": {}},
            }
        )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["failureClass"],
            "INVALID_TOOL_ARGUMENT",
        )
        self.assertEqual(result["structuredContent"]["tool"], "nope")

    def test_unexpected_error_maps_to_internal_error(self):
        with patch.object(
            server.core,
            "process_request",
            side_effect=RuntimeError("boom"),
        ):
            response = server.handle_message(
                {
                    "jsonrpc": "2.0",
                    "id": 1,
                    "method": "tools/call",
                    "params": {
                        "name": "safe_shell_quote",
                        "arguments": {"shell": "bash", "text": "x"},
                    },
                }
            )
        result = response["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["failureClass"], "INTERNAL_ERROR"
        )
        self.assertNotIn("boom", result["structuredContent"]["message"])

    def test_hot_path_skips_json_decode(self):
        with patch.object(
            server.core.json,
            "loads",
            side_effect=AssertionError("unexpected JSON decode"),
        ):
            first = server.execute_tool(
                "safe_shell_quote", {"shell": "bash", "text": "one"}
            )
            second = server.execute_tool(
                "safe_shell_quote", {"shell": "bash", "text": "two"}
            )
        self.assertEqual(first["quoted"], "'one'")
        self.assertEqual(second["quoted"], "'two'")

    def test_stdio_initialize_discovery_and_ping(self):
        messages = [
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2025-11-25"},
            },
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        completed = subprocess.run(
            [sys.executable, str(SERVER_PATH)],
            input="\n".join(json.dumps(item) for item in messages) + "\n",
            text=True,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        responses = [
            json.loads(line)
            for line in completed.stdout.splitlines()
            if line.strip()
        ]
        self.assertEqual([item["id"] for item in responses], [1, 2, 3])
        self.assertEqual(
            responses[0]["result"]["serverInfo"]["name"], "safe-shell"
        )
        self.assertEqual(len(responses[1]["result"]["tools"]), 1)

    def test_initialize_negotiates_protocol_version(self):
        supported = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {"protocolVersion": "2024-11-05"},
            }
        )
        unsupported = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "initialize",
                "params": {"protocolVersion": "2099-01-01"},
            }
        )
        self.assertEqual(
            supported["result"]["protocolVersion"], "2024-11-05"
        )
        self.assertEqual(
            unsupported["result"]["protocolVersion"], "2025-11-25"
        )

    def test_non_object_params_return_json_rpc_error(self):
        response = server.handle_message(
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/list",
                "params": [],
            }
        )
        self.assertEqual(response["error"]["code"], -32602)

    def test_repository_launcher_reports_package_version(self):
        completed = subprocess.run(
            [sys.executable, str(SERVER_PATH), "--version"],
            text=True,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        self.assertEqual(completed.stdout.strip(), "1.0.0")


if __name__ == "__main__":
    unittest.main()
