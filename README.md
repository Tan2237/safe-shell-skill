# safe-shell skill

面向 AI Agent 的单参数 Shell 引用服务。它把一段动态文本转换为目标 Shell
中“恰好一个字面参数”的源码片段，避免空格、引号、变量展开或反引号导致
参数被拆分、截断或意外解释。

如果执行接口本身接受 argv 数组，应直接把原始值作为一个数组元素传入，
不需要 safe-shell。只有必须拼接 Shell 源码字符串时才使用本项目。

## 这次从 safe-edit 吸收的架构

- 新增常驻的结构化 MCP 工具 `safe_shell_quote`，直接接收原始 `shell` 与
  `text`，无需临时请求文件、Base64 或重复启动 Python。
- 保留单文件 CLI，并新增 `--request-stdin` 与 `--request-base64` 整体请求
  传输；原有 `@request.json` 完全兼容。
- 新增零运行时依赖的 Python 包、稳定的 `safe-shell` / `safe-shell-mcp`
  命令入口与 Codex 插件清单。
- 新增 Claude Code、Cursor、OpenCode、VS Code 配置安装器；合并现有 JSON，
  修改前备份，跨文件失败时回滚已完成写入。
- 新增 MCP 协议、安装器和三种 CLI 传输的回归测试。

没有迁移 safe-edit 特有的文件锁、SHA-256 写入守卫、事务和文本匹配优化；
这些能力与只读、O(n) 的单参数引用热路径无关。

## 能力边界

safe-shell 只负责一个数据参数，不负责：

- 校验命令意图或阻止破坏性命令；
- 让 `eval`、`bash -c`、`sh -c`、`powershell -Command` 变安全；
- 引用整段脚本、管道、重定向、命令替换或其他 Shell 语法；
- 替代支持 argv 数组的进程执行 API。

返回值是“一个参数的 Shell 源码片段”，不是可受信任的命令代码。

## 支持范围

- bash、zsh、fish、PowerShell、CMD、MSYS2。
- bash/zsh/fish/MSYS2 使用单引号分段；PowerShell 使用单引号加倍。
- CMD 仅接受能维持通用字面量保证的字符子集。
- 支持 UTF-8、标准或 URL-safe Base64、1 MiB 参数大小限制。
- MSYS2 对 `/` 或 `=/path` 形式给出路径转换启发式警告。

## 安装

### 只安装 Skill / 单文件 CLI

```bash
npx skills add Tan2237/safe-shell-skill
# 全局安装
npx skills add Tan2237/safe-shell-skill -g
```

这一路径安装 `skills/safe-shell/`，不自动注册常驻 MCP。

### 安装 CLI 与 MCP 命令

项目无运行时第三方依赖：

```bash
pipx install git+https://github.com/Tan2237/safe-shell-skill.git
# 或
uv tool install git+https://github.com/Tan2237/safe-shell-skill.git
```

安装后提供：

- `safe-shell`：CLI 回退入口；
- `safe-shell-mcp`：常驻 stdio MCP 服务及跨客户端安装器。

```bash
safe-shell-mcp --version
safe-shell-mcp install --client all --scope project --project-dir . --dry-run
```

仓库还包含 `.codex-plugin/plugin.json` 与 `.mcp.json`，支持插件清单的
Codex 环境可以同时加载 skill 与常驻 MCP 服务。

## 结构化 MCP 快路径

工具调用：

```text
safe_shell_quote({"shell":"bash","text":"foo'bar"})
```

返回的 `structuredContent` 包含：

```json
{
  "ok": true,
  "quoted": "'foo'\\''bar'",
  "shell": "bash",
  "transport": "mcp-structured",
  "elapsedMs": 0.1
}
```

每个动态参数调用一次。把 `quoted` 直接放到最终命令的对应参数位置，不要
二次包引号，也不要先存入 Shell 变量再期望变量展开重新解析其中的引号。

常驻服务只导入一次引用内核；结构化请求不会再次 JSON 解码，也没有 Base64
约 33% 的体积膨胀。

## 跨客户端安装器

```bash
# 用户级；VS Code 需要 code CLI
safe-shell-mcp install --client all --scope user

# 项目级，适合团队配置
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

安装器只合并 `safe-shell` 条目。修改已有配置前创建 `.safe-shell.bak`
备份；无效 JSON/JSONC 会被拒绝且保持原文件不变。

## CLI 回退

优先使用执行工具的原生 stdin 字段，把下面的 JSON 作为 stdin 发送：

```json
{"shell":"bash","text":"foo'bar"}
```

命令：

```bash
python skills/safe-shell/safe_shell.py --request-stdin
```

没有原生 stdin 时，可对整个 UTF-8 JSON 请求做无填充 URL-safe Base64：

```bash
python skills/safe-shell/safe_shell.py --request-base64 B64
```

已有 UTF-8 请求文件继续支持：

```bash
python skills/safe-shell/safe_shell.py @request.json
```

不要为了调用工具而用 Shell 重定向临时拼出含动态内容的请求文件；那会把原本
需要解决的引用问题提前到工具调用之前。

### 请求

```json
{
  "shell": "bash",
  "text": "foo'bar"
}
```

CLI 还接受可选的 `encoding: "base64"`，用于只编码 `text` 字段；标准与
URL-safe、有填充与无填充形式都接受。结构化 MCP 调用不需要这个字段。

### 响应

成功：

```json
{"ok":true,"quoted":"'foo'\\''bar'","shell":"bash"}
```

失败：

```json
{"ok":false,"failureClass":"UNSUPPORTED_SHELL","message":"shell 'pwsh' is not supported"}
```

## Shell 规则

| Shell | 枚举值 | 引用方式 |
|---|---|---|
| Bash | `bash` | 单引号分段 |
| Zsh | `zsh` | 单引号分段 |
| Fish | `fish` | 单引号分段 |
| PowerShell | `powershell` | 单引号加倍 |
| CMD | `cmd` | 安全字符子集使用双引号 |
| MSYS2 | `msys2` | 单引号分段并提示路径转换 |

CMD 会拒绝 U+0022、`%`、`!`、CR 和 LF。这些字符经过 `cmd.exe` 的 Shell
层与目标程序参数层时不存在通用、可靠的字面量规则；失败后不要手工绕过。

MSYS2 警告只检测前导 `/` 和 `=/path` 形式，不保证覆盖所有路径转换。

## 错误类型与限制

错误类型包括 `INVALID_JSON`、`MISSING_REQUIRED_FIELD`、
`INVALID_FIELD_TYPE`、`UNSUPPORTED_SHELL`、`UNSUPPORTED_ENCODING`、
`INVALID_ENCODING_DATA`、`INPUT_TOO_LARGE`、`UNQUOTABLE_CHARACTER`、
`INTERNAL_ERROR`。

- 参数上限：解码后 1 MiB UTF-8；
- CLI 请求包上限：4 MiB；
- 每次请求：恰好一个参数。

## 测试

```bash
python -m py_compile skills/safe-shell/safe_shell.py
python -m pytest tests/ -v
ruff check mcp/ skills/ tests/
```

GitHub Actions 在 Windows 与 Linux 上验证 CLI、真实 Shell roundtrip、MCP
协议、包入口和安装器行为。

## 相关

- [safe-edit](https://github.com/Tan2237/safe-edit-skill) — 本次结构化 MCP、
  打包和安装器设计的来源。

## 许可证

MIT
