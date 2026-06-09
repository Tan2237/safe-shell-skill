# safe-shell

> A JSON-RPC style argument quoting service for AI agents.

Safely quote shell arguments for bash, zsh, fish, PowerShell, CMD, and MSYS2.

## Usage

```bash
# Create request
echo '{"shell":"bash","text":"foo'\''bar"}' > request.json

# Run safe-shell
python safe_shell.py @request.json

# Output
{"ok":true,"quoted":"'foo'\\''bar'","shell":"bash"}
```

For content with boundary characters, use base64 encoding:

```bash
echo '{"shell":"bash","encoding":"base64","text":"Zm9vJ2Jhcg=="}' > request.json
```

## Request Format

```json
{
  "shell": "bash",
  "text": "foo'bar"
}
```

## Response Format

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
| `shell` | Yes | string | Shell type |
| `text` | Yes | string | Text to quote |
| `encoding` | No | string | `base64` to decode first |

## Failure Classes

| failureClass | Meaning |
|--------------|---------|
| `INVALID_JSON` | JSON parse failed |
| `MISSING_REQUIRED_FIELD` | Required field missing |
| `UNSUPPORTED_ENCODING` | encoding not supported |
| `UNSUPPORTED_SHELL` | shell not in enum |
| `INVALID_ENCODING_DATA` | base64 decode failed |
| `INPUT_TOO_LARGE` | Input > 1 MiB |
| `UNQUOTABLE_CHARACTER` | Contains NUL |

## CMD Disclaimer

> **CMD support is best-effort.**
>
> Windows programs are free to implement their own argument parsing rules.
> `CommandLineToArgvW` is a convention, not a guarantee.

## Limits

- **MAX_INPUT_SIZE**: 1 MiB
- **Boundary**: Exactly ONE argument per request

## Related

- [safe-edit](https://github.com/Tan2237/safe-edit-skill) — Safe file editing for AI agents

## License

MIT