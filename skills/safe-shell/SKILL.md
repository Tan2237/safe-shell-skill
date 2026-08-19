---
name: safe-shell
description: |
  Quote one or an ordered batch of dynamic data arguments when an agent must
  compose shell source text for bash, zsh, fish, sh, dash, ksh, PowerShell,
  pwsh, CMD, or MSYS2. Prefer argv arrays, then the structured
  safe_shell_quote / safe_shell_quote_many MCP tools, then the sibling
  safe_shell.py CLI. Never use this skill for command code or shell syntax.
---

# safe-shell — Literal Data Arguments

Produce shell source fragments that each evaluate to exactly one literal data
argument. Keep command intent and shell syntax outside this service.

## Selection

1. If the execution API accepts an argv array, pass every raw value as one
   array element. Do not quote it.
2. If shell source is required and the MCP tools are callable:
   - call `safe_shell_quote` for one value;
   - call `safe_shell_quote_many` for 1..256 ordered values.
   This persistent structured path is the fastest safe-shell route. Batch all
   values for the same shell into one call.
3. Otherwise resolve the sibling `safe_shell.py` and use the first available
   no-disk CLI transport below.
4. If none of these paths is available, stop instead of quoting manually.

Never create a temporary request file or shared clipboard file merely to call
safe-shell. File creation adds I/O, races, and residual data without fixing the
original transport boundary.

The `shell` enum must equal the parser that will consume the final command. It
does not select or launch a shell. Select `pwsh` only for PowerShell 7.3+ when
native argv fidelity relies on `Standard` mode (or non-legacy `Windows` mode).

## Structured MCP Fast Path

One argument:

```text
safe_shell_quote({"shell":"bash","text":"foo'bar"})
```

Several independent arguments:

```text
safe_shell_quote_many({
  "shell": "bash",
  "texts": ["first", "two words", "foo'bar"]
})
```

For a successful batch, place `quoted[0]`, `quoted[1]`, and so on at their
separate argument positions. Never join the array into one quoted value or
treat it as command code.

Batch requests are atomic: `texts` must contain 1..256 strings whose combined
UTF-8 size is at most 1 MiB. If any item fails, the response has `ok: false`
and a zero-based `index`; it contains no partial `quoted` array. MSYS2 batch
warnings also include the affected `index`.

Use raw structured fields. Do not JSON-serialize or Base64-encode an MCP
request. TextContent mirrors the serialized JSON for compatibility, while
`structuredContent` carries the typed result.

The server supports modern MCP `2026-07-28` discovery and per-request metadata,
plus legacy initialize negotiation through `2025-11-25`, `2025-06-18`,
`2025-03-26`, and `2024-11-05`. This protocol detail does not change how the
quote tools are called.

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

1. Native stdin supplied in the process-start call:

   ```text
   python "SAFE_SHELL_SCRIPT" --request-stdin
   ```

   Send one UTF-8 JSON envelope through the execution tool's native stdin
   field.

2. For a small, non-sensitive request, URL-safe UTF-8 Base64 for the
   entire envelope:

   ```text
   python "SAFE_SHELL_SCRIPT" --request-base64 B64
   ```

   Prefer this one-process call when the compact envelope is at most 8 KiB and
   obtaining a writable session would add another wait or tool round trip.
   Base64 may appear in process listings or tool logs, expands the payload,
   and remains subject to argv limits, especially on Windows. Never use it
   for secrets or inputs near the service's 1 MiB data limit.

3. A writable stdin session for sensitive, larger, or argv-limited requests:

   ```text
   python "SAFE_SHELL_SCRIPT" --request-stdin-line
   ```

   Start a writable process session, then send exactly one compact JSON
   envelope followed by LF. This mode reads one frame and exits without
   waiting for EOF. Use it for execution tools that expose a later
   `write_stdin` operation. Do not send pretty-printed multiline JSON.

4. An already-existing UTF-8 JSON request file:

   ```text
   python "SAFE_SHELL_SCRIPT" @request.json
   ```

Never create a temporary request file, fixed mailbox, or shared clipboard file
merely to invoke safe-shell. Do not use shell redirection, a PowerShell
here-string, or a quoted dynamic payload to simulate native stdin.

The same CLI entry automatically dispatches single envelopes containing
`text` and batch envelopes containing `texts`. Only single requests accept the
optional `encoding: "base64"` field; batch requests require raw strings. CLI
stdout is always one UTF-8 JSON line, independent of the console code page and
`PYTHONIOENCODING`.

## Mandatory Boundary

- Quote one dynamic argument, or one ordered batch of independent arguments.
- Embed each returned fragment exactly where one data argument belongs.
- Treat output as shell source fragments, never as trusted executable code.
- Keep the selected shell equal to the final parser.
- Treat every `ok: false` response as a hard failure.
- For batch failures, do not use or invent results for any item.

## Forbidden

- Do not quote an entire command, script, pipeline, redirection, glob, command
  substitution, or control operator.
- Do not use output as the code operand of `eval`, `bash -c`, `sh -c`,
  `powershell -Command`, or an equivalent execute-string facility.
- Do not concatenate output where `|`, `&&`, `>`, or backticks are expected.
- Do not wrap returned fragments in another quoting layer.
- Do not normalize, substitute, or collapse Unicode quote characters in a
  returned fragment.
- Do not store fragments in a shell variable and expand it expecting the shell
  to parse embedded quotes again.
- Do not bypass CMD rejection with manual escaping.
- Do not bypass a `powershell` legacy-native rejection by adding quotes or
  backslashes. Use a raw argv API, or the actual `pwsh` 7.3+ parser.
- Do not create or reuse a request scratch file when structured MCP, native
  stdin, the small non-sensitive Base64 transport, or writable session stdin
  is available.

## Decision Tree

```text
Execution API accepts argv array?
  -> pass raw values as separate argv elements; do not use safe-shell

Need one dynamic data argument inside shell source?
  -> safe_shell_quote, or one CLI request

Need 2..256 dynamic data arguments for the same shell?
  -> safe_shell_quote_many, or one batch CLI request
  -> place returned elements separately and in order

Input is command code or shell syntax?
  -> do not use safe-shell
```

## Requests and Responses

Single request:

```json
{"shell":"bash","text":"foo'bar"}
```

Single success:

```json
{"ok":true,"quoted":"'foo'\\''bar'","shell":"bash"}
```

Batch request:

```json
{"shell":"pwsh","texts":["one","two words","$env:HOME"]}
```

Batch success:

```json
{
  "ok": true,
  "shell": "pwsh",
  "count": 3,
  "quoted": ["'one'", "'two words'", "'$env:HOME'"]
}
```

Batch failure:

```json
{
  "ok": false,
  "failureClass": "UNQUOTABLE_CHARACTER",
  "message": "cmd cannot safely quote character(s): '%'",
  "index": 1
}
```

Supported shell enum values: `bash`, `zsh`, `fish`, `sh`, `dash`, `ksh`,
`powershell`, `pwsh`, `cmd`, and `msys2`.

## Shell-Specific Rules

- bash, zsh, fish, sh, dash, ksh, and MSYS2: single-quote the value and
  reopen around literal single quotes.
- PowerShell and pwsh: wrap the value in ASCII U+0027. Double every
  embedded U+0027, U+2018, U+2019, U+201A, or U+201B using that same code
  point, because PowerShell treats all five as single-quote delimiters. Keep
  other quote lookalikes literal; do not normalize or substitute them.
- `powershell`: reject the empty string, any value containing U+0022, and any
  value that both contains Unicode whitespace and ends in a backslash. Windows
  PowerShell legacy native argument passing cannot preserve those values.
- `pwsh`: exact native argv for those values requires PowerShell 7.3+
  `Standard` mode or a `Windows`-mode target that does not fall back to legacy.
- CMD: double-quote accepted values using the MS C runtime convention. Reject
  U+0022, `%`, `!`, CR, and LF.
- MSYS2: inspect `MSYS2_PATH_CONVERSION` warnings for leading `/` or `=/path`.
  Warning absence is not a guarantee that conversion will not occur.

## Failure Classes

`INVALID_JSON`, `MISSING_REQUIRED_FIELD`, `UNKNOWN_FIELD`,
`INVALID_FIELD_TYPE`, `INVALID_FIELD_VALUE`, `UNSUPPORTED_SHELL`,
`UNSUPPORTED_ENCODING`, `INVALID_ENCODING_DATA`, `INPUT_TOO_LARGE`,
`UNQUOTABLE_CHARACTER`, and `INTERNAL_ERROR`.

## Limits

- Single decoded argument: 1 MiB of UTF-8.
- Batch: 1..256 strings, combined UTF-8 size at most 1 MiB.
- CLI request envelope: 4 MiB.
- Base64 command-line transport: only small requests; platform argv limits
  apply before safe-shell starts.
