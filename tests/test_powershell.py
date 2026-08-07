import json
import shutil
import subprocess
import sys
import unittest

from .conftest import quote, run_safe_shell


POWERSHELL_SINGLE_QUOTE_CHARACTERS = (
    "'",
    "\u2018",
    "\u2019",
    "\u201a",
    "\u201b",
)
POWERSHELL_QUOTE_LOOKALIKES = (
    "\u201c",
    "\u201d",
    "\uff07",
    "\u02bc",
    "\u2032",
    "\u275b",
    "\u275c",
)


def run_through_powershell(text: str, profile: str) -> str:
    executable = shutil.which(profile)
    if executable is None:
        raise RuntimeError(f"{profile} is not available")
    quoted = quote(text, profile)
    command = (
        "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
        + "[Console]::Out.Write("
        + quoted
        + ")"
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-Command", command],
        capture_output=True,
        encoding="utf-8",
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


def run_native_argument_through_powershell(
    text: str,
    profile: str,
) -> list[str]:
    executable = shutil.which(profile)
    if executable is None:
        raise RuntimeError(f"{profile} is not available")
    probe = (
        "import json,sys;print(json.dumps("
        "sys.argv[1:],ensure_ascii=True))"
    )
    prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    if profile == "pwsh":
        prefix += "$PSNativeCommandArgumentPassing='Standard';"
    command = (
        prefix
        + "& "
        + quote(sys.executable, profile)
        + " -c "
        + quote(probe, profile)
        + " "
        + quote(text, profile)
    )
    result = subprocess.run(
        [executable, "-NoProfile", "-Command", command],
        capture_output=True,
    )
    assert result.returncode == 0, result.stderr.decode(
        "utf-8", errors="replace"
    )
    return json.loads(result.stdout.decode("ascii"))


class TestPowerShellQuoting(unittest.TestCase):
    def test_simple_text(self):
        assert quote('foo', 'powershell') == '\'foo\''

    def test_text_with_space(self):
        assert quote('foo bar', 'powershell') == '\'foo bar\''

    def test_text_with_single_quote(self):
        assert quote('foo\'bar', 'powershell') == '\'foo\'\'bar\''

    def test_all_single_quote_characters_are_doubled(self):
        text = "A".join(POWERSHELL_SINGLE_QUOTE_CHARACTERS)
        escaped = "A".join(
            character * 2
            for character in POWERSHELL_SINGLE_QUOTE_CHARACTERS
        )
        for profile in ("powershell", "pwsh"):
            with self.subTest(profile=profile):
                assert quote(text, profile) == "'" + escaped + "'"

    def test_quote_lookalikes_remain_literal(self):
        text = "A".join(POWERSHELL_QUOTE_LOOKALIKES)
        for profile in ("powershell", "pwsh"):
            with self.subTest(profile=profile):
                assert quote(text, profile) == "'" + text + "'"

    def test_pwsh_accepts_double_quote_and_empty_string(self):
        text = 'foo' + chr(34) + 'bar'
        assert quote(text, "pwsh") == "'" + text + "'"
        assert quote("", "pwsh") == "''"

    def test_literal_special_characters(self):
        text = 'foo$bar' + chr(96) + 'baz\\qux'
        assert quote(text, 'powershell') == '\'' + text + '\''

    def test_legacy_profile_rejects_native_argv_loss_cases(self):
        cases = (
            "",
            'a"b',
            "path with space\\",
            "\tpath\\",
            "\u3000path\\",
        )
        for text in cases:
            with self.subTest(text=text):
                result = run_safe_shell(
                    {"shell": "powershell", "text": text}
                )
                assert result["ok"] is False
                assert (
                    result["failureClass"]
                    == "UNQUOTABLE_CHARACTER"
                )
                assert "legacy native argument passing" in result[
                    "message"
                ]

    def test_pwsh_profile_uses_the_same_token_rules(self):
        text = "foo'bar$HOME"
        assert quote(text, "pwsh") == "'foo''bar$HOME'"

    def _assert_roundtrip_cases(self, profile: str):
        injection_cases = [
            f"safe{character});Write-Output PWN;#"
            for character in POWERSHELL_SINGLE_QUOTE_CHARACTERS
        ]
        cases = [
            'Hello, World!',
            'foo\'bar',
            '$HOME',
            'foo' + chr(96) + 'bar',
            '日本語 テスト',
            'line1\nline2',
            '  surrounding spaces  ',
            "A".join(POWERSHELL_SINGLE_QUOTE_CHARACTERS),
            "A".join(POWERSHELL_QUOTE_LOOKALIKES),
            *injection_cases,
        ]
        if profile == "pwsh":
            cases.extend(["", 'a"b', "path with space\\"])
        for text in cases:
            with self.subTest(profile=profile, text=text):
                assert run_through_powershell(text, profile) == text

    @unittest.skipUnless(
        shutil.which("powershell"),
        "Windows PowerShell not available",
    )
    def test_roundtrip_through_powershell(self):
        self._assert_roundtrip_cases("powershell")

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not available")
    def test_roundtrip_through_pwsh(self):
        self._assert_roundtrip_cases("pwsh")

    @unittest.skipUnless(
        shutil.which("powershell"),
        "Windows PowerShell not available",
    )
    def test_native_argv_roundtrip_through_windows_powershell(self):
        cases = (
            "plain",
            "two words",
            "C:\\plain\\",
            "line1\nline2",
            "日本語🙂",
            "A’B",
        )
        for text in cases:
            with self.subTest(text=text):
                assert run_native_argument_through_powershell(
                    text, "powershell"
                ) == [text]

    @unittest.skipUnless(shutil.which("pwsh"), "pwsh not available")
    def test_native_argv_roundtrip_through_pwsh_standard(self):
        cases = (
            "",
            'a"b',
            'a\\"b',
            "path with space\\",
            "path with two\\\\",
            "日本語🙂",
        )
        for text in cases:
            with self.subTest(text=text):
                assert run_native_argument_through_powershell(
                    text, "pwsh"
                ) == [text]
