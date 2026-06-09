"""Tests for CMD quoting."""

import platform
import unittest

from .conftest import quote, run_safe_shell


def unquote_cmd(quoted: str) -> str:
    """Unquote a CMD quoted string using CommandLineToArgvW directly.

    This is the authoritative test: if CommandLineToArgvW parses our
    quoted string back to the original, the quoting is correct.
    Only works on Windows.
    """
    if platform.system() != "Windows":
        raise Exception("unquote_cmd only works on Windows")

    import ctypes
    from ctypes import wintypes

    # CommandLineToArgvW parses a command line string
    shell32 = ctypes.windll.shell32
    CommandLineToArgvW = shell32.CommandLineToArgvW
    CommandLineToArgvW.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(ctypes.c_int)]
    CommandLineToArgvW.restype = ctypes.POINTER(wintypes.LPWSTR)

    LocalFree = ctypes.windll.kernel32.LocalFree
    LocalFree.argtypes = [wintypes.HLOCAL]
    LocalFree.restype = wintypes.HLOCAL

    # Build a fake command line: "python script.py <quoted>"
    # CommandLineToArgvW expects the full command line
    cmdline = f'python script.py {quoted}'

    argc = ctypes.c_int()
    argv = CommandLineToArgvW(cmdline, ctypes.byref(argc))

    if not argv or argc.value < 2:
        raise Exception("CommandLineToArgvW failed")

    try:
        # argv[0] is "python", argv[1] is "script.py", argv[2] is our argument
        if argc.value >= 3:
            return argv[2]
        else:
            raise Exception(f"Not enough arguments: {argc.value}")
    finally:
        LocalFree(argv)


class TestCmdQuoting(unittest.TestCase):
    """Tests for CMD quoting correctness.

    Note: CMD quoting is best-effort. Windows programs may implement
    their own argument parsing rules. These tests verify behavior
    with standard cmd.exe and CommandLineToArgvW convention.
    """

    def test_simple_text(self):
        """Simple text is double-quoted."""
        assert quote("foo", "cmd") == '"foo"'

    def test_text_with_space(self):
        """Text with space is double-quoted."""
        assert quote("foo bar", "cmd") == '"foo bar"'

    def test_text_with_double_quote(self):
        """Double quote is escaped with backslash."""
        assert quote('foo"bar', "cmd") == '"foo\\"bar"'

    def test_text_with_backslash(self):
        """Backslash is preserved."""
        assert quote("foo\\bar", "cmd") == '"foo\\bar"'

    def test_backslash_before_quote(self):
        """Backslash before quote is doubled."""
        assert quote('foo\\"bar', "cmd") == '"foo\\\\\\"bar"'

    def test_trailing_backslash(self):
        """Trailing backslash is doubled."""
        assert quote("foo\\", "cmd") == '"foo\\\\"'

    def test_text_with_dollar(self):
        """Dollar sign is preserved."""
        assert quote("foo$bar", "cmd") == '"foo$bar"'

    def test_text_with_percent(self):
        """Percent sign is preserved (no variable expansion in double quotes)."""
        assert quote("foo%bar%", "cmd") == '"foo%bar%"'

    def test_text_with_newline(self):
        """Newline is preserved."""
        assert quote("foo\nbar", "cmd") == '"foo\nbar"'

    def test_empty_string(self):
        """Empty string produces empty quotes."""
        assert quote("", "cmd") == '""'

    def test_only_double_quote(self):
        """Only double quote."""
        assert quote('"', "cmd") == '"\\""'

    def test_multiple_double_quotes(self):
        """Multiple double quotes."""
        assert quote('a"b"c', "cmd") == '"a\\"b\\"c"'

    def test_double_backslash_before_quote(self):
        """Double backslash before quote."""
        # \\" -> \\\\"
        assert quote('foo\\\\"bar', "cmd") == '"foo\\\\\\\\\\"bar"'

    @unittest.skipUnless(platform.system() == "Windows", "CommandLineToArgvW only on Windows")
    def test_roundtrip_simple(self):
        """Roundtrip: simple text."""
        text = "Hello World"
        quoted = quote(text, "cmd")
        result = unquote_cmd(quoted)
        assert result == text

    @unittest.skipUnless(platform.system() == "Windows", "CommandLineToArgvW only on Windows")
    def test_roundtrip_with_quote(self):
        """Roundtrip: text with double quote."""
        text = 'foo"bar'
        quoted = quote(text, "cmd")
        result = unquote_cmd(quoted)
        assert result == text

    @unittest.skipUnless(platform.system() == "Windows", "CommandLineToArgvW only on Windows")
    def test_roundtrip_with_backslash(self):
        """Roundtrip: text with backslash."""
        text = "foo\\bar"
        quoted = quote(text, "cmd")
        result = unquote_cmd(quoted)
        assert result == text

    @unittest.skipUnless(platform.system() == "Windows", "CommandLineToArgvW only on Windows")
    def test_roundtrip_complex(self):
        """Roundtrip: complex string."""
        text = 'foo\\bar"baz'
        quoted = quote(text, "cmd")
        result = unquote_cmd(quoted)
        assert result == text

    def test_special_chars_preserved(self):
        """Special chars preserved in double quotes."""
        text = "foo&bar"
        quoted = quote(text, "cmd")
        assert quoted == '"foo&bar"'

    def test_pipe_preserved(self):
        """Pipe char preserved."""
        text = "foo|bar"
        quoted = quote(text, "cmd")
        assert quoted == '"foo|bar"'

    def test_redirect_preserved(self):
        """Redirect chars preserved."""
        text = "foo>bar"
        quoted = quote(text, "cmd")
        assert quoted == '"foo>bar"'

    def test_percent_warning(self):
        """Arguments containing % get CMD_PERCENT_EXPANSION warning."""
        response = run_safe_shell({"shell": "cmd", "text": "foo%bar%"})
        assert response["ok"] is True
        assert "warnings" in response
        assert response["warnings"][0]["code"] == "CMD_PERCENT_EXPANSION"

    def test_no_percent_no_warning(self):
        """Arguments without % produce no warning."""
        response = run_safe_shell({"shell": "cmd", "text": "foobar"})
        assert response["ok"] is True
        assert "warnings" not in response


class TestCmdEdgeCases(unittest.TestCase):
    """Edge cases for CMD quoting."""

    def test_backslash_at_end(self):
        """Single backslash at end is doubled."""
        assert quote("test\\", "cmd") == '"test\\\\"'

    def test_multiple_trailing_backslashes(self):
        """Multiple trailing backslashes."""
        # \\\ -> \\\\\\
        assert quote("test\\\\", "cmd") == '"test\\\\\\\\"'

    @unittest.skipUnless(platform.system() == "Windows", "CommandLineToArgvW only on Windows")
    def test_backslash_quote_backslash(self):
        """Backslash-quote-backslash pattern."""
        text = '\\"\\'
        quoted = quote(text, "cmd")
        # Should escape properly
        result = unquote_cmd(quoted)
        assert result == text

    @unittest.skipUnless(platform.system() == "Windows", "CommandLineToArgvW only on Windows")
    def test_unicode_in_cmd(self):
        """Unicode text works in CMD."""
        text = "日本語"
        quoted = quote(text, "cmd")
        assert quoted == '"日本語"'
        # Roundtrip with proper encoding
        result = unquote_cmd(quoted)
        assert result == text
