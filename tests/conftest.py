"""Shared test fixtures for safe-shell tests."""

from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "skills" / "safe-shell" / "safe_shell.py"


def _load_core():
    spec = importlib.util.spec_from_file_location(
        "safe_shell_test_core",
        SCRIPT,
    )
    if spec is None or spec.loader is None:
        raise RuntimeError("unable to load safe-shell core")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


core = _load_core()


def quote(text: str, shell: str = "bash") -> str:
    """Get one quoted string directly from the core hot path."""
    response = core.process_request({"shell": shell, "text": text})
    if not response["ok"]:
        raise RuntimeError(
            f"{response['failureClass']}: {response['message']}"
        )
    return response["quoted"]


def run_safe_shell(request: dict) -> dict:
    """Run a decoded request directly through the core dispatcher."""
    return core.process_envelope(request)


def run_safe_shell_raw(content: str) -> dict:
    """Parse raw JSON and dispatch it without spawning a process."""
    return run_safe_shell_bytes(content.encode("utf-8"))


def run_safe_shell_bytes(raw_bytes: bytes) -> dict:
    """Parse raw request bytes and return the structured core response."""
    try:
        request = core.parse_request_bytes(raw_bytes)
    except core.SafeShellError as error:
        return core.fail(error.failure_class, error.message, error.index)
    return core.process_envelope(request)


def run_safe_shell_cli(
    args: list[str],
    input_bytes: bytes | None = None,
) -> subprocess.CompletedProcess:
    """Run the real CLI for parser, transport, and exit-code integration."""
    env = os.environ.copy()
    env.pop("PYTHONIOENCODING", None)
    return subprocess.run(
        [sys.executable, str(SCRIPT)] + args,
        input=input_bytes,
        capture_output=True,
        env=env,
    )


def write_request_file(request: dict) -> str:
    """Write a request dict to a temp JSON file and return its path."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as f:
        json.dump(request, f, ensure_ascii=False)
        return f.name
