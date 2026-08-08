#!/usr/bin/env python3
"""safe-shell: A JSON-based CLI quoting service for AI agents.

Usage:
    safe-shell --request-stdin
    safe-shell --request-base64 BASE64
    safe-shell @request.json

Request format (JSON):
{
    "shell": "bash",
    "text": "foo'bar",
    "encoding": "base64"
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
MAX_FILE_SIZE = 4 * 1024 * 1024  # 4 MiB
MAX_BATCH_ITEMS = 256
MAX_JSON_DEPTH = 100

POSIX_SHELLS = frozenset(
    ["bash", "zsh", "fish", "msys2", "sh", "dash", "ksh"]
)
POWERSHELL_SHELLS = frozenset(["powershell", "pwsh"])
POWERSHELL_SINGLE_QUOTE_CHARACTERS = (
    "'",
    "\u2018",
    "\u2019",
    "\u201a",
    "\u201b",
)
SUPPORTED_SHELLS = POSIX_SHELLS | POWERSHELL_SHELLS | {"cmd"}
SINGLE_REQUEST_FIELDS = frozenset(["shell", "text", "encoding"])
BATCH_REQUEST_FIELDS = frozenset(["shell", "texts"])
# Keep code-point order identical to sorted() in the original diagnostics.
CMD_UNQUOTABLE_CHARACTERS = ("\n", "\r", "!", '"', "%")


class SafeShellError(Exception):
    """Base exception for safe-shell errors."""

    def __init__(
        self,
        failure_class: str,
        message: str,
        index: int | None = None,
    ) -> None:
        self.failure_class = failure_class
        self.message = message
        self.index = index
        super().__init__(message)


def fail(
    failure_class: str,
    message: str,
    index: int | None = None,
) -> dict[str, Any]:
    """Create a failure response."""
    result = {
        "ok": False,
        "failureClass": failure_class,
        "message": message,
    }
    if index is not None:
        result["index"] = index
    return result


def success(
    quoted: str,
    shell: str,
    warnings: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Create a success response."""
    result = {
        "ok": True,
        "quoted": quoted,
        "shell": shell,
    }
    if warnings:
        result["warnings"] = warnings
    return result


def quote_bash_zsh_fish_msys2(
    text: str,
    shell: str,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Quote for POSIX-like shells using single-quote escaping.

    'foo'bar' -> 'foo'\\''bar'
    """
    warnings = None
    if shell == "msys2" and (text.startswith("/") or "=/" in text):
        warnings = [
            {
                "code": "MSYS2_PATH_CONVERSION",
                "message": (
                    "MSYS2 may convert POSIX paths "
                    "(leading / or =/path)"
                ),
            }
        ]

    quoted = "'" + text.replace("'", "'\\''") + "'"
    return quoted, warnings


def quote_powershell(text: str) -> str:
    """Quote for PowerShell using verbatim-string escaping.

    PowerShell treats U+0027 and U+2018..U+201B as single-quote
    delimiters. Each must be doubled inside a single-quoted string.
    """
    for character in POWERSHELL_SINGLE_QUOTE_CHARACTERS:
        text = text.replace(character, character * 2)
    return "'" + text + "'"


def quote_cmd(text: str) -> tuple[str, list[dict[str, Any]] | None]:
    """Quote for CMD using CommandLineToArgvW rules.

    Validated CMD requests cannot contain a double quote. That accepted hot
    path uses C-level string operations and only duplicates trailing
    backslashes. The fallback retains the helper's prior behavior for direct
    callers that pass a double quote.
    """
    if '"' not in text:
        trailing_backslashes = len(text) - len(text.rstrip("\\"))
        return (
            '"' + text + ("\\" * trailing_backslashes) + '"',
            None,
        )

    result = ['"']
    backslashes = 0
    for character in text:
        if character == "\\":
            backslashes += 1
        elif character == '"':
            result.append("\\" * (backslashes * 2))
            result.append('\\"')
            backslashes = 0
        else:
            if backslashes:
                result.append("\\" * backslashes)
                backslashes = 0
            result.append(character)

    result.append("\\" * (backslashes * 2))
    result.append('"')
    return "".join(result), None


def quote_for_shell(
    text: str,
    shell: str,
) -> tuple[str, list[dict[str, Any]] | None]:
    """Quote text for the specified shell."""
    if shell in POSIX_SHELLS:
        return quote_bash_zsh_fish_msys2(text, shell)
    if shell in POWERSHELL_SHELLS:
        return quote_powershell(text), None
    if shell == "cmd":
        return quote_cmd(text)
    raise SafeShellError(
        "UNSUPPORTED_SHELL",
        f"shell '{shell}' is not supported",
    )


def decode_base64_bytes(text: str) -> bytes:
    """Decode padded or unpadded standard/URL-safe Base64."""
    try:
        encoded = text.encode("ascii")
    except UnicodeEncodeError as e:
        raise SafeShellError(
            "INVALID_ENCODING_DATA", f"base64 decode failed: {e}"
        ) from e
    padded = encoded + (b"=" * (-len(encoded) % 4))
    try:
        return base64.b64decode(padded, altchars=b"-_", validate=True)
    except binascii.Error as e:
        raise SafeShellError(
            "INVALID_ENCODING_DATA", f"base64 decode failed: {e}"
        ) from e


def decode_text(text: str, encoding: str | None) -> str:
    """Decode text if encoding is specified."""
    if encoding is None or encoding == "":
        return text
    if encoding != "base64":
        raise SafeShellError(
            "UNSUPPORTED_ENCODING",
            f"encoding '{encoding}' is not supported",
        )
    try:
        return decode_base64_bytes(text).decode("utf-8")
    except UnicodeDecodeError as error:
        raise SafeShellError(
            "INVALID_ENCODING_DATA",
            f"base64 decode failed: {error}",
        ) from error


def _require_fields(
    data: dict[str, Any],
    required: tuple[str, ...],
    allowed: frozenset[str],
) -> None:
    for field in required:
        if field not in data:
            raise SafeShellError(
                "MISSING_REQUIRED_FIELD",
                f"missing required field: {field}",
            )

    unknown = sorted(field for field in data if field not in allowed)
    if unknown:
        raise SafeShellError(
            "UNKNOWN_FIELD",
            f"unknown field(s): {', '.join(unknown)}",
        )


def _validate_encoding(data: dict[str, Any]) -> str | None:
    encoding = data.get("encoding")
    if encoding is None:
        return None
    if not isinstance(encoding, str):
        raise SafeShellError(
            "INVALID_FIELD_TYPE",
            "encoding must be string",
        )
    if encoding not in ("", "base64"):
        raise SafeShellError(
            "UNSUPPORTED_ENCODING",
            f"encoding '{encoding}' is not supported",
        )
    return encoding


def _validate_text(
    raw_text: Any,
    encoding: str | None,
    field_name: str,
) -> tuple[str, int]:
    if not isinstance(raw_text, str):
        raise SafeShellError(
            "INVALID_FIELD_TYPE",
            (
                f"{field_name} must be string, "
                f"got: {type(raw_text).__name__}"
            ),
        )

    text = decode_text(raw_text, encoding)
    if "\x00" in text:
        raise SafeShellError(
            "UNQUOTABLE_CHARACTER",
            (
                f"{field_name} contains NUL character (\\x00) "
                "which cannot be safely quoted"
            ),
        )
    try:
        text_bytes = text.encode("utf-8")
    except UnicodeEncodeError:
        raise SafeShellError(
            "UNQUOTABLE_CHARACTER",
            f"{field_name} contains an unpaired Unicode surrogate",
        ) from None
    return text, len(text_bytes)


def _validate_shell(data: dict[str, Any]) -> str:
    shell = data.get("shell")
    if not isinstance(shell, str):
        raise SafeShellError(
            "INVALID_FIELD_TYPE",
            f"shell must be string, got: {type(shell).__name__}",
        )
    if shell not in SUPPORTED_SHELLS:
        raise SafeShellError(
            "UNSUPPORTED_SHELL",
            f"shell '{shell}' is not supported",
        )
    return shell


def _validate_shell_text(shell: str, text: str) -> None:
    if shell == "powershell":
        if text == "":
            unsafe_reason = "an empty argument"
        elif '"' in text:
            unsafe_reason = "a literal double quote (U+0022)"
        elif text.endswith("\\") and any(
            character.isspace() for character in text
        ):
            unsafe_reason = (
                "a trailing backslash when the value contains whitespace"
            )
        else:
            return
        raise SafeShellError(
            "UNQUOTABLE_CHARACTER",
            (
                "Windows PowerShell legacy native argument passing "
                f"cannot preserve {unsafe_reason}; use an argv-array "
                "execution API or pwsh 7.3+"
            ),
        )

    if shell != "cmd":
        return

    # Iterate the five forbidden constants, not every character in text.
    unsafe = [
        character
        for character in CMD_UNQUOTABLE_CHARACTERS
        if character in text
    ]
    if unsafe:
        rendered = ", ".join(ascii(character) for character in unsafe)
        raise SafeShellError(
            "UNQUOTABLE_CHARACTER",
            f"cmd cannot safely quote character(s): {rendered}",
        )


def validate_request(data: dict[str, Any]) -> tuple[str, str]:
    """Validate a single request and return (shell, decoded text)."""
    _require_fields(
        data,
        required=("shell", "text"),
        allowed=SINGLE_REQUEST_FIELDS,
    )
    encoding = _validate_encoding(data)
    text, text_len = _validate_text(data.get("text"), encoding, "text")
    if text_len > MAX_INPUT_SIZE:
        raise SafeShellError(
            "INPUT_TOO_LARGE",
            (
                f"input size {text_len} exceeds maximum "
                f"{MAX_INPUT_SIZE} bytes"
            ),
        )

    shell = _validate_shell(data)
    _validate_shell_text(shell, text)
    return shell, text


def validate_batch_request(
    data: dict[str, Any],
) -> tuple[str, list[str]]:
    """Validate a batch request and return (shell, decoded texts)."""
    _require_fields(
        data,
        required=("shell", "texts"),
        allowed=BATCH_REQUEST_FIELDS,
    )
    raw_texts = data.get("texts")
    if not isinstance(raw_texts, list):
        raise SafeShellError(
            "INVALID_FIELD_TYPE",
            (
                "texts must be array, "
                f"got: {type(raw_texts).__name__}"
            ),
        )
    if not raw_texts or len(raw_texts) > MAX_BATCH_ITEMS:
        raise SafeShellError(
            "INVALID_FIELD_VALUE",
            (
                f"texts must contain 1..{MAX_BATCH_ITEMS} items, "
                f"got: {len(raw_texts)}"
            ),
        )

    shell = _validate_shell(data)
    texts: list[str] = []
    total_size = 0
    for index, raw_text in enumerate(raw_texts):
        try:
            text, text_len = _validate_text(
                raw_text,
                None,
                f"texts[{index}]",
            )
            total_size += text_len
            if total_size > MAX_INPUT_SIZE:
                raise SafeShellError(
                    "INPUT_TOO_LARGE",
                    (
                        f"batch input size {total_size} exceeds maximum "
                        f"{MAX_INPUT_SIZE} bytes"
                    ),
                    index=index,
                )
            _validate_shell_text(shell, text)
        except SafeShellError as error:
            if error.index is None:
                error.index = index
            raise
        texts.append(text)
    return shell, texts


def process_request(request_data: dict[str, Any]) -> dict[str, Any]:
    """Process a single request and return the response."""
    try:
        shell, text = validate_request(request_data)
        quoted, warnings = quote_for_shell(text, shell)
        return success(quoted, shell, warnings)
    except SafeShellError as error:
        return fail(error.failure_class, error.message, error.index)
    except Exception as error:
        print(f"safe-shell internal error: {error}", file=sys.stderr)
        return fail(
            "INTERNAL_ERROR",
            f"unexpected error: {type(error).__name__}",
        )


def process_batch_request(
    request_data: dict[str, Any],
) -> dict[str, Any]:
    """Process a batch atomically, without returning partial quoted output."""
    try:
        shell, texts = validate_batch_request(request_data)
        quoted_values: list[str] = []
        warnings: list[dict[str, Any]] = []
        for index, text in enumerate(texts):
            quoted, item_warnings = quote_for_shell(text, shell)
            quoted_values.append(quoted)
            for warning in item_warnings or ():
                indexed_warning = dict(warning)
                indexed_warning["index"] = index
                warnings.append(indexed_warning)

        result: dict[str, Any] = {
            "ok": True,
            "shell": shell,
            "count": len(quoted_values),
            "quoted": quoted_values,
        }
        if warnings:
            result["warnings"] = warnings
        return result
    except SafeShellError as error:
        return fail(error.failure_class, error.message, error.index)
    except Exception as error:
        print(f"safe-shell internal error: {error}", file=sys.stderr)
        return fail(
            "INTERNAL_ERROR",
            f"unexpected error: {type(error).__name__}",
        )


def process_envelope(request_data: dict[str, Any]) -> dict[str, Any]:
    """Dispatch a decoded request object to the single or batch API."""
    if "texts" in request_data:
        return process_batch_request(request_data)
    return process_request(request_data)


USAGE = (
    "Usage: safe-shell @request.json | --request-stdin | "
    "--request-base64 BASE64"
)


def _reject_json_constant(value: str) -> None:
    """Reject NaN and infinities, which are not valid JSON numbers."""
    raise ValueError(f"non-JSON numeric constant: {value}")


def _enforce_json_depth(text: str) -> None:
    """Reject JSON nested deeper than MAX_JSON_DEPTH.

    json.loads relies on the interpreter recursion limit, whose effective
    depth varies by platform and Python version, so enforce a fixed,
    platform-independent bound before parsing.
    """
    depth = 0
    in_string = False
    escaped = False
    for char in text:
        if in_string:
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char in "[{":
            depth += 1
            if depth > MAX_JSON_DEPTH:
                raise SafeShellError(
                    "INVALID_JSON",
                    f"request JSON nesting exceeds maximum depth "
                    f"{MAX_JSON_DEPTH}",
                )
        elif char in "]}":
            depth -= 1


def parse_request_bytes(raw_bytes: bytes) -> dict[str, Any]:
    """Parse one bounded UTF-8 JSON request payload."""
    if len(raw_bytes) > MAX_FILE_SIZE:
        raise SafeShellError(
            "INPUT_TOO_LARGE",
            f"request payload exceeds maximum {MAX_FILE_SIZE} bytes",
        )
    try:
        raw_content = raw_bytes.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SafeShellError("INVALID_JSON", f"request encoding error: {e}") from e
    _enforce_json_depth(raw_content)
    try:
        request_data = json.loads(
            raw_content,
            parse_constant=_reject_json_constant,
        )
    except (ValueError, RecursionError) as error:
        raise SafeShellError(
            "INVALID_JSON",
            str(error),
        ) from error
    if not isinstance(request_data, dict):
        raise SafeShellError("INVALID_JSON", "request must be a JSON object")
    return request_data


def read_request(args: list[str]) -> dict[str, Any] | None:
    """Read a request from stdin, Base64, or the legacy @file transport."""
    if args == ["--request-stdin"]:
        return parse_request_bytes(sys.stdin.buffer.read(MAX_FILE_SIZE + 1))

    if len(args) == 2 and args[0] == "--request-base64":
        if len(args[1]) > MAX_FILE_SIZE * 2:
            raise SafeShellError(
                "INPUT_TOO_LARGE",
                "encoded request exceeds the bounded command-line transport",
            )
        return parse_request_bytes(decode_base64_bytes(args[1]))

    if len(args) != 1 or not args[0].startswith("@"):
        return None
    request_file = args[0][1:]
    try:
        with open(request_file, "rb") as stream:
            return parse_request_bytes(stream.read(MAX_FILE_SIZE + 1))
    except FileNotFoundError as e:
        raise SafeShellError(
            "INVALID_JSON", f"file not found: {request_file}"
        ) from e
    except OSError as e:
        raise SafeShellError("INVALID_JSON", f"cannot read file: {e}") from e


def emit_response(response: dict[str, Any]) -> int:
    """Write one UTF-8 JSON response and return its process exit code."""
    payload = (
        json.dumps(response, ensure_ascii=False) + "\n"
    ).encode("utf-8", errors="backslashreplace")
    stdout = sys.stdout
    stdout_buffer = getattr(stdout, "buffer", None)

    if stdout_buffer is None:
        # StringIO and similar text-only streams have no binary buffer.
        stdout.write(payload.decode("utf-8"))
        stdout.flush()
    else:
        # Bypass locale-dependent encodings such as Windows GBK/cp1252.
        stdout.flush()
        stdout_buffer.write(payload)
        stdout_buffer.flush()

    return 0 if response["ok"] else 1


def main(args: list[str] | None = None) -> int:
    """Main entry point."""
    if args is None:
        args = sys.argv[1:]

    if not args:
        print(USAGE, file=sys.stderr)
        return 1

    try:
        request_data = read_request(args)
    except SafeShellError as e:
        return emit_response(fail(e.failure_class, e.message))

    if request_data is None:
        print(USAGE, file=sys.stderr)
        return 1

    return emit_response(process_envelope(request_data))


if __name__ == "__main__":
    sys.exit(main())
