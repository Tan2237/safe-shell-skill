"""MCP transport and dual-era protocol tests."""

import importlib
import importlib.util
import io
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
implementation = importlib.import_module("safe_shell_mcp.server")


def _legacy_initialize(protocol_version="2025-11-25"):
    return {
        "protocolVersion": protocol_version,
        "capabilities": {},
        "clientInfo": {"name": "test-client", "version": "1.0"},
    }


def _modern_meta(protocol_version="2026-07-28"):
    return {
        "io.modelcontextprotocol/protocolVersion": protocol_version,
        "io.modelcontextprotocol/clientCapabilities": {},
        "io.modelcontextprotocol/clientInfo": {
            "name": "test-client",
            "version": "1.0",
        },
    }


def _request(method, params=None, request_id=1):
    message = {"jsonrpc": "2.0", "id": request_id, "method": method}
    if params is not None:
        message["params"] = params
    return message


def _call(name, arguments, modern=False):
    params = {"name": name, "arguments": arguments}
    if modern:
        params["_meta"] = _modern_meta()
    return server.handle_message(_request("tools/call", params))


class SafeShellMcpToolTests(unittest.TestCase):
    def test_tools_list_exposes_both_tools_and_output_schemas(self):
        response = server.handle_message(_request("tools/list"))
        tools = response["result"]["tools"]
        self.assertEqual(
            [tool["name"] for tool in tools],
            ["safe_shell_quote", "safe_shell_quote_many"],
        )
        for tool in tools:
            self.assertTrue(tool["annotations"]["readOnlyHint"])
            self.assertIn("outputSchema", tool)

        many_input = tools[1]["inputSchema"]["properties"]["texts"]
        self.assertEqual(many_input["minItems"], 1)
        self.assertEqual(many_input["maxItems"], server.core.MAX_BATCH_ITEMS)
        many_success = tools[1]["outputSchema"]["oneOf"][0]
        self.assertEqual(many_success["properties"]["count"]["minimum"], 1)
        self.assertEqual(
            many_success["properties"]["count"]["maximum"],
            server.core.MAX_BATCH_ITEMS,
        )
        self.assertEqual(
            many_success["properties"]["quoted"]["minItems"], 1
        )
        self.assertEqual(
            many_success["properties"]["quoted"]["maxItems"],
            server.core.MAX_BATCH_ITEMS,
        )

    def test_many_failure_schema_keeps_index_optional(self):
        tools = server.handle_message(_request("tools/list"))["result"][
            "tools"
        ]
        failure = tools[1]["outputSchema"]["oneOf"][1]
        self.assertIn("index", failure["properties"])
        self.assertNotIn("index", failure["required"])

    def test_raw_structured_requests_need_no_json_or_base64(self):
        one = server.execute_tool(
            "safe_shell_quote",
            {"shell": "bash", "text": "foo'bar $HOME"},
        )
        many = server.execute_tool(
            "safe_shell_quote_many",
            {"shell": "powershell", "texts": ["a'b", "$env:HOME"]},
        )
        self.assertEqual(one["quoted"], "'foo'\\''bar $HOME'")
        self.assertEqual(many["quoted"], ["'a''b'", "'$env:HOME'"])
        self.assertEqual(many["count"], 2)
        self.assertEqual(one["transport"], "mcp-structured")

    def test_tool_schema_tracks_all_core_shell_profiles(self):
        tool = server.handle_message(_request("tools/list"))["result"][
            "tools"
        ][0]
        declared = set(
            tool["inputSchema"]["properties"]["shell"]["enum"]
        )
        self.assertEqual(declared, server.core.SUPPORTED_SHELLS)
        for shell in ("sh", "dash", "ksh", "pwsh"):
            with self.subTest(shell=shell):
                result = server.execute_tool(
                    "safe_shell_quote",
                    {"shell": shell, "text": "x"},
                )
                self.assertTrue(result["ok"])

    def test_batch_warning_shape_is_declared_by_output_schema(self):
        result = _call(
            "safe_shell_quote_many",
            {"shell": "msys2", "texts": ["/tmp/one", "plain"]},
        )["result"]["structuredContent"]
        self.assertEqual(result["warnings"][0]["index"], 0)
        tools = server.handle_message(_request("tools/list"))["result"][
            "tools"
        ]
        success_schema = tools[1]["outputSchema"]["oneOf"][0]
        warning_schema = success_schema["properties"]["warnings"][
            "items"
        ]
        self.assertIn("index", warning_schema["required"])

    def test_success_and_failure_text_content_is_compact_json(self):
        success = _call(
            "safe_shell_quote", {"shell": "bash", "text": "hello"}
        )["result"]
        failure = _call(
            "safe_shell_quote", {"shell": "cmd", "text": "%PATH%"}
        )["result"]
        for result in (success, failure):
            expected = json.dumps(
                result["structuredContent"],
                ensure_ascii=False,
                separators=(",", ":"),
            )
            self.assertEqual(result["content"][0]["text"], expected)
            self.assertEqual(
                json.loads(result["content"][0]["text"]),
                result["structuredContent"],
            )
        self.assertNotIn("isError", success)
        self.assertTrue(failure["isError"])

    def test_schema_malformed_calls_return_invalid_params(self):
        cases = [
            ("unknown tool", "nope", {}),
            ("arguments not object", "safe_shell_quote", []),
            ("arguments null", "safe_shell_quote", None),
            ("missing field", "safe_shell_quote", {"shell": "bash"}),
            (
                "wrong field type",
                "safe_shell_quote",
                {"shell": "bash", "text": 1},
            ),
            (
                "invalid enum",
                "safe_shell_quote",
                {"shell": "nushell", "text": "x"},
            ),
            (
                "extra field",
                "safe_shell_quote",
                {"shell": "bash", "text": "x", "extra": True},
            ),
            (
                "many not array",
                "safe_shell_quote_many",
                {"shell": "bash", "texts": "x"},
            ),
            (
                "many bad item",
                "safe_shell_quote_many",
                {"shell": "bash", "texts": ["x", 2]},
            ),
            (
                "many empty",
                "safe_shell_quote_many",
                {"shell": "bash", "texts": []},
            ),
            (
                "many too long",
                "safe_shell_quote_many",
                {
                    "shell": "bash",
                    "texts": ["x"] * (server.core.MAX_BATCH_ITEMS + 1),
                },
            ),
        ]
        for label, name, arguments in cases:
            with self.subTest(label=label):
                response = _call(name, arguments)
                self.assertEqual(response["error"]["code"], -32602)
                self.assertNotIn("result", response)

    def test_tools_call_params_schema_is_validated(self):
        cases = [
            {},
            {"name": 7, "arguments": {}},
            {
                "name": "safe_shell_quote",
                "arguments": {"shell": "bash", "text": "x"},
                "extra": True,
            },
        ]
        for params in cases:
            with self.subTest(params=params):
                response = server.handle_message(
                    _request("tools/call", params)
                )
                self.assertEqual(response["error"]["code"], -32602)

    def test_runtime_value_errors_remain_tool_results(self):
        cases = [
            {"shell": "bash", "text": "a\x00b"},
            {"shell": "cmd", "text": "%PATH%"},
            {
                "shell": "bash",
                "text": "x" * (server.core.MAX_INPUT_SIZE + 1),
            },
        ]
        for arguments in cases:
            with self.subTest(arguments=list(arguments)):
                response = _call("safe_shell_quote", arguments)
                result = response["result"]
                self.assertTrue(result["isError"])
                self.assertFalse(result["structuredContent"]["ok"])

    def test_quote_many_success_and_item_failure_index(self):
        success = _call(
            "safe_shell_quote_many",
            {"shell": "bash", "texts": ["one", "two words"]},
        )["result"]["structuredContent"]
        self.assertEqual(success["count"], 2)
        self.assertEqual(success["quoted"], ["'one'", "'two words'"])

        failure = _call(
            "safe_shell_quote_many",
            {"shell": "cmd", "texts": ["safe", "%bad"]},
        )["result"]["structuredContent"]
        self.assertFalse(failure["ok"])
        self.assertEqual(failure["index"], 1)
        self.assertNotIn("quoted", failure)

    def test_unexpected_core_error_maps_to_internal_tool_error(self):
        with patch.object(
            server.core,
            "process_request",
            side_effect=RuntimeError("secret boom"),
        ):
            result = _call(
                "safe_shell_quote",
                {"shell": "bash", "text": "x"},
            )["result"]
        self.assertTrue(result["isError"])
        self.assertEqual(
            result["structuredContent"]["failureClass"],
            "INTERNAL_ERROR",
        )
        self.assertNotIn("secret boom", result["content"][0]["text"])

    def test_hot_path_skips_json_decode(self):
        with patch.object(
            server.core.json,
            "loads",
            side_effect=AssertionError("unexpected JSON decode"),
        ):
            one = server.execute_tool(
                "safe_shell_quote", {"shell": "bash", "text": "one"}
            )
            many = server.execute_tool(
                "safe_shell_quote_many",
                {"shell": "bash", "texts": ["two", "three"]},
            )
        self.assertEqual(one["quoted"], "'one'")
        self.assertEqual(many["quoted"], ["'two'", "'three'"])


class SafeShellMcpProtocolTests(unittest.TestCase):
    def test_json_rpc_envelope_validation(self):
        cases = [
            ([], -32600),
            ({"method": "ping", "id": 1}, -32600),
            (
                {"jsonrpc": "1.0", "method": "ping", "id": 1},
                -32600,
            ),
            ({"jsonrpc": "2.0", "id": 1}, -32600),
            (
                {"jsonrpc": "2.0", "method": 1, "id": 1},
                -32600,
            ),
            (
                {
                    "jsonrpc": "2.0",
                    "method": "ping",
                    "id": None,
                },
                -32600,
            ),
            (
                {"jsonrpc": "2.0", "method": "ping", "id": True},
                -32600,
            ),
            (
                {"jsonrpc": "2.0", "method": "ping", "id": 1.0},
                -32600,
            ),
            (
                {
                    "jsonrpc": "2.0",
                    "method": "ping",
                    "id": 1,
                    "params": None,
                },
                -32602,
            ),
            (
                {
                    "jsonrpc": "2.0",
                    "method": "ping",
                    "id": 1,
                    "params": [],
                },
                -32602,
            ),
        ]
        for message, code in cases:
            with self.subTest(message=message):
                response = server.handle_message(message)
                self.assertEqual(response["error"]["code"], code)

    def test_unknown_request_method_and_all_notifications(self):
        unknown = server.handle_message(_request("not/a/method"))
        self.assertEqual(unknown["error"]["code"], -32601)

        notifications = [
            {"jsonrpc": "2.0", "method": "not/a/method"},
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            {
                "jsonrpc": "2.0",
                "method": "notifications/cancelled",
                "params": [],
            },
        ]
        for message in notifications:
            with self.subTest(message=message):
                self.assertIsNone(server.handle_message(message))

    def test_initialize_is_strict_and_negotiates_legacy_versions(self):
        supported = server.handle_message(
            _request("initialize", _legacy_initialize("2024-11-05"))
        )
        unsupported = server.handle_message(
            _request("initialize", _legacy_initialize("2099-01-01"), 2)
        )
        self.assertEqual(
            supported["result"]["protocolVersion"], "2024-11-05"
        )
        self.assertEqual(
            unsupported["result"]["protocolVersion"], "2025-11-25"
        )
        self.assertNotIn("resultType", supported["result"])

        with_meta_params = _legacy_initialize()
        with_meta_params["_meta"] = {"progressToken": "init-progress"}
        with_meta = server.handle_message(
            _request("initialize", with_meta_params, 3)
        )
        self.assertEqual(
            with_meta["result"]["protocolVersion"], "2025-11-25"
        )

        for token in ([], {}, True):
            invalid_token_params = _legacy_initialize()
            invalid_token_params["_meta"] = {"progressToken": token}
            invalid_token = server.handle_message(
                _request("initialize", invalid_token_params, 4)
            )
            self.assertEqual(invalid_token["error"]["code"], -32602)

        invalid = server.handle_message(
            _request(
                "initialize",
                {"protocolVersion": "2025-11-25"},
                4,
            )
        )
        self.assertEqual(invalid["error"]["code"], -32602)

        invalid_meta_params = _legacy_initialize()
        invalid_meta_params["_meta"] = []
        invalid_meta = server.handle_message(
            _request("initialize", invalid_meta_params, 5)
        )
        self.assertEqual(invalid_meta["error"]["code"], -32602)

    def test_discover_advertises_dual_era_server(self):
        params = {"_meta": _modern_meta()}
        result = server.handle_message(
            _request("server/discover", params)
        )["result"]
        self.assertEqual(result["resultType"], "complete")
        self.assertEqual(result["supportedVersions"][0], "2026-07-28")
        self.assertIn("2025-11-25", result["supportedVersions"])
        self.assertEqual(result["capabilities"], {"tools": {}})
        self.assertEqual(result["ttlMs"], 3_600_000)
        self.assertEqual(result["cacheScope"], "public")
        server_info = result["_meta"][
            "io.modelcontextprotocol/serverInfo"
        ]
        self.assertEqual(server_info["name"], "safe-shell")

    def test_discover_and_modern_meta_validation(self):
        cases = [
            _request("server/discover", {}),
            _request(
                "server/discover",
                {"_meta": {"io.modelcontextprotocol/protocolVersion":
                           "2026-07-28"}},
            ),
            _request(
                "tools/list",
                {
                    "_meta": {
                        "io.modelcontextprotocol/protocolVersion":
                            "2026-07-28",
                        "io.modelcontextprotocol/clientCapabilities": [],
                    }
                },
            ),
            _request(
                "server/discover",
                {"_meta": _modern_meta(), "extra": True},
            ),
            _request("tools/list", {"_meta": []}),
        ]
        for message in cases:
            with self.subTest(message=message):
                response = server.handle_message(message)
                self.assertEqual(response["error"]["code"], -32602)

    def test_modern_progress_token_accepts_json_numbers(self):
        meta = _modern_meta()
        meta["progressToken"] = 1.5
        result = server.handle_message(
            _request("tools/list", {"_meta": meta})
        )["result"]
        self.assertEqual(result["resultType"], "complete")

    def test_unsupported_modern_protocol_has_standard_error(self):
        response = server.handle_message(
            _request(
                "tools/list",
                {"_meta": _modern_meta("2099-01-01")},
            )
        )
        self.assertEqual(response["error"]["code"], -32022)
        self.assertEqual(
            response["error"]["data"]["requested"], "2099-01-01"
        )
        self.assertIn(
            "2026-07-28",
            response["error"]["data"]["supported"],
        )

    def test_modern_results_have_result_type_server_meta_and_cache(self):
        list_result = server.handle_message(
            _request("tools/list", {"_meta": _modern_meta()})
        )["result"]
        call_result = _call(
            "safe_shell_quote",
            {"shell": "bash", "text": "x"},
            modern=True,
        )["result"]
        for result in (list_result, call_result):
            self.assertEqual(result["resultType"], "complete")
            self.assertIn(
                "io.modelcontextprotocol/serverInfo",
                result["_meta"],
            )
        self.assertEqual(list_result["cacheScope"], "public")
        self.assertIn("ttlMs", list_result)

    def test_modern_ping_was_removed_before_method_param_validation(self):
        response = server.handle_message(
            _request(
                "ping",
                {"_meta": _modern_meta(), "extra": "ignored"},
                2,
            )
        )
        self.assertEqual(response["error"]["code"], -32601)

    def test_legacy_meta_validates_progress_token(self):
        for token in ([], {}, True):
            response = server.handle_message(
                _request(
                    "tools/list",
                    {"_meta": {"progressToken": token}},
                )
            )
            self.assertEqual(response["error"]["code"], -32602)

    def test_legacy_results_remain_legacy_shaped(self):
        list_result = server.handle_message(
            _request(
                "tools/list",
                {"_meta": {"progressToken": "legacy-progress"}},
            )
        )["result"]
        ping_result = server.handle_message(_request("ping", {}, 2))[
            "result"
        ]
        self.assertNotIn("resultType", list_result)
        self.assertNotIn("_meta", list_result)
        self.assertNotIn("ttlMs", list_result)
        self.assertEqual(ping_result, {})


class SafeShellMcpStdioTests(unittest.TestCase):
    def test_stdio_dual_era_discovery_tools_and_ping_compatibility(self):
        messages = [
            _request("initialize", _legacy_initialize(), 1),
            {
                "jsonrpc": "2.0",
                "method": "notifications/initialized",
            },
            _request("tools/list", {}, 2),
            _request(
                "server/discover",
                {"_meta": _modern_meta()},
                3,
            ),
            _request(
                "tools/list",
                {"_meta": _modern_meta()},
                4,
            ),
            _request("ping", {"_meta": _modern_meta()}, 5),
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
            if line.isspace() is False
        ]
        self.assertEqual(
            [item["id"] for item in responses],
            [1, 2, 3, 4, 5],
        )
        self.assertEqual(
            len(responses[1]["result"]["tools"]),
            2,
        )
        self.assertEqual(
            responses[2]["result"]["resultType"],
            "complete",
        )
        self.assertEqual(
            responses[3]["result"]["cacheScope"],
            "public",
        )
        self.assertEqual(responses[4]["error"]["code"], -32601)

    def test_parse_recursion_error_does_not_stop_service(self):
        ping = _request("ping", {}, 2)
        source = io.BytesIO(b"too-deep\n" + json.dumps(ping).encode() + b"\n")
        target = io.BytesIO()
        with patch.object(
            implementation.json,
            "loads",
            side_effect=[RecursionError("deep"), ping],
        ):
            server.serve(source, target)
        responses = [
            json.loads(line)
            for line in target.getvalue().splitlines()
        ]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["id"], 2)

    def test_non_json_numbers_and_surrogates_do_not_stop_service(self):
        invalid_number = (
            b'{"jsonrpc":"2.0","id":NaN,"method":"ping"}\n'
        )
        surrogate_tool = (
            b'{"jsonrpc":"2.0","id":2,"method":"tools/call",'
            b'"params":{"name":"\\ud800","arguments":{}}}\n'
        )
        ping = json.dumps(_request("ping", {}, 3)).encode() + b"\n"
        target = io.BytesIO()
        server.serve(
            io.BytesIO(invalid_number + surrogate_tool + ping),
            target,
        )
        responses = [
            json.loads(line)
            for line in target.getvalue().splitlines()
        ]
        self.assertEqual(responses[0]["error"]["code"], -32700)
        self.assertEqual(responses[1]["error"]["code"], -32602)
        self.assertEqual(responses[2]["id"], 3)

    def test_whitespace_check_does_not_copy_with_strip(self):
        class NoStripBytes(bytes):
            def strip(self, *args, **kwargs):
                raise AssertionError("strip must not be called")

        class Source:
            def __init__(self):
                self.lines = [NoStripBytes(b" \t\r\n"), b""]

            def readline(self, limit):
                return self.lines.pop(0)

        target = io.BytesIO()
        server.serve(Source(), target)
        self.assertEqual(target.getvalue(), b"")

    def test_repository_launcher_version_and_cli_delegation(self):
        completed = subprocess.run(
            [sys.executable, str(SERVER_PATH), "--version"],
            text=True,
            capture_output=True,
            encoding="utf-8",
            check=True,
        )
        self.assertEqual(completed.stdout.strip(), "1.0.1")

        from safe_shell_mcp import installer

        for command in ("install", "uninstall", "status", "doctor"):
            args = [command, "--json"]
            with self.subTest(command=command), patch.object(
                installer,
                "main",
                return_value=7,
            ) as installer_main:
                self.assertEqual(server.main(args), 7)
                installer_main.assert_called_once_with(args)


if __name__ == "__main__":
    unittest.main()
