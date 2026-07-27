---
name: safe-shell
description: |
  Quote exactly one dynamic data argument when an agent must compose shell
  source text for bash, zsh, fish, PowerShell, CMD, or MSYS2. Prefer the
  structured safe_shell_quote MCP tool when callable; otherwise use the
  sibling safe_shell.py CLI through stdin, Base64, or an existing request
  file. Do not use this skill when an execution API accepts an argv array,
  or to quote scripts, pipelines, redirections, eval input, or command code.
---

# safe-shell — One-Argument Quoting

Produce a shell source fragment that evaluates to exactly one literal data
argument. Keep command intent and shell syntax outside this service.

## Selection

1. If the execution API accepts an argv array, pass the raw argument as one
   array element. Do not quote it.
2. If `safe_shell_quote` is callable and shell source text is required, call
   it with raw structured `shell` and `text` fields.
3. Otherwise resolve the sibling `safe_shell.py` and use a CLI transport below.
4. If none of these paths is available, stop instead of quoting manually.

## Structured MCP Fast Path

Call one tool invocation per dynamic argument:

```text
safe_shell_quote({"shell":"bash","text":"foo'bar"})
```

Use the returned `quoted` value inline at the exact argument position. The MCP
path accepts raw text, avoids temporary request files and Base64 expansion, and
reuses one long-lived Python process.

Do not serialize or Base64-encode an already structured MCP request.

## CLI Fallback Resolution

Resolve `SAFE_SHELL_SCRIPT` once before the first CLI call:

1. Start from the absolute path of this `SKILL.md` supplied by the loader.
2. Select the sibling `safe_shell.py`.
3. Convert it to an absolute path and verify that it exists.
4. Reuse that exact path for the task.

`SAFE_SHELL_SCRIPT` below is a placeholder, not an environment variable. Never
resolve it from the current directory, target argument, or request file. If the
sibling script is missing, stop and report both expected paths.

Use CLI transports in this order:

1. Native execution-tool stdin:

   ```text
   python "SAFE_SHELL_SCRIPT" --request-stdin
   ```

   Send the JSON request through the execution tool's native stdin field.

2. URL-safe UTF-8 Base64 for the entire JSON envelope:

   ```text
   python "SAFE_SHELL_SCRIPT" --request-base64 B64
   ```

3. An existing UTF-8 JSON request file:

   ```text
   python "SAFE_SHELL_SCRIPT" @request.json
   ```

Do not create a request file with shell redirection merely to invoke safe-shell.
Do not put a PowerShell here-string or quoted dynamic payload in the command to
simulate native stdin; that still relies on the quoting being solved.

## Mandatory Boundary

- Quote exactly one dynamic argument per request.
- Embed `quoted` directly where one data argument belongs.
- Treat output as a shell source fragment for one argument, never as trusted
  executable code.
- Keep the selected shell equal to the shell that will parse the final command.
- Treat any `ok: false` response as a hard failure.

## Forbidden

- Do not quote an entire command, script, pipeline, redirection, glob, command
  substitution, or control operator.
- Do not pass output as the code operand of `eval`, `bash -c`, `sh -c`,
  `powershell -Command`, or an equivalent execute-string facility.
- Do not concatenate output where shell syntax such as `|`, `&&`, `>`, or
  backticks is expected.
- Do not wrap the returned fragment in another quoting layer.
- Do not store the fragment in a shell variable and expand it expecting the
  shell to parse embedded quotes again.
- Do not bypass CMD rejection with manual escaping.

## Decision Tree

```text
Execution API accepts argv array?
  -> pass raw data as one argv element; do not use safe-shell

Need one dynamic data argument inside shell source text?
  -> call safe_shell_quote, or the CLI fallback once

Need several dynamic arguments?
  -> call once per argument and place each result separately

Input is command code or shell syntax?
  -> do not use safe-shell
```

## Request and Response

Request:

```json
{"shell":"bash","text":"foo'bar"}
```

The optional CLI-only `encoding: "base64"` field decodes the `text` value as
padded or unpadded standard/URL-safe Base64 before quoting. It is unnecessary
for structured MCP calls.

Success:

```json
{"ok":true,"quoted":"'foo'\\''bar'","shell":"bash"}
```

Failure:

```json
{"ok":false,"failureClass":"UNSUPPORTED_SHELL","message":"shell 'pwsh' is not supported"}
```

Supported shell enum values: `bash`, `zsh`, `fish`, `powershell`, `cmd`, and
`msys2`.

## Shell-Specific Rules

- bash, zsh, fish, MSYS2: single-quote the value and reopen around literal
  single quotes.
- PowerShell: single-quote the value and double embedded single quotes.
- CMD: always double-quote accepted values using the MS C runtime convention.
  Reject U+0022, `%`, `!`, CR, and LF because no general quoting rule preserves
  them through all `cmd.exe` parsing layers.
- MSYS2: inspect the `MSYS2_PATH_CONVERSION` warning for leading `/` or `=/path`
  patterns. The warning is heuristic; absence of a warning is not a guarantee.

## Failure Classes

`INVALID_JSON`, `MISSING_REQUIRED_FIELD`, `INVALID_FIELD_TYPE`,
`UNSUPPORTED_SHELL`, `UNSUPPORTED_ENCODING`, `INVALID_ENCODING_DATA`,
`INPUT_TOO_LARGE`, `UNQUOTABLE_CHARACTER`, and `INTERNAL_ERROR`.

## Limits

- Decoded argument: 1 MiB of UTF-8.
- CLI request envelope: 4 MiB.
- Boundary: exactly one argument per request.
