---
name: safe-shell
description: |
  Safely quote shell arguments for AI agents.

  Usage:
    python safe_shell.py @request.json

  Request format (JSON):
    {"shell": "bash", "text": "foo'bar"}

  Supports: bash, zsh, fish, powershell, cmd, msys2
---

# safe-shell — Argument Quoting Service

A JSON-RPC style service that safely quotes shell arguments for AI agents.

## Quick Reference

```bash
# Create request
echo '{"shell":"bash","text":"foo'\''bar"}' > request.json

# Run safe-shell
python safe_shell.py @request.json

# Output
{"ok":true,"quoted":"'foo'\\''bar'","shell":"bash"}
```

For content with boundary characters, use base64:

```bash
echo '{"shell":"bash","encoding":"base64","text":"Zm9vJ2Jhcg=="}' > request.json
```

## Request

```json
{
  "shell": "bash",
  "text": "foo'bar"
}
```

## Response

Success:

```json
{
  "ok": true,
  "quoted": "'foo'\\''bar'",
  "shell": "bash"
}
```

Failure:

```json
{
  "ok": false,
  "failureClass": "UNSUPPORTED_SHELL",
  "message": "shell 'pwsh' is not supported"
}
```

## Shell Types

| Shell | Enum Value |
|-------|------------|
| Bash | `bash` |
| Zsh | `zsh` |
| Fish | `fish` |
| PowerShell | `powershell` |
| CMD | `cmd` |
| MSYS2 | `msys2` |

## Field Reference

| Field | Required | Type | Description |
|-------|----------|------|-------------|
| `shell` | Yes | string | Shell type (see above) |
| `text` | Yes | string | Text to quote |
| `encoding` | No | string | `base64` to decode first |

## Failure Classes

| failureClass | Meaning |
|--------------|---------|
| `INVALID_JSON` | JSON parse failed |
| `MISSING_REQUIRED_FIELD` | Required field missing |
| `INVALID_FIELD_TYPE` | Field type error (e.g. text is number) |
| `UNSUPPORTED_SHELL` | shell not in enum |
| `UNSUPPORTED_ENCODING` | encoding not supported |
| `INVALID_ENCODING_DATA` | base64 decode failed |
| `INPUT_TOO_LARGE` | Input > 1 MiB |
| `UNQUOTABLE_CHARACTER` | Contains NUL |

## Warnings

```json
{
  "warnings": [
    {
      "code": "MSYS2_PATH_CONVERSION",
      "message": "MSYS2 may convert paths starting with /"
    }
  ]
}
```

> **MSYS2 path warning is heuristic.**
>
> Only detects `/` and `//` prefixes. Does not guarantee detecting all cases.
> No warning ≠ safe.

## CMD Implementation

CMD quoting uses `subprocess.list2cmdline()` from Python standard library.
This implements the MS C runtime `CommandLineToArgvW` convention.

## Limits

- **MAX_INPUT_SIZE**: 1 MiB
- **Boundary**: Exactly ONE argument per request
