"""Tests for bash quoting."""

import os
import platform
import shutil
import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from .conftest import quote, run_safe_shell


def run_posix_fragment(
    executable: str,
    quoted: str,
    env=None,
) -> str:
    """Parse one quoted fragment with a real POSIX-like shell."""
    result = subprocess.run(
        [executable, "-c", f"printf '%s' {quoted}"],
        capture_output=True,
        env=env,
    )
    assert result.returncode == 0, result.stderr.decode(
        "utf-8",
        errors="replace",
    )
    return result.stdout.decode("utf-8")


def unquote_bash(quoted: str) -> str:
    """Unquote a bash quoted string."""
    return run_posix_fragment("bash", quoted)


def find_windows_msys2_bash():
    """Find a native MSYS2 or Git Bash executable on Windows."""
    if platform.system() != "Windows":
        return None
    program_files = Path(
        os.environ.get("ProgramFiles", r"C:\Program Files")
    )
    configured = os.environ.get("SAFE_SHELL_TEST_MSYS2_BASH")
    candidates = []
    if configured:
        candidates.append(Path(configured))
    candidates.extend([
        Path(r"C:\msys64\usr\bin\bash.exe"),
        Path(r"C:\tools\msys64\usr\bin\bash.exe"),
        program_files / "Git" / "bin" / "bash.exe",
        program_files / "Git" / "usr" / "bin" / "bash.exe",
    ])
    for candidate in candidates:
        if candidate.is_file():
            return str(candidate)
    return None


WINDOWS_MSYS2_BASH = find_windows_msys2_bash()


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
        assert result.returncode == 0, result.stderr
        assert result.stdout == text


class TestPosixCompatibilityProfiles(unittest.TestCase):
    """Tests for sh/dash/ksh and any installed POSIX-like shell."""

    def test_compatibility_profiles_use_single_quote_algorithm(self):
        for shell in ("sh", "dash", "ksh"):
            with self.subTest(shell=shell):
                assert quote("foo'bar", shell) == "'foo'\\''bar'"

    def test_all_available_profiles_roundtrip(self):
        available = [
            (shell, executable)
            for shell in (
                "bash",
                "sh",
                "dash",
                "ksh",
                "zsh",
                "fish",
            )
            if (executable := shutil.which(shell))
        ]
        if not available:
            self.skipTest("no POSIX-like shell available")

        text = "line 1\n$HOME ' \" \\ 日本語"
        for shell, executable in available:
            with self.subTest(shell=shell):
                quoted = quote(text, shell)
                assert run_posix_fragment(executable, quoted) == text


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

    def test_ci_override_is_preferred(self):
        configured = str(Path(__file__).resolve())
        with patch.dict(
            os.environ,
            {"SAFE_SHELL_TEST_MSYS2_BASH": configured},
        ):
            with patch.object(platform, "system", return_value="Windows"):
                self.assertEqual(find_windows_msys2_bash(), configured)

    def test_simple_text(self):
        """Simple text is single-quoted."""
        assert quote("foo", "msys2") == "'foo'"

    def test_single_quote_escaped(self):
        """Single quote is escaped."""
        assert quote("foo'bar", "msys2") == "'foo'\\''bar'"

    def test_path_warning(self):
        """Paths starting with / get MSYS2_PATH_CONVERSION warning."""
        response = run_safe_shell({"shell": "msys2", "text": "/foo/bar"})
        assert response["ok"] is True
        assert "warnings" in response
        assert response["warnings"][0]["code"] == "MSYS2_PATH_CONVERSION"

    def test_double_slash_warning(self):
        """Paths starting with // get warning."""
        response = run_safe_shell({"shell": "msys2", "text": "//server/share"})
        assert response["ok"] is True
        assert "warnings" in response

    def test_option_value_path_warning(self):
        """Option values like --mount=/tmp/foo get MSYS2_PATH_CONVERSION warning."""
        response = run_safe_shell({"shell": "msys2", "text": "--mount=/tmp/foo"})
        assert response["ok"] is True
        assert "warnings" in response
        assert response["warnings"][0]["code"] == "MSYS2_PATH_CONVERSION"

    def test_url_no_warning(self):
        """URLs (https://...) do not trigger MSYS2 path warning (no false positive)."""
        response = run_safe_shell({"shell": "msys2", "text": "https://example.com/path"})
        assert response["ok"] is True
        assert "warnings" not in response

    def test_no_warning_for_normal_text(self):
        """No warning for text not starting with /."""
        response = run_safe_shell({"shell": "msys2", "text": "foo/bar"})
        assert response["ok"] is True
        assert "warnings" not in response

    @unittest.skipUnless(
        WINDOWS_MSYS2_BASH,
        "MSYS2 or Git Bash not available on Windows",
    )
    def test_windows_msys2_fragment_roundtrip(self):
        """A discovered native bash must parse the MSYS2 fragment exactly."""
        text = "/tmp/a b'c$HOME\\tail"
        response = run_safe_shell({"shell": "msys2", "text": text})
        assert response["ok"] is True
        assert response["warnings"][0]["code"] == (
            "MSYS2_PATH_CONVERSION"
        )

        env = os.environ.copy()
        env["MSYS2_ARG_CONV_EXCL"] = "*"
        assert run_posix_fragment(
            WINDOWS_MSYS2_BASH,
            response["quoted"],
            env=env,
        ) == text
