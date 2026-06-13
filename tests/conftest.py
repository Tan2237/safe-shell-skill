"""Shared test fixtures for safe-shell tests."""

import json
import os
import subprocess
import sys
import tempfile
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


def run_safe_shell(request: dict) -> dict:
    """Run safe-shell with a JSON request and return the parsed response."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(request, f, ensure_ascii=False)
        request_file = f.name

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), f"@{request_file}"],
            capture_output=True,
            env=env,
        )
        return json.loads(result.stdout.decode("utf-8"))
    finally:
        Path(request_file).unlink(missing_ok=True)


def run_safe_shell_raw(content: str) -> dict:
    """Run safe-shell with raw content (may be invalid JSON)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        f.write(content)
        request_file = f.name

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), f"@{request_file}"],
            capture_output=True,
            env=env,
        )
        return json.loads(result.stdout.decode("utf-8"))
    finally:
        Path(request_file).unlink(missing_ok=True)


def run_safe_shell_bytes(raw_bytes: bytes) -> dict:
    """Run safe-shell with raw bytes file content (for testing encoding errors)."""
    with tempfile.NamedTemporaryFile(mode="wb", suffix=".json", delete=False) as f:
        f.write(raw_bytes)
        request_file = f.name

    try:
        env = os.environ.copy()
        env["PYTHONIOENCODING"] = "utf-8"
        result = subprocess.run(
            [sys.executable, str(SCRIPT), f"@{request_file}"],
            capture_output=True,
            env=env,
        )
        return json.loads(result.stdout.decode("utf-8"))
    finally:
        Path(request_file).unlink(missing_ok=True)
