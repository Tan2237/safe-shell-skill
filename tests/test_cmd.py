import ast
import platform
import subprocess
import unittest

from .conftest import quote, run_safe_shell


def run_through_cmd(text: str) -> str:
    if platform.system() != 'Windows':
        raise RuntimeError('cmd.exe integration test requires Windows')

    quoted = quote(text, 'cmd')
    command = (
        subprocess.list2cmdline(
            ['python', '-c', 'import sys;print(ascii(sys.argv[1]))']
        )
        + ' '
        + quoted
    )
    result = subprocess.run(
        'cmd.exe /d /c ' + command,
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    return ast.literal_eval(result.stdout.strip())


class TestCmdQuoting(unittest.TestCase):
    def test_simple_text(self):
        assert quote('foo', 'cmd') == chr(34) + 'foo' + chr(34)

    def test_text_with_space(self):
        assert quote('foo bar', 'cmd') == chr(34) + 'foo bar' + chr(34)

    def test_text_with_backslash(self):
        assert quote(r'foo\bar', 'cmd') == chr(34) + r'foo\bar' + chr(34)

    def test_trailing_backslash(self):
        assert quote('foo\\', 'cmd') == chr(34) + 'foo\\\\' + chr(34)

    def test_empty_string(self):
        assert quote('', 'cmd') == chr(34) * 2

    def test_shell_metacharacters_inside_quotes(self):
        for text in ['foo&bar', 'foo|bar', 'foo>bar', 'foo<bar', 'foo^bar', '(foo)']:
            with self.subTest(text=text):
                assert quote(text, 'cmd') == chr(34) + text + chr(34)

    def test_unquotable_characters_are_rejected(self):
        cases = [
            'foo' + chr(34) + 'bar',
            'foo%PATH%',
            'foo!PATH!',
            'foo\nbar',
            'foo\rbar',
        ]
        for text in cases:
            with self.subTest(text=text):
                response = run_safe_shell({'shell': 'cmd', 'text': text})
                assert response['ok'] is False
                assert response['failureClass'] == 'UNQUOTABLE_CHARACTER'

    def test_multiple_unquotable_characters_reported_once(self):
        response = run_safe_shell(
            {'shell': 'cmd', 'text': chr(34) + '%PATH%!\r\n'}
        )
        assert response['ok'] is False
        assert response['failureClass'] == 'UNQUOTABLE_CHARACTER'
        assert 'cmd cannot safely quote' in response['message']


class TestCmdIntegration(unittest.TestCase):
    @unittest.skipUnless(platform.system() == 'Windows', 'cmd.exe only on Windows')
    def test_safe_arguments_roundtrip_through_cmd_exe(self):
        cases = [
            'Hello World',
            r'foo\bar',
            'test\\',
            'foo&bar',
            'foo|bar',
            'foo>bar',
            'foo<bar',
            'foo^bar',
            '(foo)',
            '日本語',
        ]
        for text in cases:
            with self.subTest(text=text):
                assert run_through_cmd(text) == text
