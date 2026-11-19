---
description: "AgenticX 安全模型：权限、隐私、审计、操作系统沙箱、MCP 集成、百炼 CLI 与 localhost Server。"
---
# 安全、平台与 MCP

## 权限 { #permissions }

默认为 `confirm_mode = "all"`：写入操作与 CONFIRM 工具需要批准。显式
`deny` 规则始终优先 — 包括 AUTO 工具与 `auto_approve` 运行。并行委托
获得在执行期间强制生效的只读工具白名单，而不仅仅是对模型隐藏；子代理
不继承父级缓存的批准。

确认选项：

- `y` — 允许一次。
- `n` — 拒绝一次；Agent 收到"操作已取消"并继续。
- `a` — 仅在**当前这一轮**信任该工具名（一次 `run_stream` 调用）；
  轮次结束时自动清除。用 `/trust` 查看，用 `/trust clear` 清除。
- `s` — 抛出 `UserAbortedTurn`；循环结束，控制权回到提示符。

DANGEROUS 工具（`delete_file`、`delete_directory`）只接受 `y`/`n` —
绝不缓存。

### 始终生效的保护层

与权限级别无关：

- **敏感路径**：对 `.ssh`、`.aws`、`.gnupg`、`.env`、`.npmrc`、
  `shadow` 等的读写始终被拒绝。
- **硬性阻止的命令**：`rm -rf /`、`mkfs`、`dd if=`、`> /dev/sd`、
  `chmod -R 777 /` 及类似命令直接拒绝，不予确认。
- **只读分类**：`run_command` 自动放行已知的只读命令（`grep`、`ls`、
  `find`、`cat`、`git status`、`git log`、`git diff`、`pip list`、
  `docker ps`、…）。任何写入信号（`rm`、`mv`、`cp`、`chmod`、`sudo`、
  `pip install`、`make`、重定向 `>`、命令替换 `$(…)`）都会回退到确认
  流程。

### 策略引擎

在 `permissions.toml` 中预设按工具 / 按命令前缀的规则（工作区
`./.agenticx/` 按相同键覆盖全局 `~/.config/AgenticX/`）。策略优先于
交互式确认；命令规则按最长前缀匹配：

```toml
# .agenticx/permissions.toml
[tools]
delete_file = "deny"

[commands]
"git status" = "allow"
"git push" = "deny"     # longest prefix wins over git = allow
```

App 安装/更新会在复制文件前预览新权限。Expert 模板无法安装
`skills/**/tools.py`、Shell 工具或可执行运行 Hook — 即使使用
`--force`；可执行扩展请使用受信任的 App Bundle。权限元数据与校验和
**不是**操作系统/文件系统/网络隔离，也不是发布者签名。

## 操作系统沙箱

配置中的 `sandbox = true` 会在后端可用时于操作系统沙箱内运行 Shell
命令：

| 平台 | 后端 | 说明 |
|---|---|---|
| macOS | Seatbelt（`sandbox-exec`，系统内置） | Profile 将写入限制在工作区内 |
| Linux | bubblewrap（`bwrap`，需已安装） | 只读根目录，绑定工作区 |
| Windows | — | 无后端；命令不加沙箱运行 |

沙箱支持属于纵深防御，并非加固的隔离契约。可用性与限制取决于后端；
自动化前请审查所报告的执行边界（`SANDBOX_STATUS` 事件、`/info`）。

## 隐私模式

```text
/privacy on | off | status
```

| 效果 | 说明 |
|---|---|
| 云端能力禁用 | `bailian.*` 视为未启用（`/mcp` 被拦截）；本地记忆/存储不受影响 |
| 定时任务暂停 | 跳过 cron 运行，标记为"因隐私模式跳过" |
| 审计脱敏 | 工具参数与用户输入截断为 20 个字符 |

!!! note
    隐私模式**不会**阻止 LLM API 调用 — 对话内容仍会发送给模型
    Provider。如需完全离线使用，请切换到 `ollama` 等本地 Provider。

## 审计日志

关键操作以 JSONL 追加到 `./.agenticx/audit.log`（按工作区）：

```jsonc
{
  "timestamp": "2026-11-19T10:00:00Z",
  "source":    "agent",        // user / agent / scheduler / system
  "action":    "tool_call",    // input / tool_call / permission / cron / mcp
  "subject":   "run_command",
  "decision":  "approved",     // approved / denied / executed / skipped / auto
  "reason":    "",
  "details":   { "args": { "command": "git status" } }
}
```

```text
/audit recent [n]      # latest records (default 20)
/audit query tool_call # filter by action
/audit clear
```

隐私模式下，参数值（`content`、`command`、`query`、`text`、`prompt`、
`url`、`path`、`file`）截断为 20 个字符并附加 `…(redacted)`；工具名与
决策始终保留。

## MCP 集成

AgenticX 既是 MCP **客户端**也是**服务端**。

### 客户端（`/mcp`，能力 `bailian.mcp`）

通过 **SSE 传输**连接，将工具注册为 `mcp_<service>_<tool>`。兼容百炼
MCP 市场以及任何 SSE 兼容的第三方/本地服务：

```text
/capability enable bailian.mcp        # requires DASHSCOPE_API_KEY for Bailian
/mcp list                             # marketplace services
/mcp add time                         # Bailian default endpoint
/mcp add my-server http://localhost:3000/sse   # custom SSE URL
/mcp                                  # connected services + tools
/mcp remove my-server
```

连接会持久化到工作区 `config.toml` 的 `[[mcp_servers]]`，并在启动时
重连。无需百炼密钥的第三方服务可将 `tongyi_api_key` 留空；服务侧认证
在 SSE 端点处理（例如 URL 中的 token）。

!!! note "传输限制"
    MCP 客户端仅支持基于 HTTP 的 SSE — 不支持 stdio。请改用
    `[[shell_tools]]` / `[[capabilities.tools]]` 将本地 stdio MCP
    服务器封装为工具。

### 服务端（`agenticx mcp-server`）

通过 stdio（JSON-RPC）向其他 MCP 兼容 Agent 暴露已注册的工具，协议
版本 `2024-11-05`，方法为 `tools/list` 与 `tools/call`。

!!! warning
    MCP 服务端以 `auto_approve` 模式执行工具（无交互式确认）。仅向
    受信任的客户端暴露。

## 百炼 CLI { #bailian-cli }

启用 `bailian.cli` 能力、PATH 中有 `bl` 且已认证（`bl auth login`）
后，AgenticX 会将完整的百炼命令集注册为原生函数调用工具
（`bailian_text_chat`、`bailian_image_generate`、…）。Schema 来自
`bl config export-schema`，在 `~/.config/AgenticX/bl-schemas.json`
缓存 24 小时；camelCase 参数自动转换为 `--kebab-case` 标志。权限按
动词分级：读取类动词为 Auto，消耗配额/产生文件的动词为 Confirm。
AgenticX 会将已配置的 DashScope 密钥作为 `--api-key` 透传，因此认证
只需配置一次。`bailian.cli` 与 `bailian.mcp` 可以共存，不会发生工具名
冲突。

## 本地 HTTP API { #local-http-api }

`agenticx serve` 启动一个基于 token 认证的 localhost Client API，
提供 Runs、Sessions、Artifacts 与 Traces：

```bash
agenticx serve --token <at-least-16-chars>   # default 127.0.0.1:8765
# token omitted → generated and printed at startup
```

| 路由 | 方法 | 用途 |
|---|---|---|
| `/health` | GET | 存活检查 |
| `/v1/sessions`, `/v1/sessions/<topic>` | GET | 会话列表 / 状态 |
| `/v1/artifacts?session_id=…` | GET | 产物 |
| `/v1/traces` | GET | 追踪文件名 |
| `/v1/runs` | POST | 运行一轮（`input`，可选 `session_id`、`system_prompt`）；返回带类型的事件列表 |
| `/v1/permissions*` | POST | 始终 **403** — 权限决策仅限本地运行时 |

请求需要 `Authorization: Bearer <token>`（常量时间比较）。服务器仅
绑定 `127.0.0.1` / `localhost` / `::1`；其他主机被拒绝。Runs 强制
`auto_approve = False` 与 `confirm_mode = "all"`，任何需要批准的操作
都会被**拒绝**，即使本地 CLI 配置启用了自动执行。这不是面向互联网的
部署契约。

Python 客户端请直接嵌入 [SDK](sdk.md)；非 Python 客户端可从
`/v1/runs` 消费同样的 `AgentEventEnvelope` 载荷。