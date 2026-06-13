"""Protocol contract tests for safe-shell.

These tests verify protocol stability and do not test quoting logic.
"""

import base64
import json
import tempfile
import unittest
from pathlib import Path

from .conftest import run_safe_shell, run_safe_shell_raw, run_safe_shell_bytes, run_safe_shell_cli, write_request_file


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
        result = run_safe_shell({"shell": "pwsh", "text": "foo"})
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

    def test_all_supported_shells(self):
        """All supported shells work."""
        for shell in ["bash", "zsh", "fish", "powershell", "cmd", "msys2"]:
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


class TestCLIContract(unittest.TestCase):
    """Tests for main() CLI entry point behavior."""

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

    def test_multiple_at_file_uses_first(self):
        """Multiple @file args uses first one and prints warning to stderr."""
        path1 = write_request_file({"shell": "bash", "text": "first"})
        path2 = write_request_file({"shell": "bash", "text": "second"})
        try:
            proc = run_safe_shell_cli([f"@{path1}", f"@{path2}"])
            assert proc.returncode == 0
            response = json.loads(proc.stdout.decode("utf-8"))
            assert response["ok"] is True
            assert response["quoted"] == "'first'"
            assert b"Warning" in proc.stderr
        finally:
            Path(path1).unlink(missing_ok=True)
            Path(path2).unlink(missing_ok=True)
