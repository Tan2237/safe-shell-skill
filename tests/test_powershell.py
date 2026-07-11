import platform
import subprocess
import unittest

from .conftest import quote


def run_through_powershell(text: str) -> str:
    quoted = quote(text, 'powershell')
    result = subprocess.run(
        [
            'powershell',
            '-NoProfile',
            '-Command',
            '[Console]::OutputEncoding=[Text.Encoding]::UTF8;' + '[Console]::Out.Write(' + quoted + ')',
        ],
        capture_output=True,
        encoding='utf-8',
    )
    assert result.returncode == 0, result.stderr
    return result.stdout


class TestPowerShellQuoting(unittest.TestCase):
    def test_simple_text(self):
        assert quote('foo', 'powershell') == '\'foo\''

    def test_text_with_space(self):
        assert quote('foo bar', 'powershell') == '\'foo bar\''

    def test_text_with_single_quote(self):
        assert quote('foo\'bar', 'powershell') == '\'foo\'\'bar\''

    def test_text_with_double_quote(self):
        text = 'foo' + chr(34) + 'bar'
        assert quote(text, 'powershell') == '\'' + text + '\''

    def test_literal_special_characters(self):
        text = 'foo$bar' + chr(96) + 'baz\\qux'
        assert quote(text, 'powershell') == '\'' + text + '\''

    def test_empty_string(self):
        assert quote('', 'powershell') == '\'\''

    @unittest.skipUnless(platform.system() == 'Windows', 'PowerShell only on Windows')
    def test_roundtrip_through_powershell(self):
        cases = [
            'Hello, World!',
            'foo\'bar',
            '$HOME',
            'foo' + chr(96) + 'bar',
            '日本語 テスト',
            'line1\nline2',
            '  surrounding spaces  ',
        ]
        for text in cases:
            with self.subTest(text=text):
                assert run_through_powershell(text) == text
