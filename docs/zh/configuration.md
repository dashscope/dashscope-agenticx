---
description: "AgenticX 分层配置：全局与工作区 config.toml、环境变量、Provider 与 API Key 管理。"
---
# 配置

AgenticX 使用**分层配置**：

- **全局** — 平台标准的 `AgenticX` 应用目录
  （Linux 上为 `~/.config/AgenticX`；任何平台上均可用 `AGENTICX_HOME`
  覆盖）：各 Provider 的 API Key 槽位、语音/TTS 设置，跨项目共享。
- **工作区** — `./.agenticx/config.toml`：Provider、模型、平台能力、
  MCP 服务、用户名。

优先级：**工作区配置 > 全局配置 > 环境变量（仅填充空槽位）>
交互式输入**。`AGENTICX_PROVIDER` 与 `AGENTICX_MODEL` 始终强制覆盖。
同一套解析逻辑也以编程方式暴露为
[`Config.load()`](reference/sdk.md#config)。

## 工作区配置

```toml
# ./.agenticx/config.toml
user_name = "agenticx"
provider = "tongyi"
model = "qwen3.8-flash"
fallback_providers = "openai,anthropic"   # tried in order on primary failure
enabled_capabilities = "bailian.mcp,bailian.cli"
max_turns = 100                  # tool-call turns per conversation (default 50)
session_persist = true           # cross-session history persistence
tui = false                      # false = inline CLI REPL instead of TUI
user_directives = ["Don't push after each commit"]   # written by /rule
delegation = { max_concurrent = 5, default_timeout = 120 }

[[mcp_servers]]
service = "time"
```

包含类型与默认值的完整字段列表见
[Config 参考](reference/sdk.md#config)。

## 全局配置与 API Key { #global-config-api-keys }

每个 Provider 都有独立的密钥槽位，因此切换 Provider 绝不会覆盖
其他密钥。新的密钥存储在操作系统 keyring 中，服务名为
`dashscope-agenticx`；TOML 中只保存引用：

```toml
# ~/.config/AgenticX/config.toml
tongyi_api_key    = "KEYRING:v1:..."
anthropic_api_key = "KEYRING:v1:..."
openai_api_key    = "KEYRING:v1:..."
```

`/provider` 向导以无终端回显的方式输入密钥，并提供三种来源：
操作系统 keyring、环境变量，或稍后配置。旧版单一 `api_key` 行会在
首次启动时迁移到当前 Provider 的槽位；旧版 `ENC:` 值仍可读取以便
迁移，但新密钥绝不会以此格式写入。

## 环境变量

API Key 类变量仅填充空槽位；`AGENTICX_PROVIDER` /
`AGENTICX_MODEL` 始终覆盖。

| 变量 | 说明 |
|---|---|
| `DASHSCOPE_API_KEY` | Tongyi / Bailian API Key |
| `ANTHROPIC_API_KEY` | Claude API Key |
| `OPENAI_API_KEY` | OpenAI API Key |
| `OPENAI_BASE_URL` | 自定义 OpenAI 兼容端点（优先级最高） |
| `AGENTICX_PROVIDER` | 强制指定 Provider |
| `AGENTICX_MODEL` | 强制指定模型 |
| `AGENTICX_MCP_SERVERS` | 预连接的 MCP 服务（逗号分隔） |
| `AGENTICX_MEMORY_USER_ID` | 用于画像隔离的用户标识 |
| `AGENTICX_MEMORY_LIBRARY_ID` | 百炼画像库 ID |
| `AGENTICX_COMMAND_TIMEOUT` | `run_command` 超时秒数（默认 600） |
| `AGENTICX_HOME` | 覆盖全局配置目录 |

## 内置 Provider

| Provider | 协议 | 默认模型 |
|---|---|---|
| `tongyi` | DashScope 原生 | `qwen3.8-flash` |
| `anthropic` | Anthropic Messages | `claude-sonnet-4-20250514` |
| `openai` | OpenAI | `gpt-4o` |

使用 `/provider`（向导）或 `/model <name>`（仅切换模型）进行切换。
支持推理模型（`reasoning_content`）；推理内容会在下一次请求中回传，
以避免 Provider 返回 400 错误。

## 自定义 Provider { #custom-providers }

DeepSeek、Kimi、Ollama 以及任何 OpenAI/Anthropic 兼容端点都在
`custom-extensions.toml` 中声明（工作区按 `name` 覆盖全局）—
无需编写代码：

```toml
# ./.agenticx/custom-extensions.toml
[[providers]]
name          = "moonshot"
base_url      = "https://api.moonshot.cn/v1"
api_key_env   = "MOONSHOT_API_KEY"   # env var name; no plaintext on disk
default_model = "kimi-k2"
protocol      = "openai"             # openai / anthropic / dashscope

[[providers]]
name          = "ollama"
base_url      = "http://localhost:11434/v1"
default_model = "llama3"
protocol      = "openai"
auth          = false                # local, no key needed
```

明文 `api_key = "sk-..."` 会被加载器拒绝；请使用 `api_key_env` 或
`KEYRING:v1:` 引用。`/dev provider add` 会以交互方式写入这些配置块。
同一文件中的 HTTP 工具组、Shell 工具与技能参见
[工具、技能与专家](guides/tools-and-skills.md#custom-tools)。

**base_url 优先级**（主 Provider）：`OPENAI_BASE_URL` 环境变量 >
`config.toml` 中的 `base_url` > 匹配的 `[[providers]]` 条目 > 内置
默认值。Fallback Provider 始终使用其自身声明的端点。

## 工作区文件一览

| 文件 | 用途 |
|---|---|
| `.agenticx/config.toml` | 模型、能力、交互设置 |
| `.agenticx/system-prompt.md` | 领域角色与行为指令 |
| `.agenticx/skills/` | Markdown 技能与技能包 |
| `.agenticx/custom-extensions.toml` | 声明式 Provider 与 HTTP/Shell 工具 |
| `.agenticx/permissions.toml` | 工具/命令的允许-拒绝-确认策略 |
| `.agenticx/hooks.toml` | 受信任与权限控制约束的事件 Hook |
| `.agenticx/session/` | 持久化会话与运行记录 |