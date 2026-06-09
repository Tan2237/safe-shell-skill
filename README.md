# safe-shell skill

通用的 Shell 参数引用 Agent Skill。安全地为 bash、zsh、fish、PowerShell、CMD、MSYS2 引用文本参数，避免特殊字符被 Shell 解释执行。

适合 Agent 拼接 Shell 命令、传递用户输入到命令行、处理含特殊字符（引号、空格、`$`、反引号等）的参数，以及任何不希望被 Shell 静默篡改或注入的场景。

## 特性

- 单文件 Python 标准库实现，Windows/Linux/macOS 通用。
- 支持 bash、zsh、fish、PowerShell、CMD、MSYS2 六种 Shell。
- 自动选择正确的引用规则：bash/zsh/fish/msys2 用单引号转义，PowerShell 用单引号加倍，CMD 用双引号加反斜杠转义。
- 支持 base64 编码输入，避免请求文件本身的引用问题。
- 结构化 JSON 输出，包含成功/失败状态、错误类型分类。
- 严格的输入校验：检查必填字段、Shell 类型、输入大小（最大 1 MiB）、NUL 字符。
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
USER_INPUT="foo'bar; rm -rf /"

# 错误做法：直接拼接
echo $USER_INPUT  # 可能被执行

# 正确做法：先引用
QUOTED="'foo'\''bar; rm -rf /'"
echo $QUOTED  # 安全地作为字面量输出
```

不同 Shell 的引用规则不同：

| Shell | 引号内特殊字符 | 转义方式 |
|-------|---------------|---------|
| bash/zsh/fish | `'` | `'foo'\''bar'`（关闭引号、转义引号、重新打开） |
| PowerShell | `'` | `'foo''bar'`（单引号加倍） |
| CMD | `"` `\` | `"foo\"bar"`（反斜杠转义，尾部反斜杠要加倍） |

手动处理容易出错，safe-shell 自动应用正确的规则。

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
| CMD | `cmd` | 双引号 + 反斜杠转义 |
| MSYS2 | `msys2` | 单引号转义 |

## 错误类型

| failureClass | 含义 |
|--------------|------|
| `INVALID_JSON` | JSON 解析失败 |
| `MISSING_REQUIRED_FIELD` | 缺少必填字段 |
| `UNSUPPORTED_ENCODING` | encoding 不支持 |
| `UNSUPPORTED_SHELL` | shell 不在枚举中 |
| `INVALID_ENCODING_DATA` | base64 解码失败 |
| `INPUT_TOO_LARGE` | 输入超过 1 MiB |
| `UNQUOTABLE_CHARACTER` | 含 NUL 字符 |

## CMD 说明

> **CMD 支持是尽力而为。**
>
> Windows 程序可以自定义参数解析规则。`CommandLineToArgvW` 是约定，不是保证。

## 限制

- **MAX_INPUT_SIZE**: 1 MiB
- **边界**: 每次请求仅一个参数

## 测试

```bash
python -m py_compile skills/safe-shell/safe_shell.py
python -m unittest discover -s tests -v
```

GitHub Actions 会在 Windows、Linux 上运行测试。

## 相关

- [safe-edit](https://github.com/Tan2237/safe-edit-skill) — AI Agent 专用安全文件编辑

## 许可证

MIT