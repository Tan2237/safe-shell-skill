#!/usr/bin/env python3
"""safe-shell: A JSON-RPC style argument quoting service for AI agents.

Usage: safe-shell @request.json

Request format (JSON):
{
    "shell": "bash",
    "text": "foo'bar",
    "encoding": "base64"  // optional
}

Response format (JSON):
{
    "ok": true,
    "quoted": "'foo'\\''bar'",
    "shell": "bash"
}
"""

from __future__ import annotations

import base64
import binascii
import json
import sys
from typing import Any

MAX_INPUT_SIZE = 1024 * 1024  # 1 MiB
MAX_FILE_SIZE = 4 * 1024 * 1024  # 4 MiB (base64 of 1 MiB ≈ 1.37 MiB; leaves headroom for JSON overhead)

SUPPORTED_SHELLS = frozenset(["bash", "zsh", "fish", "powershell", "cmd", "msys2"])


class SafeShellError(Exception):
    """Base exception for safe-shell errors."""

    def __init__(self, failure_class: str, message: str) -> None:
        self.failure_class = failure_class
        self.message = message
        super().__init__(message)


def fail(failure_class: str, message: str) -> dict[str, Any]:
    """Create a failure response."""
    return {
        "ok": False,
        "failureClass": failure_class,
        "message": message,
    }


def success(quoted: str, shell: str, warnings: list[dict[str, str]] | None = None) -> dict[str, Any]:
    """Create a success response."""
    result = {
        "ok": True,
        "quoted": quoted,
        "shell": shell,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def quote_bash_zsh_fish_msys2(text: str, shell: str) -> tuple[str, list[dict[str, str]] | None]:
    """Quote for bash/zsh/fish/msys2 using single-quote escaping.

    'foo'bar' -> 'foo'\\''bar'
    """
    warnings = None
    if shell == "msys2" and (text.startswith("/") or text.startswith("//")):
        warnings = [{"code": "MSYS2_PATH_CONVERSION", "message": "MSYS2 may convert paths starting with /"}]

    # Replace ' with '\'' and wrap in single quotes
    quoted = "'" + text.replace("'", "'\\''") + "'"
    return quoted, warnings


def quote_powershell(text: str) -> str:
    """Quote for PowerShell using single-quote escaping.

    foo'bar -> 'foo''bar'
    """
    # Escape ' by doubling it
    return "'" + text.replace("'", "''") + "'"


def quote_cmd(text: str) -> tuple[str, list[dict[str, str]] | None]:
    """Quote for CMD using CommandLineToArgvW rules.

    This implements the MS C runtime convention (same as subprocess.list2cmdline):
    - Backslashes before quotes: 2n backslashes + quote -> n backslashes + escaped quote
    - Backslashes before quotes: 2n+1 backslashes + quote -> n backslashes + literal quote
    - Trailing backslashes are doubled before the closing quote

    Unlike subprocess.list2cmdline, this always returns a quoted string
    so agents can safely concatenate arguments.

    Note: CMD double-quoting does NOT prevent %VAR% expansion inside
    for loops or call contexts. This is an inherent CMD limitation.
    """
    warnings = []
    if "%" in text:
        warnings.append({"code": "CMD_PERCENT_EXPANSION", "message": "CMD may expand %VAR% patterns inside for/call contexts even within double quotes"})
    if "\n" in text or "\r" in text:
        warnings.append({"code": "CMD_NEWLINE_INJECTION", "message": "CMD may interpret newlines as command separators"})

    # Build the quoted string
    result = ['"']

    # Count backslashes and process
    backslashes = 0
    for c in text:
        if c == '\\':
            backslashes += 1
        elif c == '"':
            # Double all backslashes before the quote, then add escaped quote
            result.append('\\' * (backslashes * 2))
            result.append('\\"')
            backslashes = 0
        else:
            # Output any pending backslashes
            if backslashes:
                result.append('\\' * backslashes)
                backslashes = 0
            result.append(c)

    # Handle trailing backslashes (double them before closing quote)
    result.append('\\' * (backslashes * 2))
    result.append('"')

    return ''.join(result), warnings or None


def quote_for_shell(text: str, shell: str) -> tuple[str, list[dict[str, str]] | None]:
    """Quote text for the specified shell."""
    if shell in ("bash", "zsh", "fish", "msys2"):
        return quote_bash_zsh_fish_msys2(text, shell)
    elif shell == "powershell":
        return quote_powershell(text), None
    elif shell == "cmd":
        return quote_cmd(text)
    else:
        raise SafeShellError("UNSUPPORTED_SHELL", f"shell '{shell}' is not supported")


def decode_text(text: str, encoding: str | None) -> str:
    """Decode text if encoding is specified."""
    if encoding is None or encoding == "":
        return text

    if encoding == "base64":
        try:
            decoded = base64.b64decode(text, validate=True)
            return decoded.decode("utf-8")
        except (binascii.Error, UnicodeDecodeError) as e:
            raise SafeShellError("INVALID_ENCODING_DATA", f"base64 decode failed: {e}")
    else:
        raise SafeShellError("UNSUPPORTED_ENCODING", f"encoding '{encoding}' is not supported")


def validate_request(data: dict[str, Any]) -> tuple[str, str]:
    """Validate request and return (shell, text).

    Validation order:
    1. Required fields
    2. encoding type and value
    3. text type check
    4. encoding data decode
    5. NUL character check
    6. input size (decoded, measured in UTF-8 bytes)
    7. shell type and value
    """
    # 1. Required fields
    for field in ["shell", "text"]:
        if field not in data:
            raise SafeShellError("MISSING_REQUIRED_FIELD", f"missing required field: {field}")

    # 2. encoding (optional)
    encoding = data.get("encoding")
    if encoding is not None:
        if not isinstance(encoding, str):
            raise SafeShellError("INVALID_FIELD_TYPE", "encoding must be string")
        if encoding != "" and encoding != "base64":
            raise SafeShellError("UNSUPPORTED_ENCODING", f"encoding '{encoding}' is not supported")

    # 3. text type check
    text = data.get("text")
    if not isinstance(text, str):
        raise SafeShellError("INVALID_FIELD_TYPE", f"text must be string, got: {type(text).__name__}")

    # 4. encoding data decode
    text = decode_text(text, encoding)

    # 5. Check for NUL character
    if "\x00" in text:
        raise SafeShellError(
            "UNQUOTABLE_CHARACTER",
            "text contains NUL character (\\x00) which cannot be safely quoted"
        )

    # 6. input size check (on decoded text, measured in UTF-8 bytes)
    text_len = len(text)
    if text_len > MAX_INPUT_SIZE:
        raise SafeShellError(
            "INPUT_TOO_LARGE",
            f"input exceeds maximum {MAX_INPUT_SIZE} bytes (character count: {text_len})"
        )
    # Only encode if character count is large enough to potentially exceed the byte limit
    # (worst case: 4 bytes per character in UTF-8). Skip for short inputs.
    if text_len > MAX_INPUT_SIZE // 4:
        text_bytes = len(text.encode("utf-8"))
        if text_bytes > MAX_INPUT_SIZE:
            raise SafeShellError(
                "INPUT_TOO_LARGE",
                f"input size {text_bytes} exceeds maximum {MAX_INPUT_SIZE} bytes"
            )

    # 7. shell validation
    shell = data.get("shell")
    if not isinstance(shell, str):
        raise SafeShellError("INVALID_FIELD_TYPE", f"shell must be string, got: {type(shell).__name__}")

    if shell not in SUPPORTED_SHELLS:
        raise SafeShellError("UNSUPPORTED_SHELL", f"shell '{shell}' is not supported")

    return shell, text


def process_request(request_data: dict[str, Any]) -> dict[str, Any]:
    """Process a request and return the response."""
    try:
        shell, text = validate_request(request_data)
        quoted, warnings = quote_for_shell(text, shell)
        return success(quoted, shell, warnings)
    except SafeShellError as e:
        return fail(e.failure_class, e.message)
    except Exception as e:
        print(f"safe-shell internal error: {e}", file=sys.stderr)
        return fail("INTERNAL_ERROR", f"unexpected error: {type(e).__name__}")


def main(args: list[str] | None = None) -> int:
    """Main entry point."""
    if args is None:
        args = sys.argv[1:]

    if not args:
        print("Usage: safe-shell @request.json", file=sys.stderr)
        return 1

    # Handle @file syntax
    request_file = None
    for arg in args:
        if arg.startswith("@"):
            if request_file is not None:
                print("Warning: multiple @file arguments found, using first one only", file=sys.stderr)
                break
            request_file = arg[1:]

    if request_file is None:
        print("Usage: safe-shell @request.json", file=sys.stderr)
        return 1

    # Read request file
    try:
        with open(request_file, "rb") as f:
            raw_bytes = f.read(MAX_FILE_SIZE + 1)
            if len(raw_bytes) > MAX_FILE_SIZE:
                response = fail("INPUT_TOO_LARGE", f"request file exceeds maximum {MAX_FILE_SIZE} bytes")
                print(json.dumps(response, ensure_ascii=False))
                return 1
        try:
            raw_content = raw_bytes.decode("utf-8")
        except UnicodeDecodeError as e:
            response = fail("INVALID_JSON", f"file encoding error: {e}")
            print(json.dumps(response, ensure_ascii=False))
            return 1
    except FileNotFoundError:
        response = fail("INVALID_JSON", f"file not found: {request_file}")
        print(json.dumps(response, ensure_ascii=False))
        return 1
    except OSError as e:
        response = fail("INVALID_JSON", f"cannot read file: {e}")
        print(json.dumps(response, ensure_ascii=False))
        return 1

    # Parse JSON
    try:
        request_data = json.loads(raw_content)
    except json.JSONDecodeError as e:
        response = fail("INVALID_JSON", str(e))
        print(json.dumps(response, ensure_ascii=False))
        return 1

    # Validate that we got a dict
    if not isinstance(request_data, dict):
        response = fail("INVALID_JSON", "request must be a JSON object")
        print(json.dumps(response, ensure_ascii=False))
        return 1

    # Process request
    response = process_request(request_data)
    print(json.dumps(response, ensure_ascii=False))

    return 0 if response["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
