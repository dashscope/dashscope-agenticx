---
description: "AgenticX CLI 选项、子命令与完整的斜杠命令参考。"
---
# 命令行与斜杠命令

## 启动选项

```shell
agenticx                          # interactive session (TUI if installed)
agenticx --cli                    # inline REPL with native scrollback
agenticx --tui                    # force the Textual interface
agenticx -c "Summarize this project"   # one prompt, then exit
agenticx --dry-run                # preview loaded configuration
agenticx --protocol anthropic     # startup protocol: openai (default) / anthropic
agenticx --max-turns 20           # override the conversation turn limit
agenticx --version
```

## 子命令

```shell
agenticx app list                 # versioned App Bundles
agenticx app install <path>       # install + permission diff preview
agenticx app info <name>          # publisher, permissions, runtime range, checksum
agenticx app verify <name>        # checksum + runtime compatibility
agenticx app update <name>        # reinstall from the recorded source

agenticx expert                   # list expert templates
agenticx expert download <name>   # merge an expert profile into ./.agenticx/
agenticx expert restore           # undo the most recent merge

agenticx mcp-server               # expose tools over MCP stdio (trusted clients only)
agenticx serve --token <token>    # localhost Client API (default 127.0.0.1:8765)
```

`app install` 会将 Bundle 注册到 `.agenticx/apps/` 下，但不会激活其
配置档案；专家下载属于角色/配置数据，并非已注册的 App。参见
[工具、技能与专家](guides/tools-and-skills.md#experts)与
[安全、平台与 MCP](guides/security-and-platform.md#local-http-api)。

## 斜杠命令

命令分为四个层级：会话 → 配置 → 能力 → 开发。`/help` 按此顺序列出。

### 会话

| 命令 | 说明 |
|---|---|
| `/help` | 显示帮助，按类别分组 |
| `/clear` | 清空对话历史（并持久化空历史） |
| `/info` | 运行时信息：Provider/模型/协议/base URL/能力/隐私 |
| `/copy` | 将最后一条回复复制到剪贴板 |
| `/save [path]` | 将最后一条回复保存到文件 |
| `/json on\|off` | 强制 JSON 回复（`response_format` 透传） |
| `/compress` | 用 LLM 摘要替换历史以节省 token |
| `/summarize` | 将当前任务总结为经验记忆 |
| `/stats` | token 用量、提示词构成、API/工具调用次数、耗时 |
| `/report` | 追踪性能报告（调用次数、成功率、延迟） |
| `/feedback good\|bad` | 标记任务满意度（经验记忆） |
| `/history [subcmd]` | 历史统计/列表/搜索/导出/清空 |
| `/undo` | 撤销最近一次文件写入/删除（检查点快照） |
| `/session [subcmd]` | 话题：new/list/switch/rename/fork/remove/scene |
| `/privacy [on\|off\|status]` | 隐私模式 |
| `/audit [subcmd]` | 审计日志：recent/query/clear |
| `/voice [subcmd]` | 语音输入：on/off/model/silence/max/threshold |
| `/tts [subcmd]` | 语音输出：on/off/model/voice/speed/say/last |
| `/camera capture\|record` | 摄像头拍照 / 录像（media 扩展） |
| `/log [subcmd]` | `/debug on` 下记录的完整提示词：tail/search/clear |
| `/trace [subcmd]` | 执行追踪：tail/search/clear |
| `/exit` | 退出（别名 `/quit`、`/q`） |

`/v` 是语音输入快捷键（`Ctrl+T`）：录音 → ASR → 发送。

### 配置

| 命令 | 说明 |
|---|---|
| `/setup` | 交互式工作区初始化向导 |
| `/provider` | 向导：Provider → 模型 → 连接 → API Key |
| `/model [name]` | 在当前 Provider 内切换模型 |
| `/capability [subcmd]` | list/enable/disable/reload/config |
| `/subagents [subcmd]` | list/reload/enable/disable/config |
| `/trust [subcmd]` | 本轮工具信任缓存：list/clear/allow/deny |
| `/rule [subcmd]` | 长期操作规则：list/add/remove/edit/enable/disable/clear |
| `/theme [subcmd]` | list/set/自定义配色（dark/light/monokai/dracula/nord/solarized） |
| `/debug [on\|off\|status]` | 将完整的脱敏提示词记录到会话追踪 |
| `/directives [subcmd]` | 自定义指令 + 自动学习建议/接受/拒绝 |

### 能力

| 命令 | 说明 |
|---|---|
| `/profile [subcmd]` | 用户画像：list/search/add/remove/clear |
| `/memory [subcmd]` | 对话历史记忆：list/search/remove/clear |
| `/mcp [subcmd]` | MCP 服务：add/remove/list（需要 `bailian.mcp`） |
| `/skill [name] [args]` | 列出或调用技能 |
| `/skill install\|link\|update\|uninstall` | 技能包生命周期 |
| `/skill search\|publish\|reload` | 注册表搜索、打包、重新扫描 |
| `/skill enable\|disable <name>` | 强制设置技能包状态 |
| `/app list\|info\|install\|update\|verify` | 带版本的 App Bundle 管理 |
| `/cron [subcmd]` | 定时任务：add/list/remove/pause/resume |

已禁用能力对应的命令会被拦截并给出启用提示，而不是因缺少凭据而失败。

### 开发

| 命令 | 说明 |
|---|---|
| `/dev model add\|rm\|list` | 在运行时注册模型 |
| `/dev provider add\|list\|rm` | 扩展 Provider |
| `/dev capability add\|list\|rm` | 扩展 HTTP 工具组 |
| `/dev skill add\|list\|rm` | 扩展技能 |
| `/dev tool add\|list\|rm` | 扩展 Shell 工具 |
| `/dev debug tools\|schema\|call\|prompt` | 调试辅助工具 |
| `/dev test provider <name>` | 测试 Provider 连通性 |
| `/dev reload` | 热重载 `custom-extensions.toml` |
| `/dev log` | 工具注册统计 |
| `/expert [list]` | 列出专家模板 |
| `/expert download <name>` | 将专家合并到 `./.agenticx/`（冲突时备份） |
| `/expert restore` | 撤销最近一次合并 |

## 终端输出

- Markdown 回复带轮次编号；工具详情默认折叠（`Ctrl+O` 切换），
  失败时保持展开。
- `Ctrl+G` 展开 token/API/工具/提示词构成统计。提示词大小以字符计，
  并非精确的 token 数。
- 选区复制：macOS 上为 `Cmd+C`，Windows/Linux 上为 `Ctrl+C`；在
  macOS 上 `Ctrl+C` 用于清空/取消/退出。`/copy` 复制最新回复的源文本；
  `--cli` 提供原生滚动回看。