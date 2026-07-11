# safe-shell skill

通用的 Shell 参数引用 Agent Skill。将任意文本稳定转换为恰好一个 Shell 参数，避免 quoting / escaping 错误导致参数被错误拆分、截断或被 Shell 意外解释。

适合 Agent 拼接 Shell 命令、传递用户输入到命令行、处理含特殊字符（引号、空格、`$`、反引号等）的参数，以及任何不希望被 Shell 静默篡改或注入的场景。

## safe-shell 不做什么

safe-shell **不**提供以下保证：

- 不校验命令意图，不阻止破坏性命令
- 不能让 `eval` 变安全
- 不能让整段 shell 脚本变安全
- 不能净化传给 `bash -c` / `sh -c` / `powershell -Command` 的 shell 代码
- 不处理 shell 语法（管道、`&&`、重定向、反引号）

safe-shell 只负责把**一个数据参数**正确引用为字面量。

## 特性

- 单文件 Python 标准库实现，Windows/Linux/macOS 通用。
- 支持 bash、zsh、fish、PowerShell、CMD、MSYS2 六种 Shell。
- 自动选择引用规则：bash/zsh/fish/msys2 用单引号转义，PowerShell 用单引号加倍；CMD 仅接受可被可靠引用的安全字符子集。
- 支持 base64 编码输入，避免请求文件本身的引用问题。
- 结构化 JSON 输出，包含成功/失败状态、错误类型分类。
- 严格的输入校验：检查必填字段、Shell 类型、UTF-8 合法性、输入大小（最大 1 MiB）、NUL 与 CMD 不可安全引用字符。
- MSYS2 路径转换警告：提醒用户 MSYS2 可能转换 `/` 开头的路径。

## 安装

使用支持 `skills` 生态的安装器：

```bash
npx skills add Tan2237/safe-shell-skill
```

全局安装：

```bash
npx skills add Tan2237/safe-shell-skill -g
```

指定 Agent：

```bash
npx skills add Tan2237/safe-shell-skill -a opencode
npx skills add Tan2237/safe-shell-skill -a claude-code
```

仓库中的 skill 位于：

```text
skills/safe-shell/
  SKILL.md
  safe_shell.py
```

## 为什么需要这个工具

直接拼接 Shell 命令是危险的：

```bash
# 用户输入
USER_INPUT="foo'bar"

# 错误做法：直接拼接，未引用
filename=$USER_INPUT
cat $filename  # word splitting + glob expansion

# 更危险：传入执行上下文
bash -c "echo $USER_INPUT"  # $USER_INPUT 会被重新解析
eval "$USER_INPUT"  # 直接执行用户输入

# 正确做法：引用后直接内联到命令中（safe-shell 生成此形式）
cat 'foo'\''bar'

# 注意：shell 不会重新解析变量展开中的引号
# QUOTED="'foo'\''bar'"; cat $QUOTED   ← 错误！cat 会去找名为 'foo'\''bar' 的文件
```

不同 Shell 的引用规则不同：

| Shell | 引号内特殊字符 | 转义方式 |
|-------|---------------|---------|
| bash/zsh/fish | `'` | `'foo'\''bar'`（关闭引号、转义引号、重新打开） |
| PowerShell | `'` | `'foo''bar'`（单引号加倍） |
| CMD | 安全字符子集 | 双引号包裹；拒绝 U+0022、`%`、`!`、CR、LF |

手动处理容易出错，safe-shell 自动应用正确的规则。

## 为什么不直接用 shlex.quote

`shlex.quote` 只覆盖 POSIX 系 shell（bash/zsh），且不提示各 Shell 的特有陷阱。safe-shell 在此基础上额外提供：

- 跨 Shell 统一接口：bash、zsh、fish、PowerShell、CMD、MSYS2 共用同一套 JSON 协议。
- Shell 专属处理：提示 MSYS2 路径转换；拒绝 CMD 中无法通用、安全引用的字符。
- 结构化 JSON 输入输出，便于 Agent 通过文件调用，避免请求本身的引用问题（支持 base64）。
- 与模型行为解耦的确定性引用，不依赖 LLM 对引用规则的记忆。

## 基本用法

```bash
# 创建请求文件
echo '{"shell":"bash","text":"foo'\''bar"}' > request.json

# 运行 safe-shell
python safe_shell.py @request.json

# 输出
{"ok":true,"quoted":"'foo'\\''bar'","shell":"bash"}
```

含边界字符的内容使用 base64 编码：

```bash
echo '{"shell":"bash","encoding":"base64","text":"Zm9vJ2Jhcg=="}' > request.json
python safe_shell.py @request.json
```

## 请求格式

```json
{
  "shell": "bash",
  "text": "foo'bar"
}
```

| 字段 | 必填 | 类型 | 说明 |
|------|------|------|------|
| `shell` | 是 | string | Shell 类型 |
| `text` | 是 | string | 待引用文本 |
| `encoding` | 否 | string | `base64` 先解码 |

## 响应格式

成功：

```json
{
  "ok": true,
  "quoted": "'foo'\\''bar'",
  "shell": "bash"
}
```

失败：

```json
{
  "ok": false,
  "failureClass": "UNSUPPORTED_SHELL",
  "message": "shell 'pwsh' is not supported"
}
```

带警告（MSYS2 路径转换提醒）：

```json
{
  "ok": true,
  "quoted": "'/usr/local'",
  "shell": "msys2",
  "warnings": [
    {
      "code": "MSYS2_PATH_CONVERSION",
      "message": "MSYS2 may convert paths starting with /"
    }
  ]
}
```

## Shell 类型

| Shell | 枚举值 | 引用方式 |
|-------|--------|---------|
| Bash | `bash` | 单引号转义 |
| Zsh | `zsh` | 单引号转义 |
| Fish | `fish` | 单引号转义 |
| PowerShell | `powershell` | 单引号加倍 |
| CMD | `cmd` | 安全字符子集使用双引号；危险字符返回失败 |
| MSYS2 | `msys2` | 单引号转义 |

## 错误类型

| failureClass | 含义 |
|--------------|------|
| `INVALID_JSON` | JSON 解析失败 |
| `MISSING_REQUIRED_FIELD` | 缺少必填字段 |
| `UNSUPPORTED_ENCODING` | encoding 不支持 |
| `UNSUPPORTED_SHELL` | shell 不在枚举中 |
| `INVALID_FIELD_TYPE` | 字段类型错误 |
| `INVALID_ENCODING_DATA` | base64 解码失败 |
| `INPUT_TOO_LARGE` | 输入超过 1 MiB |
| `UNQUOTABLE_CHARACTER` | 含 NUL、未配对 surrogate 或 CMD 不可安全引用字符 |
| `INTERNAL_ERROR` | 内部意外错误 |

## MSYS2 路径警告

MSYS2 路径转换警告是**启发式**的：

- 检测到以 `/` 开头的文本（如 `/usr/bin`），或 `=/路径` 形式的选项值（如 `--mount=/tmp/foo`）时触发
- 不保证检测到所有会被转换的情况（例如路径出现在参数中间）
- **无警告不等于安全**

如需精确控制，请查阅 MSYS2 文档了解 Cygwin 路径转换规则。

## CMD 说明

CMD 存在 shell 解析和目标程序参数解析两层规则，没有一种通用转义能可靠覆盖所有字符。为维持“恰好一个字面参数”的承诺，CMD 模式会拒绝以下输入：

- U+0022（双引号）
- `%`（环境变量展开）
- `!`（延迟变量展开）
- CR 和 LF（命令分隔风险）

这些输入返回 `UNQUOTABLE_CHARACTER`，不会返回带警告的成功结果。其余输入仍按 `CommandLineToArgvW` 约定引用；目标程序若使用自定义解析器，仍需单独验证。

## 限制

- **MAX_INPUT_SIZE**: 1 MiB
- **边界**: 每次请求仅一个参数

## 测试

```bash
python -m py_compile skills/safe-shell/safe_shell.py
python -m unittest discover -s . -v
```

GitHub Actions 会在 Windows、Linux 上运行测试。

## 相关

- [safe-edit](https://github.com/Tan2237/safe-edit-skill) — AI Agent 专用安全文件编辑

## 许可证

MIT