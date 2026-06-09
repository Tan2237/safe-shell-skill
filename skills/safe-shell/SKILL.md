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

```json
{
  "warnings": [
    {
      "code": "CMD_PERCENT_EXPANSION",
      "message": "CMD may expand %VAR% patterns inside for/call contexts even within double quotes"
    }
  ]
}
```

> **CMD percent warning.**
>
> CMD double-quoting does NOT prevent `%VAR%` expansion inside `for` loops
> or `call` contexts. If the argument contains `%`, the Agent should be aware
> that environment variable expansion may occur. This is an inherent CMD
> limitation with no reliable workaround.

## CMD Implementation

CMD quoting implements the MS C runtime `CommandLineToArgvW` convention,
following the same rules as Python's `subprocess.list2cmdline()`.
Unlike `list2cmdline`, the output is always double-quoted so agents can
safely concatenate arguments.

> **CMD newline limitation.**
>
> While `CommandLineToArgvW` correctly preserves newlines within double-quoted
> arguments, `cmd.exe` itself may interpret literal newlines as command
> separators when the quoted argument appears in a batch script or is passed
> through multiple layers of command processing. Agents should be cautious
> when quoting arguments containing newlines for CMD.

## Limits

- **MAX_INPUT_SIZE**: 1 MiB
- **Boundary**: Exactly ONE argument per request
