"""Protocol contract tests for safe-shell.

These tests verify protocol stability and do not test quoting logic.
"""

import base64
import unittest

from .conftest import run_safe_shell, run_safe_shell_raw


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

    def test_all_supported_shells(self):
        """All supported shells work."""
        for shell in ["bash", "zsh", "fish", "powershell", "cmd", "msys2"]:
            result = run_safe_shell({"shell": shell, "text": "foo"})
            assert result["ok"] is True, f"shell {shell} failed"
            assert result["shell"] == shell
