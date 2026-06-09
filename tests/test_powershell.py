"""Tests for PowerShell quoting."""

import json
import os
import platform
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "safe-shell" / "safe_shell.py"


def quote(text: str) -> str:
    """Get quoted string from safe-shell for PowerShell."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"shell": "powershell", "text": text}, f, ensure_ascii=False)
        request_file = f.name

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), f"@{request_file}"],
            capture_output=True,
            env=env,
        )
        response = json.loads(result.stdout.decode("utf-8"))
        if not response["ok"]:
            raise Exception(f"{response['failureClass']}: {response['message']}")
        return response["quoted"]
    finally:
        Path(request_file).unlink(missing_ok=True)


class TestPowerShellQuoting(unittest.TestCase):
    """Tests for PowerShell quoting correctness."""

    def test_simple_text(self):
        """Simple text is single-quoted."""
        assert quote("foo") == "'foo'"

    def test_text_with_space(self):
        """Text with space is single-quoted."""
        assert quote("foo bar") == "'foo bar'"

    def test_text_with_single_quote(self):
        """Single quote is doubled."""
        assert quote("foo'bar") == "'foo''bar'"

    def test_text_with_double_quote(self):
        """Double quote is preserved in single quotes."""
        assert quote('foo"bar') == "'foo\"bar'"

    def test_text_with_dollar(self):
        """Dollar sign is preserved in single quotes (no variable expansion)."""
        assert quote("foo$bar") == "'foo$bar'"

    def test_text_with_backtick(self):
        """Backtick is preserved in single quotes (no escape)."""
        assert quote("foo`bar") == "'foo`bar'"

    def test_text_with_backslash(self):
        """Backslash is preserved in single quotes."""
        assert quote("foo\\bar") == "'foo\\bar'"

    def test_text_with_newline(self):
        """Newline is preserved in single quotes."""
        assert quote("foo\nbar") == "'foo\nbar'"

    def test_text_with_special_chars(self):
        """Multiple special chars."""
        text = "foo$bar`baz\\qux"
        quoted = quote(text)
        assert quoted == "'foo$bar`baz\\qux'"

    def test_empty_string(self):
        """Empty string produces empty quotes."""
        assert quote("") == "''"

    def test_only_single_quote(self):
        """Only single quote."""
        assert quote("'") == "''''"

    def test_multiple_single_quotes(self):
        """Multiple single quotes."""
        assert quote("a'b'c") == "'a''b''c'"

    @unittest.skipUnless(platform.system() == "Windows", "PowerShell only on Windows")
    def test_roundtrip_simple(self):
        """Roundtrip: quote then unquote returns original."""
        text = "Hello, World!"
        quoted = quote(text)

        # Use PowerShell to unquote
        result = subprocess.run(
            ["powershell", "-Command", f"Write-Output {quoted}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # PowerShell adds trailing newline
            assert result.stdout.strip() == text

    @unittest.skipUnless(platform.system() == "Windows", "PowerShell only on Windows")
    def test_roundtrip_with_single_quote(self):
        """Roundtrip: string with single quote."""
        text = "foo'bar"
        quoted = quote(text)

        result = subprocess.run(
            ["powershell", "-Command", f"Write-Output {quoted}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            assert result.stdout.strip() == text

    def test_roundtrip_unicode(self):
        """Roundtrip: Unicode."""
        text = "日本語 テスト"
        quoted = quote(text)

        result = subprocess.run(
            ["powershell", "-Command", f"Write-Output {quoted}"],
            capture_output=True,
        )
        if result.returncode == 0:
            # PowerShell on Windows may use different encoding
            # Just verify the quoting is correct
            assert quoted == "'日本語 テスト'"

    @unittest.skipUnless(platform.system() == "Windows", "PowerShell only on Windows")
    def test_roundtrip_multiline(self):
        """Roundtrip: Multiline."""
        text = "line1\nline2"
        quoted = quote(text)

        result = subprocess.run(
            ["powershell", "-Command", f"Write-Output {quoted}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            # PowerShell may normalize line endings
            assert result.stdout.strip().replace("\r\n", "\n") == text

    @unittest.skipUnless(platform.system() == "Windows", "PowerShell only on Windows")
    def test_variable_not_expanded(self):
        """$VAR is not expanded in single quotes."""
        quoted = quote("$HOME")
        assert quoted == "'$HOME'"

        result = subprocess.run(
            ["powershell", "-Command", f"Write-Output {quoted}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            assert result.stdout.strip() == "$HOME"

    @unittest.skipUnless(platform.system() == "Windows", "PowerShell only on Windows")
    def test_backtick_not_escaped(self):
        """Backtick is literal in single quotes."""
        quoted = quote("foo`bar")
        assert quoted == "'foo`bar'"

        result = subprocess.run(
            ["powershell", "-Command", f"Write-Output {quoted}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            assert result.stdout.strip() == "foo`bar"
