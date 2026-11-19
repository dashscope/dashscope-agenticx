---
description: "AgenticX Agent 模式：子代理与委托、生命周期 Hook、定时任务、记忆、规则与提示词构成。"
---
# 子代理、Hooks 与记忆

## 子代理与委托 { #subagents-and-delegation }

### `subagent_invoke`（能力 `local.subagent`）

模型可以启动一个拥有**隔离上下文**的二级 Agent 来执行子任务；只有
摘要会返回，因此中间的工具调用不会污染主对话。

| 参数 | 默认值 | 说明 |
|---|---|---|
| `prompt` | 必填 | 子任务，作为单条用户消息 |
| `system_prompt` | 空 | 覆盖子代理的角色（评审员 / 研究员 / …） |
| `max_turns` | 10（上限 50） | 子循环轮次限制，防止失控执行 |

子代理**共享**主 Agent 的 Provider、执行器、工具表、MemoryManager
（计划进度、反思提示）、`/rule` 规则与能力通知 — 但**不共享**
`session_path`：子代理是一次性的，绝不写入会话历史。

启用 `local.subagent` 还会注册三个带固定系统提示词的专家工具：
`review_code`（缺陷/安全/性能/风格）、`write_tests`（pytest 生成）、
`generate_docs`（API 文档与示例）。

### `delegate` / `delegate_parallel`（能力 `local.delegate`）

将复杂任务拆分给多个隔离的子代理并汇总结果：

- `delegate` — 串行：将一个大提示词交给单个子代理，返回完整结果。
- `delegate_parallel` — 并行：同时下发多个 `(name, prompt)` 任务；
  结果以 `name: result` 的形式拼接后交给主 Agent。

| 参数 | 必填 | 说明 |
|---|---|---|
| `task` | 是 | 任务描述 |
| `system_prompt` | 否 | 角色覆盖 |
| `tools` | 否 | 工具白名单（如 `["read_file", "search_files"]`）；为空则继承主工具表 |
| `context_files` | 否 | 文件路径，其内容会前置到子任务中 |
| `max_turns` | 否 | 默认 10，上限 50 |
| `timeout` | 否 | 每个子任务的秒数，默认 120 |
| `model` | 否 | 子代理模型；默认继承 |

```toml
# config.toml
delegation = { max_concurrent = 5, default_timeout = 120 }
```

子代理**始终禁止进一步委托**（创建时 `allow_delegate=False`），防止
无限级联。`allow_nested` 配置字段会被解析并存储，但当前不生效。

## 事件 Hook

`.agenticx/hooks.toml` 在生命周期节点插入自定义行为：拦截危险工具、
打印警告、运行审计脚本或请求确认。

| 事件 | 触发时机 |
|---|---|
| `before_tool_call` | 工具执行前 |
| `after_tool_call` | 工具执行完成后 |
| `on_error` | 工具抛出异常或子代理失败时 |
| `on_message` | 收到用户消息时 |
| `on_response` | 收到 LLM 响应时 |

治理类动作优先分发；命中 `block` 会短路所有后续 Hook：

| 类别 | 动作 | 行为 |
|---|---|---|
| 治理 | `block` | 阻止操作，向 Agent 返回自定义消息 |
| 治理 | `confirm` | 仅在用户确认后继续 |
| 治理 | `alert` | 红色告警；工具失败时升级 |
| 扩展 | `run` | 执行 Shell 命令（`run = "..."` 或参数数组） |
| 扩展 | `warn` | 黄色警告 |
| 扩展 | `log` | 写入日志，不输出到终端 |

```toml
# .agenticx/hooks.toml
[[hooks.before_tool_call]]
match = "write_file"
condition = "*.py"
action = "confirm"
message = "About to write {filename}, confirm?"

[[hooks.before_tool_call]]
match = "run_command"
content_pattern = "*rm -rf*"
action = "block"
message = "Dangerous command detected, blocked"

[[hooks.after_tool_call]]
match = "write_file"
condition = "*.py"
action = "run"
run = ["python", "-m", "py_compile", "{path}"]
on_fail = "warn"
silent = true
```

匹配字段：`match`（工具名 glob，默认 `"*"`）、`condition`（从参数中
提取的文件路径 glob）、`content_pattern`（对参数 JSON 或结果的内容
glob）。其他字段：`message`（模板）、`on_fail`（默认 `warn` /
`alert` / `block`）、`silent`。

模板变量包括 `{tool_name}`、`{args}`、`{result}`、`{error}`、
`{path}`、`{filename}`、`{filename_stem}`、`{content}`、`{exit_code}`、
`{timestamp}`。

!!! warning "运行 Hook 的参数边界"
    运行 Hook 将模板值作为**字面进程参数**传递，不经过 Shell。包含
    变量的命令字符串会在替换前先解析；管道与重定向会被拒绝。可执行
    文件不能使用模板；带模板的 Hook 不能调用 Shell 解释器，也不能
    插值 Python 代码/模块名；Windows 目标不能是 `.bat`/`.cmd`。只有
    常量命令字符串保留 Shell 执行。此边界并不能沙箱化一个把参数当作
    代码求值的脚本。

Skill 包可以通过 `.agenticx/skills/<name>/hooks.toml` 将 Hook 限定在
自身范围内，挂载到同一个 HookBus 上。Hook 受信任与权限控制约束 —
参见[安全](security-and-platform.md)。

## 定时任务（`/cron`）

按计划运行 Skill；每次运行默认使用各自隔离的子代理。任务跨会话持久化
在 `~/.config/AgenticX/cron_jobs.json`。

```text
/cron add every 5m skill weather Hangzhou
/cron add at 14:30 skill search AI morning briefing      # one-shot
/cron add cron "0 9 * * 1-5" skill search morning briefing
/cron add every 1h skill weather Beijing skill search tech news
/cron add every 10m skill search service alerts condition "curl -sf http://health"
/cron add every 5m skill weather Hangzhou no-subagent    # main conversation
```

管理：`/cron list|pause <id>|resume <id>|remove <id>`。

`at` 任务执行后自动禁用；每次运行有 300 秒超时；隐私模式启用期间
cron 任务不会运行。

## 系统提示词与项目指令

`.agenticx/system-prompt.md`（工作区）或
`~/.config/AgenticX/system-prompt.md`（全局）替换**基础**提示词；
工作区优先。随后 `PromptPipeline` 会追加各个部分 — 你无需重新声明
工具用法、规则或身份：

```text
[base prompt / system-prompt.md]
[Project instructions]
─── stable prefix: byte-identical for the whole session (prompt-cache friendly) ───
[Skills templates] [Skill package rules] [Disabled-capability notice]
[Long-lived operation rules] [Current plan] [Experience recall]
[Tool-chain context] [Directive proposals] [Environment info]
─── ephemeral suffix: recomputed every turn ───
```

项目指令文件会从当前工作目录向上自动发现，首个匹配生效：
`.agenticx/rules.jsonl` > `.agenticx/prompt.md` > `.agenticx/prompt` >
`.agenticx/instructions.md` > `.cursorrules` > `.claude.md` >
`CLAUDE.md`。YAML frontmatter 会被剥离；超长文件在 8000 字符处按
头/尾 60/40 截断。

## 规则与画像

两种互补的记忆形式：

| 维度 | `/profile` | `/rule` |
|---|---|---|
| 内容 | 身份、偏好、技术栈（事实） | 操作规则、流程约束 |
| 注入 | 关键词召回 — 仅相关轮次 | 已启用规则 — 每轮注入 |
| 存储 | `.agenticx/memory/<user_id>.json` | `.agenticx/rules.jsonl` |
| 示例 | "我是后端开发者" | "提交后不要 push" |

经验法则：**`/profile` 描述"我是谁"；`/rule` 描述"事情该怎么做"。**

```text
/rule add "Do not push after each commit; wait until the user says push"
/rule list | edit | enable <n> | disable <n> | remove <n> | clear
/profile list | search <kw> | add <text> | remove <n> | clear
```

启用规则超过 15 条会触发精简提示（token 会持续增长）。

**指令自动学习**：工具调用序列会记录到
`.agenticx/memory/behavior_patterns.json`；超过 5 轮后，出现 3 次以上
的相邻工具对会成为规则建议（置信度 = `min(count / 10, 1.0)`，同时
最多 2 条待审）。使用 `/directives proposals` 审阅，然后
`/directives accept <id>` 或 `reject <id>`。除此建议机制外，核心默认
不会在后台学习指令或生成技能；实验性的技能演化需要实验包与
`AGENTICX_EXPERIMENTAL=1`。

## 记忆分层与会话

| 层级 | 内容 | 生命周期 |
|---|---|---|
| 工作记忆 | 对话消息；按模型上下文窗口进行 token 预算压缩（软阈值 → LLM 摘要旧消息，硬阈值 → 截断） | 会话内 |
| 中期记忆 | 计划进度（TTL 过期）、反思提示、工具链模式 | 会话内 |
| 长期记忆 | 跨会话经验学习（`experiences.jsonl`，支持 CJK 的检索） | 跨会话 |

会话（话题）隔离工作记忆：`.agenticx/session/<topic>/history.json`。
长期记忆（画像、技能、规则、经验）跨话题共享。每个话题可携带
**场景记忆**（`scene.md`），每次切回该话题时注入：

```text
/session new|list|switch|rename|fork|remove
/session scene Context: data platform, Python 3.11, pytest only
/session scene set <text>     # replace
/session scene clear
```

经验记忆记录任务结果与工具模式；长任务（6 条以上消息）会自动总结，
`/summarize` 手动触发，`/feedback good|bad` 标记满意度。连续 3 次以上
工具失败后，反思提示会促使模型改变策略。

对话历史摘要保存在 `.agenticx/memory/history.json`
（`/memory list|search|remove|clear`）。每次 LLM 调用与工具执行都会
追踪到 `.agenticx/traces/<session>.jsonl`，用于调试与评测
（`/trace`、`/report`、`/stats`）。

上下文管理命令：`/compress` 用 LLM 摘要替换历史；`/undo` 通过检查点
快照回退最近一次文件写入/删除。