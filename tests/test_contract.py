"""Protocol contract tests for safe-shell.

These tests verify protocol stability and do not test quoting logic.
"""

import base64
import io
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from .conftest import (
    core,
    run_safe_shell,
    run_safe_shell_bytes,
    run_safe_shell_cli,
    run_safe_shell_raw,
    write_request_file,
)


class TestProtocolContract(unittest.TestCase):
    """Tests for protocol stability."""

    def test_success_structure(self):
        """Success response has required fields."""
        result = run_safe_shell({"shell": "bash", "text": "foo"})
        assert result["ok"] is True
        assert "quoted" in result
        assert result["shell"] == "bash"

    def test_failure_structure(self):
        """Failure response has required fields."""
        result = run_safe_shell({"shell": "unknown", "text": "foo"})
        assert result["ok"] is False
        assert "failureClass" in result
        assert "message" in result

    def test_invalid_json(self):
        """INVALID_JSON for malformed JSON."""
        result = run_safe_shell_raw("{ invalid json")
        assert result["ok"] is False
        assert result["failureClass"] == "INVALID_JSON"

    def test_missing_required_field(self):
        """MISSING_REQUIRED_FIELD for missing fields."""
        # Missing shell
        result = run_safe_shell({"text": "foo"})
        assert result["failureClass"] == "MISSING_REQUIRED_FIELD"

        # Missing text
        result = run_safe_shell({"shell": "bash"})
        assert result["failureClass"] == "MISSING_REQUIRED_FIELD"

    def test_unsupported_shell(self):
        """UNSUPPORTED_SHELL for unknown shell."""
        result = run_safe_shell({"shell": "tcsh", "text": "foo"})
        assert result["failureClass"] == "UNSUPPORTED_SHELL"

    def test_unsupported_encoding(self):
        """UNSUPPORTED_ENCODING for unknown encoding."""
        result = run_safe_shell({"shell": "bash", "encoding": "hex", "text": "foo"})
        assert result["failureClass"] == "UNSUPPORTED_ENCODING"

    def test_invalid_encoding_data(self):
        """INVALID_ENCODING_DATA for invalid base64."""
        result = run_safe_shell({
            "shell": "bash",
            "encoding": "base64",
            "text": "!!!invalid!!!"
        })
        assert result["failureClass"] == "INVALID_ENCODING_DATA"

    def test_input_too_large(self):
        """INPUT_TOO_LARGE for input > 1 MiB."""
        large_text = "x" * (1024 * 1024 + 1)
        result = run_safe_shell({"shell": "bash", "text": large_text})
        assert result["failureClass"] == "INPUT_TOO_LARGE"

    def test_max_input_size_boundary(self):
        """Test input at exactly 1 MiB boundary."""
        # Exactly at limit should succeed
        at_limit = "x" * (1024 * 1024)
        result = run_safe_shell({"shell": "bash", "text": at_limit})
        assert result["ok"] is True

        # One byte over should fail
        over_limit = "x" * (1024 * 1024 + 1)
        result = run_safe_shell({"shell": "bash", "text": over_limit})
        assert result["ok"] is False
        assert result["failureClass"] == "INPUT_TOO_LARGE"

    def test_max_input_size_unicode_boundary(self):
        """Size limit uses UTF-8 bytes, not character count."""
        # CJK '你' = 3 bytes UTF-8. 350000 chars = 1050000 bytes > 1 MiB
        over_limit_cjk = "你" * 350000
        result = run_safe_shell({"shell": "bash", "text": over_limit_cjk})
        assert result["ok"] is False
        assert result["failureClass"] == "INPUT_TOO_LARGE"

    def test_max_input_size_unicode_within_limit(self):
        """Multi-byte text within byte limit succeeds."""
        # CJK '你' = 3 bytes UTF-8. 300000 chars = 900000 bytes < 1 MiB
        within_limit_cjk = "你" * 300000
        result = run_safe_shell({"shell": "bash", "text": within_limit_cjk})
        assert result["ok"] is True

    def test_max_input_size_applies_to_decoded(self):
        """MAX_INPUT_SIZE applies to decoded text, not base64 string length."""
        # Create base64 that decodes to > 1 MiB
        large_text = "x" * (1024 * 1024 + 1)
        encoded = base64.b64encode(large_text.encode()).decode()
        result = run_safe_shell({
            "shell": "bash",
            "encoding": "base64",
            "text": encoded
        })
        assert result["failureClass"] == "INPUT_TOO_LARGE"

    def test_nul_character_rejected(self):
        """UNQUOTABLE_CHARACTER for NUL character."""
        result = run_safe_shell({"shell": "bash", "text": "foo\x00bar"})
        assert result["failureClass"] == "UNQUOTABLE_CHARACTER"

    def test_unpaired_unicode_surrogate_rejected(self):
        raw = json.dumps({'shell': 'bash', 'text': chr(0xD800)})
        result = run_safe_shell_raw(raw)
        assert result['ok'] is False
        assert result['failureClass'] == 'UNQUOTABLE_CHARACTER'

    def test_invalid_field_type_text(self):
        """INVALID_FIELD_TYPE when text is not string."""
        result = run_safe_shell({"shell": "bash", "text": 123})
        assert result["failureClass"] == "INVALID_FIELD_TYPE"
        assert "text" in result["message"]

    def test_invalid_field_type_shell(self):
        """INVALID_FIELD_TYPE when shell is not string."""
        result = run_safe_shell({"shell": None, "text": "foo"})
        assert result["failureClass"] == "INVALID_FIELD_TYPE"
        assert "shell" in result["message"]

    def test_invalid_field_type_encoding(self):
        """INVALID_FIELD_TYPE when encoding is not string."""
        result = run_safe_shell({"shell": "bash", "text": "foo", "encoding": 0})
        assert result["failureClass"] == "INVALID_FIELD_TYPE"
        assert "encoding" in result["message"]

    def test_unknown_fields_are_rejected(self):
        result = run_safe_shell(
            {"shell": "bash", "text": "foo", "extra": True}
        )
        assert result["ok"] is False
        assert result["failureClass"] == "UNKNOWN_FIELD"
        assert "extra" in result["message"]

    def test_all_supported_shells(self):
        """All supported shells work."""
        shells = [
            "bash",
            "zsh",
            "fish",
            "sh",
            "dash",
            "ksh",
            "powershell",
            "pwsh",
            "cmd",
            "msys2",
        ]
        for shell in shells:
            result = run_safe_shell({"shell": shell, "text": "foo"})
            assert result["ok"] is True, f"shell {shell} failed"
            assert result["shell"] == shell

    def test_file_encoding_error(self):
        """INVALID_JSON for non-UTF-8 file content."""
        # Latin-1 encoded content that is not valid UTF-8
        result = run_safe_shell_bytes(b'\xff\xfe{"shell": "bash"}')
        assert result["ok"] is False
        assert result["failureClass"] == "INVALID_JSON"
        assert "encoding error" in result["message"]


class TestBatchProtocolContract(unittest.TestCase):
    """Tests for the atomic batch envelope."""

    def test_batch_success_structure(self):
        result = core.process_batch_request(
            {"shell": "bash", "texts": ["one", "a'b"]}
        )
        assert result == {
            "ok": True,
            "shell": "bash",
            "count": 2,
            "quoted": ["'one'", "'a'\\''b'"],
        }

    def test_batch_item_count_bounds(self):
        empty = run_safe_shell({"shell": "bash", "texts": []})
        assert empty["failureClass"] == "INVALID_FIELD_VALUE"

        at_limit = run_safe_shell(
            {"shell": "bash", "texts": ["x"] * 256}
        )
        assert at_limit["ok"] is True
        assert at_limit["count"] == 256

        too_many = run_safe_shell(
            {"shell": "bash", "texts": ["x"] * 257}
        )
        assert too_many["failureClass"] == "INVALID_FIELD_VALUE"

    def test_batch_rejects_wrong_types_and_extra_fields(self):
        wrong_container = run_safe_shell(
            {"shell": "bash", "texts": "not-an-array"}
        )
        assert wrong_container["failureClass"] == "INVALID_FIELD_TYPE"

        wrong_item = run_safe_shell(
            {"shell": "bash", "texts": ["ok", 3]}
        )
        assert wrong_item["failureClass"] == "INVALID_FIELD_TYPE"
        assert wrong_item["index"] == 1

        extra = run_safe_shell(
            {
                "shell": "bash",
                "texts": ["x"],
                "encoding": "base64",
            }
        )
        assert extra["failureClass"] == "UNKNOWN_FIELD"

        mixed = run_safe_shell(
            {"shell": "bash", "text": "one", "texts": ["two"]}
        )
        assert mixed["failureClass"] == "UNKNOWN_FIELD"

    def test_batch_aggregate_utf8_limit(self):
        half = 512 * 1024
        at_limit = run_safe_shell(
            {
                "shell": "bash",
                "texts": [
                    "x" * half,
                    ("你" * (half // 3)) + "yy",
                ],
            }
        )
        assert at_limit["ok"] is True

        over_limit = run_safe_shell(
            {
                "shell": "bash",
                "texts": ["x" * half, "y" * (half + 1)],
            }
        )
        assert over_limit["failureClass"] == "INPUT_TOO_LARGE"
        assert over_limit["index"] == 1
        assert "quoted" not in over_limit

    def test_batch_first_item_failure_has_index_and_no_partial_output(self):
        result = run_safe_shell(
            {"shell": "cmd", "texts": ["safe", "bad%value", "later"]}
        )
        assert result["failureClass"] == "UNQUOTABLE_CHARACTER"
        assert result["index"] == 1
        assert "quoted" not in result

        earliest = run_safe_shell(
            {"shell": "cmd", "texts": ["bad%value", 3]}
        )
        assert earliest["failureClass"] == "UNQUOTABLE_CHARACTER"
        assert earliest["index"] == 0
        assert "quoted" not in earliest

        nul = run_safe_shell(
            {"shell": "bash", "texts": ["safe", "bad\x00value"]}
        )
        assert nul["failureClass"] == "UNQUOTABLE_CHARACTER"
        assert nul["index"] == 1
        assert "quoted" not in nul

    def test_batch_powershell_legacy_native_failure_has_index(self):
        result = run_safe_shell(
            {
                "shell": "powershell",
                "texts": ["safe", "path with space\\", "later"],
            }
        )
        assert result["failureClass"] == "UNQUOTABLE_CHARACTER"
        assert result["index"] == 1
        assert "quoted" not in result

        pwsh = run_safe_shell(
            {
                "shell": "pwsh",
                "texts": ["", 'a"b', "path with space\\"],
            }
        )
        assert pwsh["ok"] is True
        assert pwsh["count"] == 3

    def test_batch_msys2_warnings_include_indexes(self):
        result = run_safe_shell(
            {
                "shell": "msys2",
                "texts": ["normal", "/tmp/a", "--mount=/work"],
            }
        )
        assert result["ok"] is True
        assert [warning["index"] for warning in result["warnings"]] == [
            1,
            2,
        ]


class TestCLIContract(unittest.TestCase):
    """Tests for main() CLI entry point behavior."""

    def test_emit_response_bypasses_legacy_text_encoding(self):
        response = {
            "ok": True,
            "quoted": "'emoji-🙂-中文'",
            "shell": "bash",
        }
        expected = (
            json.dumps(response, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        raw_stdout = io.BytesIO()
        legacy_stdout = io.TextIOWrapper(
            raw_stdout,
            encoding="cp1252",
        )

        with patch.object(sys, "stdout", legacy_stdout):
            assert core.emit_response(response) == 0

        assert raw_stdout.getvalue() == expected
        legacy_stdout.detach()

    def test_emit_response_supports_stringio(self):
        response = {
            "ok": False,
            "failureClass": "EXAMPLE",
            "message": "emoji-🙂-中文",
        }
        stream = io.StringIO()

        with patch.object(sys, "stdout", stream):
            assert core.emit_response(response) == 1

        assert stream.getvalue() == (
            json.dumps(response, ensure_ascii=False) + "\n"
        )

    def test_cli_stdout_is_utf8_without_pythonioencoding(self):
        request = {
            "shell": "bash",
            "text": "emoji-🙂-中文",
        }
        payload = json.dumps(
            request,
            ensure_ascii=False,
        ).encode("utf-8")
        proc = run_safe_shell_cli(["--request-stdin"], payload)

        assert proc.returncode == 0, proc.stderr
        response = core.process_envelope(request)
        expected = (
            json.dumps(response, ensure_ascii=False) + "\n"
        ).encode("utf-8")
        assert proc.stdout == expected

    def test_cli_escapes_unpaired_surrogate_in_error_response(self):
        request = {"shell": "invalid\ud800", "text": "x"}
        payload = json.dumps(request).encode("utf-8")
        proc = run_safe_shell_cli(["--request-stdin"], payload)

        assert proc.returncode == 1, proc.stderr
        assert b"\\ud800" in proc.stdout
        response = json.loads(proc.stdout.decode("utf-8"))
        assert response["failureClass"] == "UNSUPPORTED_SHELL"

    def test_cli_rejects_non_json_numbers_across_transports(self):
        constants = (
            ("NaN", float("nan")),
            ("Infinity", float("inf")),
            ("-Infinity", float("-inf")),
        )
        for constant, value in constants:
            request = {"shell": value, "text": "x"}
            payload = json.dumps(
                request,
                separators=(",", ":"),
            ).encode("utf-8")
            encoded = (
                base64.urlsafe_b64encode(payload).decode().rstrip("=")
            )
            path = write_request_file(request)
            try:
                transports = (
                    ("stdin", ["--request-stdin"], payload),
                    (
                        "base64",
                        ["--request-base64", encoded],
                        None,
                    ),
                    ("file", [f"@{path}"], None),
                )
                for transport, args, input_bytes in transports:
                    with self.subTest(
                        constant=constant,
                        transport=transport,
                    ):
                        proc = run_safe_shell_cli(args, input_bytes)
                        assert proc.returncode == 1, proc.stderr
                        response = json.loads(
                            proc.stdout.decode("utf-8")
                        )
                        assert response["failureClass"] == "INVALID_JSON"
                        assert constant in response["message"]
            finally:
                Path(path).unlink(missing_ok=True)

    def test_no_args_returns_1(self):
        """No arguments prints usage to stderr and returns 1."""
        proc = run_safe_shell_cli([])
        assert proc.returncode == 1
        assert b"Usage" in proc.stderr

    def test_no_at_file_returns_1(self):
        """Arguments without @ prefix prints usage and returns 1."""
        proc = run_safe_shell_cli(["foo.json"])
        assert proc.returncode == 1
        assert b"Usage" in proc.stderr

    def test_file_not_found(self):
        """Nonexistent file returns INVALID_JSON."""
        proc = run_safe_shell_cli(["@/nonexistent/path/file.json"])
        assert proc.returncode == 1
        response = json.loads(proc.stdout.decode("utf-8"))
        assert response["ok"] is False
        assert response["failureClass"] == "INVALID_JSON"
        assert "not found" in response["message"]

    def test_file_too_large(self):
        """File exceeding MAX_FILE_SIZE returns INPUT_TOO_LARGE."""
        # MAX_FILE_SIZE = 4 MiB. Write 4 MiB + 1 byte.
        large_path = None
        try:
            with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as f:
                f.write(b'"' + b"x" * (4 * 1024 * 1024 + 1) + b'"')
                large_path = f.name
            proc = run_safe_shell_cli([f"@{large_path}"])
            assert proc.returncode == 1
            response = json.loads(proc.stdout.decode("utf-8"))
            assert response["ok"] is False
            assert response["failureClass"] == "INPUT_TOO_LARGE"
        finally:
            if large_path:
                Path(large_path).unlink(missing_ok=True)

    def test_non_dict_json(self):
        """JSON array or string root returns INVALID_JSON."""
        # Array root
        result = run_safe_shell_raw("[1, 2, 3]")
        assert result["ok"] is False
        assert result["failureClass"] == "INVALID_JSON"

        # String root
        result = run_safe_shell_raw('"hello"')
        assert result["ok"] is False
        assert result["failureClass"] == "INVALID_JSON"

    def test_success_returns_0(self):
        """Successful request returns exit code 0."""
        path = write_request_file({"shell": "bash", "text": "hello"})
        try:
            proc = run_safe_shell_cli([f"@{path}"])
            assert proc.returncode == 0
            response = json.loads(proc.stdout.decode("utf-8"))
            assert response["ok"] is True
        finally:
            Path(path).unlink(missing_ok=True)

    def test_failure_returns_1(self):
        """Failed validation returns exit code 1."""
        path = write_request_file({"shell": "unknown", "text": "hello"})
        try:
            proc = run_safe_shell_cli([f"@{path}"])
            assert proc.returncode == 1
            response = json.loads(proc.stdout.decode("utf-8"))
            assert response["ok"] is False
        finally:
            Path(path).unlink(missing_ok=True)

    def test_cli_rejects_unknown_request_fields(self):
        path = write_request_file(
            {"shell": "bash", "text": "hello", "extra": True}
        )
        try:
            proc = run_safe_shell_cli([f"@{path}"])
            assert proc.returncode == 1
            response = json.loads(proc.stdout.decode("utf-8"))
            assert response["failureClass"] == "UNKNOWN_FIELD"
            assert "extra" in response["message"]
        finally:
            Path(path).unlink(missing_ok=True)

    def test_batch_dispatches_across_all_cli_transports(self):
        request = {"shell": "bash", "texts": ["one", "two"]}
        payload = json.dumps(request).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        path = write_request_file(request)
        try:
            cases = [
                (["--request-stdin"], payload),
                (["--request-base64", encoded], None),
                ([f"@{path}"], None),
            ]
            for args, input_bytes in cases:
                with self.subTest(args=args):
                    proc = run_safe_shell_cli(args, input_bytes)
                    assert proc.returncode == 0, proc.stderr
                    response = json.loads(
                        proc.stdout.decode("utf-8")
                    )
                    assert response["count"] == 2
                    assert response["quoted"] == ["'one'", "'two'"]
        finally:
            Path(path).unlink(missing_ok=True)

    def test_mixed_and_extra_transport_arguments_are_rejected(self):
        payload = json.dumps(
            {"shell": "bash", "text": "stdin"}
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode().rstrip("=")
        path1 = write_request_file(
            {"shell": "bash", "text": "first"}
        )
        path2 = write_request_file(
            {"shell": "bash", "text": "second"}
        )
        try:
            cases = [
                (["--request-stdin", "extra"], payload),
                (["--request-base64", encoded, "extra"], None),
                ([f"@{path1}", f"@{path2}"], None),
                ([f"@{path1}", "--request-stdin"], payload),
            ]
            for args, input_bytes in cases:
                with self.subTest(args=args):
                    proc = run_safe_shell_cli(args, input_bytes)
                    assert proc.returncode == 1
                    assert proc.stdout == b""
                    assert b"Usage" in proc.stderr
        finally:
            Path(path1).unlink(missing_ok=True)
            Path(path2).unlink(missing_ok=True)

    def test_deep_json_recursion_is_structured_invalid_json(self):
        payload = (
            b'{"shell":"bash","text":"x","extra":'
            + (b"[" * 10000)
            + b"0"
            + (b"]" * 10000)
            + b"}"
        )
        proc = run_safe_shell_cli(["--request-stdin"], payload)
        assert proc.returncode == 1
        response = json.loads(proc.stdout.decode("utf-8"))
        assert response["failureClass"] == "INVALID_JSON"
        assert b"Traceback" not in proc.stderr
