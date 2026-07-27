"""CLI transport regression tests."""

import base64
import json
import os
import subprocess
import sys
import unittest

from .conftest import SCRIPT, run_safe_shell


def _run(args, input_bytes=None):
    env = os.environ.copy()
    env["PYTHONIOENCODING"] = "utf-8"
    return subprocess.run(
        [sys.executable, str(SCRIPT), *args],
        input=input_bytes,
        capture_output=True,
        env=env,
    )


class TestCliTransports(unittest.TestCase):
    def test_request_stdin(self):
        payload = json.dumps(
            {"shell": "bash", "text": "foo'bar"}, ensure_ascii=False
        ).encode("utf-8")
        completed = _run(["--request-stdin"], payload)
        self.assertEqual(completed.returncode, 0)
        response = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(response["quoted"], "'foo'\\''bar'")

    def test_request_base64_accepts_urlsafe_unpadded_envelope(self):
        payload = json.dumps(
            {"shell": "powershell", "text": "a'b"},
            ensure_ascii=False,
            separators=(",", ":"),
        ).encode("utf-8")
        encoded = base64.urlsafe_b64encode(payload).decode("ascii").rstrip("=")
        completed = _run(["--request-base64", encoded])
        self.assertEqual(completed.returncode, 0)
        response = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(response["quoted"], "'a''b'")

    def test_invalid_request_base64_is_structured_failure(self):
        completed = _run(["--request-base64", "not*base64"])
        self.assertEqual(completed.returncode, 1)
        response = json.loads(completed.stdout.decode("utf-8"))
        self.assertEqual(response["failureClass"], "INVALID_ENCODING_DATA")

    def test_text_base64_accepts_urlsafe_unpadded_data(self):
        original = "路径?/a+b"
        encoded = base64.urlsafe_b64encode(
            original.encode("utf-8")
        ).decode("ascii").rstrip("=")
        response = run_safe_shell(
            {"shell": "bash", "text": encoded, "encoding": "base64"}
        )
        self.assertTrue(response["ok"])
        self.assertEqual(response["quoted"], "'" + original + "'")


if __name__ == "__main__":
    unittest.main()
