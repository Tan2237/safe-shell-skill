# safe-shell skill

面向 AI Agent 的 Shell 参数引用服务。它把一段动态文本，或一组有序动态
文本，转换为目标 Shell 中独立的字面参数源码片段，避免空格、引号、变量
展开或反引号导致参数被拆分、截断或意外解释。

如果执行接口接受 argv 数组，应直接把每个原始值作为一个数组元素传入，
不需要 safe-shell。只有必须拼接 Shell 源码字符串时才使用本项目。

## 能力边界

safe-shell 只负责数据参数，不负责：

- 校验命令意图或阻止破坏性命令；
- 让 `eval`、`bash -c`、`sh -c`、`powershell -Command` 变安全；
- 引用整段脚本、管道、重定向、命令替换或其他 Shell 语法；
- 替代支持 argv 数组的进程执行 API。

返回值是“一个参数的 Shell 源码片段”，或按输入顺序排列的这类片段数组，
不是可受信任的命令代码。批量调用不会把多个参数拼成一段命令。

## 支持范围

| Shell | 枚举值 | 引用方式 |
|---|---|---|
| Bash | `bash` | POSIX 风格单引号分段 |
| Zsh | `zsh` | POSIX 风格单引号分段 |
| Fish | `fish` | 单引号分段 |
| POSIX sh | `sh` | POSIX 风格单引号分段 |
| Dash | `dash` | POSIX 风格单引号分段 |
| KornShell | `ksh` | POSIX 风格单引号分段 |
| Windows PowerShell | `powershell` | 单引号定界；拒绝 legacy native 无法保真的值 |
| PowerShell 7.3+ | `pwsh` | 单引号定界；5 种定界字符同字双写 |
| CMD | `cmd` | 安全字符子集使用双引号 |
| MSYS2 | `msys2` | 单引号分段，并提示路径转换 |

枚举值必须与最终解析命令的 Shell 一致；它不会选择或启动该 Shell。

- 单条请求：恰好一个参数，解码后 UTF-8 最大 1 MiB。
- 批量请求：`texts` 包含 1..256 项，全部 UTF-8 字节合计最大 1 MiB。
- CLI 请求 envelope 最大 4 MiB。
- 单条 CLI 请求可用标准或 URL-safe Base64 编码 `text`。
- 批量请求不接受 `encoding`，每项必须是原始字符串。
- MSYS2 对前导 `/` 或包含 `=/path` 的值给出启发式警告。
- `powershell` 拒绝 legacy native 参数层无法原样保留的空字符串、含
  U+0022 的值，以及“含 Unicode 空白且以反斜杠结尾”的值。
- `pwsh` 的完整 native argv 保真边界是 PowerShell 7.3+ 的
  `Standard` 模式，或未回退到 legacy 的 `Windows` 模式。

## 安装

### 只安装 Skill / 单文件 CLI

```bash
npx skills add Tan2237/safe-shell-skill
# 全局安装
npx skills add Tan2237/safe-shell-skill -g
```

这一路径安装 `skills/safe-shell/`，不自动注册常驻 MCP。CLI 需要
Python 3.9 或更高版本。

### 安装 CLI 与 MCP 命令

项目无运行时第三方依赖：

```bash
pipx install git+https://github.com/Tan2237/safe-shell-skill.git
# 或
uv tool install git+https://github.com/Tan2237/safe-shell-skill.git
```

安装后提供：

- `safe-shell`：CLI 回退入口；
- `safe-shell-mcp`：stdio MCP 服务与跨客户端配置管理入口。

```bash
safe-shell-mcp --version
safe-shell-mcp install --client all --scope project --project-dir . --dry-run
safe-shell-mcp status --client all --scope project --project-dir . --json
safe-shell-mcp doctor --timeout 10 --json
```

仓库还包含 `.codex-plugin/plugin.json` 与 `.mcp.json`。支持插件清单的
Codex 环境可以同时加载 skill 与常驻 MCP 服务。

## 选择调用路径

1. 执行 API 接受 argv 数组时，直接传原始数组元素。
2. 必须生成 Shell 源码且 `safe_shell_quote` / `safe_shell_quote_many`
   可调用时，使用结构化 MCP。
3. 否则使用 CLI 的原生 stdin。
4. 小请求且没有 stdin 时，可使用整个 JSON envelope 的 URL-safe Base64。
5. 大请求没有 stdin 时，只能使用已经存在的 UTF-8 请求文件；不要用 Shell
   重定向或动态 here-string 临时创建它。

每个返回的 `quoted` 元素只能放到一个数据参数的位置，不要二次包引号。

## 结构化 MCP

### 单个参数

```text
safe_shell_quote({"shell":"bash","text":"foo'bar"})
```

成功的 `structuredContent`：

```json
{
  "ok": true,
  "quoted": "'foo'\\''bar'",
  "shell": "bash",
  "transport": "mcp-structured",
  "elapsedMs": 0.1
}
```

### 多个参数

```text
safe_shell_quote_many({
  "shell": "bash",
  "texts": ["first", "two words", "foo'bar"]
})
```

成功：

```json
{
  "ok": true,
  "shell": "bash",
  "count": 3,
  "quoted": ["'first'", "'two words'", "'foo'\\''bar'"],
  "transport": "mcp-structured",
  "elapsedMs": 0.1
}
```

批量请求先验证全部输入，再生成结果。任意一项失败时返回 `ok: false` 和
零基 `index`，不返回部分 `quoted`。MSYS2 批量警告也带对应的 `index`。

MCP 的 TextContent 同步包含序列化 JSON，兼容不读取
`structuredContent` 的客户端；工具清单还提供 output schema。

### MCP 协议版本

服务支持现代 `2026-07-28` 协议的 `server/discover`、逐请求 `_meta`、
`resultType: "complete"`、server info 与可缓存的工具清单，同时保留
`2025-11-25`、`2025-06-18`、`2025-03-26` 和 `2024-11-05` 的 legacy
`initialize` 握手。客户端应优先协商现代协议，并在需要时回退到 legacy。

## CLI 回退

优先通过执行工具的原生 stdin 发送 UTF-8 JSON：

```json
{"shell":"bash","text":"foo'bar"}
```

```bash
python skills/safe-shell/safe_shell.py --request-stdin
# 安装包后也可用：
safe-shell --request-stdin
```

批量 envelope 通过同一入口自动分派：

```json
{"shell":"pwsh","texts":["one","two words","$env:HOME"]}
```

### Base64 只适合小请求

没有原生 stdin 时，可对整个 UTF-8 JSON envelope 做无填充 URL-safe
Base64：

```bash
safe-shell --request-base64 B64
```

Base64 受操作系统 argv 限制。Windows 的进程命令行通常远小于本项目的
1 MiB 数据上限，因此不要把 1 MiB / 4 MiB 服务上限理解成 Base64
命令行可达上限。较大请求必须走原生 stdin 或已有文件：

```bash
safe-shell @request.json
```

不要为了调用工具而用 Shell 重定向、PowerShell here-string 或含动态值的
命令临时拼请求文件；那会把待解决的引用问题提前到调用之前。

单条 CLI 请求还接受可选的 `encoding: "base64"`，仅编码 `text` 字段；
标准与 URL-safe、有填充与无填充形式都接受。批量 `texts` 不支持该字段。

### CLI 响应与退出码

单条成功：

```json
{"ok":true,"quoted":"'foo'\\''bar'","shell":"bash"}
```

批量失败示例：

```json
{
  "ok": false,
  "failureClass": "UNQUOTABLE_CHARACTER",
  "message": "cmd cannot safely quote character(s): '%'",
  "index": 1
}
```

CLI 始终向 stdout 写入一行 UTF-8 JSON，不依赖终端默认代码页或
`PYTHONIOENCODING`。成功退出码为 0，校验或处理失败为 1；用法错误由
命令行解析器报告。

## 客户端配置生命周期

### 安装

```bash
# 用户级；VS Code 需要 code CLI
safe-shell-mcp install --client all --scope user

# 项目级
safe-shell-mcp install --client all --scope project --project-dir .

# 先预览
safe-shell-mcp install --client cursor --scope user --dry-run --json
```

| 客户端 | 用户级 | 项目级 |
|---|---|---|
| Claude Code | `~/.claude.json` | `.mcp.json` |
| Cursor | `~/.cursor/mcp.json` | `.cursor/mcp.json` |
| OpenCode | `~/.config/opencode/opencode.json` | `opencode.json` |
| VS Code | `code --add-mcp` | `.vscode/mcp.json` |

### 状态、诊断与卸载

```bash
safe-shell-mcp status --client all --scope project --project-dir . --json
safe-shell-mcp doctor --timeout 10 --json
safe-shell-mcp uninstall --client cursor --scope project \
  --project-dir . --dry-run --json
safe-shell-mcp uninstall --client cursor --scope project --project-dir .
```

- `status` 只读检查配置中是否存在 `safe-shell` 条目。VS Code 用户级状态
  无安全、稳定的 CLI 查询方式，因此返回 unknown。
- `doctor` 实际启动 stdio server，在超时内执行 legacy `initialize` 与
  `tools/list` 探测，并返回结构化协议、server info 和工具清单。
- `uninstall` 只移除 `safe-shell` 条目，保留同一文件的其他 server 与配置。
- VS Code `code` CLI 没有安全的 remove-MCP 操作，因此用户级 uninstall
  会在任何文件写入前失败；请在 VS Code 中显式移除。
- `install` 与 `uninstall` 均支持 `--dry-run`、`--json`、修改前备份和失败
  回滚。

安装器只接受严格 UTF-8 JSON（可带 BOM），不解析注释或尾逗号形式的
JSONC。写入前与回滚前都会比较规划快照，已发现并发修改时中止，以降低覆盖
外部更新的风险。快照比较与最终 `os.replace` / `unlink` 不是同一原子操作，
因此仍存在极窄的 TOCTOU 窗口。任何回滚失败都会列出路径、原因和可用
backup，而不是声称已经恢复。

## Shell 规则

POSIX 风格 Shell 使用单引号分段。PowerShell / pwsh 以 ASCII
U+0027 包住字符串；PowerShell 会把 U+0027、U+2018、U+2019、U+201A 和
U+201B 都识别为单引号定界符，因此每次出现时必须用相同码点双写。其他
相似引号保持原字面值，不做 Unicode 归一化或替换。调用方必须原样使用返回
片段；任何后处理都可能重新打开字符串边界。

Windows PowerShell 5.1 会在解析源码之后，用 legacy marshaller 把字符串
重新拼成 native 命令行；该层无法原样保留空字符串、含 U+0022 的值，以及
含 Unicode 空白且以反斜杠结尾的值。因此 `powershell` 对这些输入返回
`UNQUOTABLE_CHARACTER`。不要手工补反斜杠：那会改变传给 cmdlet 或 function
的字符串。应改用 argv 数组 API，或在最终解析器确为 PowerShell 7.3+ 时选择
`pwsh`。即使使用 `pwsh`，`Windows` 模式也会为部分程序和 `.bat` / `.cmd`
回退到 legacy；完整规则见
[Microsoft about_Parsing](https://learn.microsoft.com/en-us/powershell/module/microsoft.powershell.core/about/about_parsing?view=powershell-7.6#passing-arguments-that-contain-quote-characters)。

CMD 会拒绝 U+0022、`%`、`!`、CR 和 LF。这些字符经过 `cmd.exe` Shell
层与目标程序参数层时不存在通用、可靠的字面量规则；失败后不要手工绕过。

MSYS2 警告只检测前导 `/` 和 `=/path` 形式，不保证覆盖全部路径转换。
出现警告时，应评估 `MSYS2_ARG_CONV_EXCL` 或改用原生 Windows 路径。

## 错误类型

错误类型包括：

- `INVALID_JSON`
- `MISSING_REQUIRED_FIELD`
- `UNKNOWN_FIELD`
- `INVALID_FIELD_TYPE`
- `INVALID_FIELD_VALUE`
- `UNSUPPORTED_SHELL`
- `UNSUPPORTED_ENCODING`
- `INVALID_ENCODING_DATA`
- `INPUT_TOO_LARGE`
- `UNQUOTABLE_CHARACTER`
- `INTERNAL_ERROR`

批量项目相关失败可额外返回零基 `index`。错误响应不会包含部分引用结果。

## 开发与发布检查

```bash
python -m pip install ".[dev]"
python -m compileall -q mcp/ skills/
python -m pytest tests/ -v
python -m ruff check mcp/ skills/ tests/ --no-cache
python -m build
python -m twine check dist/*
```

CI 在 Python 3.9 与最新 Python、Windows 与 Linux 上运行测试；Linux 安装
zsh、fish、ksh，Windows 安装 MSYS2，以执行可用 Shell 的真实往返测试。
发布任务构建 wheel 与 sdist，并在干净虚拟环境中 smoke 两个 console
entry、MCP doctor 和安装器 dry-run。

Python 包版本以 `safe_shell_mcp.__version__` 为唯一 Python metadata
来源，并由测试检查插件版本、入口点和 marketplace 清单一致性。

## 相关

- [safe-edit](https://github.com/Tan2237/safe-edit-skill) — 结构化 MCP、
  guarded write、打包和客户端安装器设计的来源。

## 许可证

MIT
