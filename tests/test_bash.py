"""Tests for bash quoting."""

import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "safe-shell" / "safe_shell.py"


def quote(text: str, shell: str = "bash") -> str:
    """Get quoted string from safe-shell."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump({"shell": shell, "text": text}, f, ensure_ascii=False)
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


def unquote_bash(quoted: str) -> str:
    """Unquote a bash quoted string."""
    # Use bash to unquote: eval the string and echo it
    result = subprocess.run(
        ["bash", "-c", f"printf '%s' {quoted}"],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise Exception(f"Unquote failed: {result.stderr}")
    return result.stdout


class TestBashQuoting(unittest.TestCase):
    """Tests for bash quoting correctness."""

    def test_simple_text(self):
        """Simple text is single-quoted."""
        assert quote("foo") == "'foo'"

    def test_text_with_space(self):
        """Text with space is single-quoted."""
        assert quote("foo bar") == "'foo bar'"

    def test_text_with_single_quote(self):
        """Single quote is escaped."""
        assert quote("foo'bar") == "'foo'\\''bar'"

    def test_text_with_double_quote(self):
        """Double quote is preserved in single quotes."""
        assert quote('foo"bar') == "'foo\"bar'"

    def test_text_with_dollar(self):
        """Dollar sign is preserved in single quotes."""
        assert quote("foo$bar") == "'foo$bar'"

    def test_text_with_backtick(self):
        """Backtick is preserved in single quotes."""
        assert quote("foo`bar") == "'foo`bar'"

    def test_text_with_backslash(self):
        """Backslash is preserved in single quotes."""
        assert quote("foo\\bar") == "'foo\\bar'"

    def test_text_with_newline(self):
        """Newline is preserved in single quotes."""
        assert quote("foo\nbar") == "'foo\nbar'"

    def test_text_with_tab(self):
        """Tab is preserved in single quotes."""
        assert quote("foo\tbar") == "'foo\tbar'"

    @unittest.skipUnless(platform.system() != "Windows", "Bash roundtrip not reliable on Windows (Git Bash encoding)")
    def test_text_with_special_chars(self):
        """Multiple special chars."""
        text = "foo$bar`baz\\qux"
        quoted = quote(text)
        assert unquote_bash(quoted) == text

    def test_empty_string(self):
        """Empty string produces empty quotes."""
        assert quote("") == "''"

    def test_only_single_quote(self):
        """Only single quote."""
        assert quote("'") == "''\\'''"

    def test_multiple_single_quotes(self):
        """Multiple single quotes."""
        assert quote("a'b'c") == "'a'\\''b'\\''c'"

    @unittest.skipUnless(platform.system() != "Windows", "Bash roundtrip not reliable on Windows (Git Bash encoding)")
    def test_roundtrip_simple(self):
        """Roundtrip: quote then unquote returns original."""
        text = "Hello, World!"
        quoted = quote(text)
        assert unquote_bash(quoted) == text

    @unittest.skipUnless(platform.system() != "Windows", "Bash roundtrip not reliable on Windows (Git Bash encoding)")
    def test_roundtrip_complex(self):
        """Roundtrip: complex string."""
        text = "foo'bar \"baz\" $var `cmd` \\n"
        quoted = quote(text)
        assert unquote_bash(quoted) == text

    @unittest.skipUnless(platform.system() != "Windows", "Bash roundtrip not reliable on Windows (Git Bash encoding)")
    def test_roundtrip_unicode(self):
        """Roundtrip: Unicode."""
        text = "日本語 日本語"
        quoted = quote(text)
        # Use utf-8 encoding for subprocess on Windows
        result = subprocess.run(
            ["bash", "-c", f"printf '%s' {quoted}"],
            capture_output=True,
        )
        assert result.stdout.decode("utf-8") == text

    @unittest.skipUnless(platform.system() != "Windows", "Bash roundtrip not reliable on Windows (Git Bash encoding)")
    def test_roundtrip_emoji(self):
        """Roundtrip: Emoji."""
        text = "foo 😀 bar"
        quoted = quote(text)
        result = subprocess.run(
            ["bash", "-c", f"printf '%s' {quoted}"],
            capture_output=True,
        )
        assert result.stdout.decode("utf-8") == text

    @unittest.skipUnless(platform.system() != "Windows", "Bash roundtrip not reliable on Windows (Git Bash encoding)")
    def test_roundtrip_multiline(self):
        """Roundtrip: Multiline."""
        text = "line1\nline2\nline3"
        quoted = quote(text)
        assert unquote_bash(quoted) == text


class TestZshQuoting(unittest.TestCase):
    """Tests for zsh quoting (same algorithm as bash)."""

    @unittest.skipUnless(shutil.which("zsh"), "zsh not available")
    def test_simple_text(self):
        """Simple text is single-quoted."""
        assert quote("foo", "zsh") == "'foo'"

    def test_single_quote_escaped(self):
        """Single quote is escaped."""
        assert quote("foo'bar", "zsh") == "'foo'\\''bar'"

    @unittest.skipUnless(shutil.which("zsh"), "zsh not available")
    def test_roundtrip(self):
        """Roundtrip via zsh."""
        text = "foo$bar'baz"
        quoted = quote(text, "zsh")

        result = subprocess.run(
            ["zsh", "-c", f"printf '%s' {quoted}"],
            capture_output=True,
            text=True,
        )
        if result.returncode == 0:
            assert result.stdout == text


class TestFishQuoting(unittest.TestCase):
    """Tests for fish quoting (same algorithm as bash)."""

    def test_simple_text(self):
        """Simple text is single-quoted."""
        assert quote("foo", "fish") == "'foo'"

    def test_single_quote_escaped(self):
        """Single quote is escaped."""
        assert quote("foo'bar", "fish") == "'foo'\\''bar'"


class TestMsys2Quoting(unittest.TestCase):
    """Tests for msys2 quoting (same algorithm as bash, with warnings)."""

    def test_simple_text(self):
        """Simple text is single-quoted."""
        assert quote("foo", "msys2") == "'foo'"

    def test_single_quote_escaped(self):
        """Single quote is escaped."""
        assert quote("foo'bar", "msys2") == "'foo'\\''bar'"

    def test_path_warning(self):
        """Paths starting with / get MSYS2_PATH_CONVERSION warning."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"shell": "msys2", "text": "/foo/bar"}, f, ensure_ascii=False)
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
            assert response["ok"] is True
            assert "warnings" in response
            assert response["warnings"][0]["code"] == "MSYS2_PATH_CONVERSION"
        finally:
            Path(request_file).unlink(missing_ok=True)

    def test_double_slash_warning(self):
        """Paths starting with // get warning."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"shell": "msys2", "text": "//server/share"}, f, ensure_ascii=False)
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
            assert response["ok"] is True
            assert "warnings" in response
        finally:
            Path(request_file).unlink(missing_ok=True)

    def test_no_warning_for_normal_text(self):
        """No warning for text not starting with /."""
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
            json.dump({"shell": "msys2", "text": "foo/bar"}, f, ensure_ascii=False)
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
            assert response["ok"] is True
            assert "warnings" not in response
        finally:
            Path(request_file).unlink(missing_ok=True)
