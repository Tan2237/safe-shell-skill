---
name: safe-shell
description: |
  Quote a shell argument for AI agents.

  Usage:
    python "SAFE_SHELL_SCRIPT" @request.json

  Request format (JSON):
    {"shell": "bash", "text": "foo'bar"}

  Supports: bash, zsh, fish, powershell, cmd (safe character subset), msys2

  Resolve SAFE_SHELL_SCRIPT as the absolute path to safe_shell.py
  next to this SKILL.md. Never assume it is in the current directory.

  MANDATORY: Quotes exactly ONE argument per request. For multiple
  arguments, quote each separately. Do NOT use to quote shell scripts
  or shell code passed to eval / bash -c / powershell -Command.
---

# safe-shell — Argument Quoting Service

A JSON-based CLI quoting service for AI agents.
Quotes a single shell argument using correct quoting conventions.
CMD rejects characters that no general quoting rule can preserve safely — see CMD Rejections.

## Runtime Script Resolution

Before invoking safe-shell, resolve `SAFE_SHELL_SCRIPT` once:

1. Start from the absolute path of this `SKILL.md` supplied by the skill loader.
2. Set `SAFE_SHELL_SCRIPT` to the sibling file `safe_shell.py` in the same directory.
3. Convert it to an absolute path and verify that it exists before the first invocation.
4. Reuse that exact absolute path for every safe-shell command in the current task.

`SAFE_SHELL_SCRIPT` in the examples below is a placeholder for that resolved absolute path. It is not a shell environment variable and must not be passed literally.

Never assume `safe_shell.py` is in the current working directory, never resolve it relative to the request file, and never search the whole filesystem. If the sibling script is missing, **STOP** and report both the resolved `SKILL.md` path and the expected script path.

## Mandatory

- **Never manually quote dynamic shell arguments.** Call safe-shell instead.
- **Quote each dynamic argument separately.** One request = exactly ONE argument.
- **safe-shell output is a literal data argument**, not shell syntax.

## Forbidden

- Do NOT use safe-shell to quote entire shell scripts or shell code.
- Do NOT pass safe-shell output as the code operand of `eval`, `bash -c`,
  `sh -c`, `powershell -Command`, or any "execute this string" facility.
- Do NOT concatenate safe-shell output where shell syntax (pipes, `&&`, redirections, backticks) is expected.
- Do NOT re-wrap the quoted output in another quoting layer.
- Do NOT store the quoted string in a variable and expand it unquoted
  (e.g. `cat $QUOTED`) — the shell does not re-parse quotes from variable expansion.

## Decision Tree

```
Need exactly one dynamic data argument?
  -> use safe-shell, embed the quoted output inline in the command

Need multiple dynamic arguments?
  -> call safe-shell once per argument; embed each quoted output inline

Argument is shell code / a script to be executed?
  -> DO NOT use safe-shell

Need pipes, &&, redirections, or backticks?
  -> that is shell syntax, not a data argument; DO NOT use safe-shell
```

## Quick Reference

```bash
# Create request
echo '{"shell":"bash","text":"foo'\''bar"}' > request.json

# Run safe-shell
python "SAFE_SHELL_SCRIPT" @request.json

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
| `text` | Yes | string | Text to quote, or base64-encoded data when `encoding` is `base64` |
| `encoding` | No | string | `base64` — the service will decode it before quoting |

## Failure Classes

| failureClass | Meaning |
|--------------|---------|
| `INVALID_JSON` | Request could not be read or parsed as valid JSON |
| `MISSING_REQUIRED_FIELD` | Required field missing |
| `INVALID_FIELD_TYPE` | Field type error (e.g. text is number) |
| `UNSUPPORTED_SHELL` | shell not in enum |
| `UNSUPPORTED_ENCODING` | encoding not supported |
| `INVALID_ENCODING_DATA` | base64 decode failed |
| `INPUT_TOO_LARGE` | Decoded text > 1 MiB, or request file > 4 MiB |
| `UNQUOTABLE_CHARACTER` | Contains NUL, an unpaired surrogate, or a CMD-unsafe character |
| `INTERNAL_ERROR` | Unexpected internal error |

## Warnings

Warnings appear as an extra field in success responses:

```json
{
  "ok": true,
  "quoted": "'/foo'",
  "shell": "msys2",
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
> Detects leading `/` (e.g. `/usr/bin`) and `=/path` option values
> (e.g. `--mount=/tmp/foo`). Does not guarantee detecting all cases.
> No warning ≠ safe.

## CMD Rejections

`cmd.exe` applies shell parsing before the target program parses its arguments. No general quoting rule can preserve every character through both layers. To maintain the one-literal-argument guarantee, CMD requests fail with `UNQUOTABLE_CHARACTER` when text contains:

- `U+0022` (double quote)
- `%` (environment expansion)
- `!` (delayed expansion)
- CR or LF (command-separator risk)

Treat this as a hard failure. Do not use the returned message as shell syntax and do not attempt to bypass the rejection with manual escaping.

## CMD Implementation

For accepted input, CMD quoting follows the MS C runtime `CommandLineToArgvW` convention and always wraps the result in double quotes. Target programs may use custom argument parsers, so validate application-specific behavior when exact parsing matters.

## Limits

- **MAX_INPUT_SIZE**: 1 MiB (decoded text)
- **MAX_FILE_SIZE**: 4 MiB (request file)
- **Boundary**: Exactly ONE argument per request
