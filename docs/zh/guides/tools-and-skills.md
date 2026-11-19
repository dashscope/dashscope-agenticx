---
description: "AgenticX 工具、权限分级、自定义扩展、Skill 系统与 Expert 模板。"
---
# 工具、技能与专家

## 内置工具与权限分级

每个工具都有一个权限级别，决定其确认方式：

| 级别 | 行为 |
|---|---|
| **Auto** | 不提示；模型直接调用 |
| **Confirm** | 提示 `[y]es / [n]o / [a]lways (this conversation) / [s]top (abort turn)` |
| **Dangerous** | 仅 `y` / `n` — 破坏性操作每次都会询问；"always" 缓存无法绕过 |

`permissions.toml` 中的显式拒绝策略即使对 AUTO 工具与 `auto_approve`
运行也生效。参见[权限](security-and-platform.md#permissions)。

### 文件系统

| 工具 | 权限 | 说明 |
|---|---|---|
| `read_file` | Auto | 读取文件内容（行偏移/行数限制） |
| `write_file` | Confirm | 写入文件，返回统一 diff |
| `list_directory` | Auto | 列出目录内容，含类型与大小 |
| `create_directory` | Confirm | 创建目录，包括嵌套的父目录 |
| `delete_file` | Dangerous | 删除文件（不可逆） |
| `delete_directory` | Dangerous | 删除目录树（不可逆） |
| `search_files` | Auto | 按 glob 模式搜索文件 |
| `move_file` | Confirm | 移动 / 重命名文件或目录 |

### Shell

`run_command`（Confirm）执行 Shell 命令。危险模式的命令会被拦截，
只读命令会被自动分类。`timeout` 参数（秒，默认 600，
`AGENTICX_COMMAND_TIMEOUT`）限制运行时长；超时后会终止整个进程组，
因此管道不会在超时后继续存活。

### 网络（可选 `web` / `browser` 扩展）

| 工具 | 权限 | 说明 |
|---|---|---|
| `web_search` | Auto | DuckDuckGo 搜索：标题、URL、摘要 |
| `scrape_web` | Auto | 通过 Playwright 无头浏览器获取页面纯文本（可渲染 JS SPA） |
| `scrape_web_html` | Auto | 渲染后的 HTML 源码 |
| `scrape_web_screenshot` | Auto | 截图并保存为图片文件 |

安装扩展后，浏览器工具还需要单独安装 Playwright 运行时。摄像头工具
（`camera_capture`、`camera_record`，均为 Confirm）需要
`camera`/`vision` 媒体扩展。

### 会话与配置（始终可用）

`switch_model`、`switch_provider`、`list_models`、`capability_list`
（Auto）；`capability_enable`、`capability_disable`、`mcp_connect`、
`mcp_disconnect`（Confirm）；`mcp_list_services`、`create_plan`、
`complete_step`、`get_plan_status`（Auto）。

### 子代理（启用 `local.subagent` / `local.delegate` 后）

`subagent_invoke`、`review_code`、`write_tests`、`generate_docs`、
`delegate`、`delegate_parallel` — 均为 Auto。参见
[子代理、Hooks 与记忆](agent-patterns.md#subagents-and-delegation)。

### 平台工具（启用 `bailian.cli` 后）

完整的百炼 CLI 命令集会被注册为 `bailian_<command_path>` 工具
（如 `bailian_text_chat`、`bailian_image_generate`）。读取类动词
（list/get/status/search）为 Auto；写入类动词（generate/upload/delete）
为 Confirm。Schema 来自 `bl config export-schema`，缓存 24 小时。
参见[平台集成](security-and-platform.md#bailian-cli)。

## 能力

可选工具组由能力（capability）门控：

```text
agenticx> /capability list
agenticx> /capability enable bailian.cli    # takes effect after restart
agenticx> /capability disable local.subagent
agenticx> /capability reload
```

已禁用能力对应的命令会被拦截并给出启用提示，而不是因缺少凭据而失败。
安装扩展本身绝不会为 App 授予权限。

## 自定义工具 { #custom-tools }

### 第 1 层 — 配置即程序（无需代码）

`custom-extensions.toml`（工作区 `./.agenticx/` 覆盖全局
`~/.config/AgenticX/`；文件权限为 chmod 600）在启动时注册 Provider、
HTTP 工具组与 Shell 工具。`/dev reload` 可热重载。

HTTP 工具组：

```toml
[[capabilities]]
key = "moonshot.web"
display = "Moonshot web search"
auth = "bearer:$MOONSHOT_API_KEY"

[[capabilities.tools]]
name = "web_search"
description = "Web search for real-time information"
endpoint = "https://api.moonshot.cn/v1/tools/web_search"
http_method = "POST"
permission = "auto"                    # auto / confirm / dangerous
params = [
  {name="query", type="string", required=true, description="Search keywords"},
  {name="count", type="integer", default=10, description="Number of results"},
]
body_template = '{"query": {{query}}, "count": {{count}}}'
result_jsonpath = "data.results"       # optional dot-path extraction
```

认证格式：`bearer:$ENV`、`apikey-header:HeaderName:$ENV`、`none`。
请求体模板使用 Mustache `{{var}}` 占位符（值在替换前会进行 JSON
编码）。

Shell 工具 — 将一条命令封装为 LLM 可调用的工具：

```toml
[[shell_tools]]
name = "check_port"
description = "Check whether a TCP port is open"
command_template = "nc -z -w 3 {{host}} {{port}}"
params = [
  {name="host", type="string", required=true},
  {name="port", type="integer", required=true},
]
permission = "auto"
```

参数化的 `{{name}}` 值会作为字面进程参数传递，即使占位符被引号包裹
也是如此。参数化管道、重定向、Shell 解释器以及可执行的 Python
代码/模块名均被拒绝；复杂工作流请以字面参数调用受信任的可执行脚本。
不带参数的常量命令保留显式的 Shell 行为。

Provider 声明（`[[providers]]`）与 API Key 安全规则见
[配置](../configuration.md#custom-providers)。

### 第 2 层 — 真正的 Python 工具

向运行时注册表注册 `ToolDefinition`，或使用 `@tool` 装饰器
（参数 Schema 由类型注解推导）：

```python
from dashscope_agenticx.tools.registry import (
    PermissionLevel,
    ToolDefinition,
    tool,
)

@tool("check_complexity", "Check cyclomatic complexity of a file",
      permission=PermissionLevel.AUTO)
async def check_complexity(path: str) -> str:
    ...
```

在 Skill 包内，于 `tools.py` 中暴露 `register(registry)` 函数；工具名
会自动加上包名前缀（`my-review_check_complexity`）：

```python
from dashscope_agenticx.tools.registry import PermissionLevel, ToolDefinition

async def check_complexity(path: str) -> str:
    ...

def register(registry):
    registry.register(ToolDefinition(
        name="check_complexity",
        description="Check the cyclomatic complexity of a file",
        func=check_complexity,
        permission=PermissionLevel.AUTO,
        parameters={
            "type": "object",
            "properties": {"path": {"type": "string"}},
            "required": ["path"],
        },
    ))
```

`/dev` 命令族可以交互式地生成以上所有内容：
`/dev provider|capability|skill|tool add|list|rm`、`/dev model add`、
`/dev debug tools|schema|call|prompt`、`/dev test provider <name>`。
旧版 `.agenticx/plugins/` 自动加载机制已移除 — 请改用第 1 层 TOML、
Hook 或第 2 层模块。

## 技能

Skill 是通过 `/skill` 调用的预设提示词模板。有两种形式：单文件
`.md` 与目录包。启动时会扫描 `~/.config/AgenticX/skills/` 与
`.agenticx/skills/`；同名的工作区技能会覆盖全局技能。

### 单文件技能

YAML frontmatter 定义元数据；正文是带 `{arg}` 占位符替换的提示词
模板：

```markdown
---
name: review
description: Code review with improvement suggestions
arguments: [file]
---
Please perform a code review of the following file, focusing on:
1. Potential bugs
2. Performance issues
3. Readability

File: {file}
```

使用 `/skill review src/main.py` 调用。单文件技能会以精简目录的形式
注入系统提示词（参数、描述、MCP 依赖状态）。

### 目录包技能

用于自定义 Python 工具或专属 Hook：

```text
.agenticx/skills/my-review/
├── skill.toml       # metadata, activation rules
├── prompt.md        # prompt template
├── tools.py         # optional: custom tools (prefixed my-review_<tool>)
└── hooks.toml       # optional: hooks scoped to this skill
```

```toml
# skill.toml
name = "my-review"
version = "1.0.0"
description = "Custom code review with a complexity check tool"
author = "your-name"
always_active = false
triggers = ["*review*", "*audit*"]
```

- `always_active = true`：每轮都注入 `prompt.md`。
- `triggers`：glob 模式；用户消息匹配任一模式时技能即激活。

### 生命周期

```text
/skill list                     # single files + packages
/skill reload                   # rescan directories
/skill enable|disable <name>    # force state (ignore triggers)
/skill install ./my-review      # local path / git+URL / tar.gz URL / registry name[@version]
/skill link ./my-review         # dev symlink; /skill reload picks up edits
/skill update <name>|--all
/skill uninstall <name>
/skill search review            # installed + registry
/skill publish ./my-review      # validate + package tar.gz into .dist/
```

安装记录保存在 `.agenticx/skills/.agenticx-skills.json`。URL 包会经过
tar 路径穿越校验（包括链接目标）与 50 MB 大小限制。

注册表是一个静态的 TOML/JSON/YAML 索引 — 不依赖服务器：

```toml
# index.toml
[[skills]]
name = "code-review"
version = "1.2.0"
description = "Code review (security+style+performance)"
source = "git+https://github.com/agenticx/skills/code-review.git"
tags = ["python", "review"]
```

将 `.agenticx/config.toml` 中的 `skill_registry` 指向索引 URL 或本地
文件。没有注册表时，`/skill install <name>` 需要本地路径或 git URL。

## 专家（Experts） { #experts }

**配置即 Agent。** Expert 是一个开箱即用的 `.agenticx/` 配置档案 —
人设、Provider、技能、知识索引 — 无需引导代码。下载一个 Expert 是
创建新 Agent 的推荐起点：

```bash
agenticx expert                          # list templates
agenticx expert download basic-chat      # merge into ./.agenticx/
agenticx                                 # already the Expert's Agent
```

- Expert 的 `.agenticx/` 目录树会合并到工作区；根级文件（README 等）
  也会被收集到 `.agenticx/` 中。
- 同名文件会备份到 `.agenticx/backup/`（单一滚动备份）；
  `/expert restore` 可撤销合并 — 恢复被覆盖的文件并移除新增的文件。
- 通过纯文本编辑进行定制：`system-prompt.md`（人设）、
  `skills/*.md`（任务模板）、`references/*.md`（常驻知识，启动时
  注入）、`custom-extensions.toml`（Provider/工具）。

配置档案从
[dashscope/dashscope-agenticx-experts](https://github.com/dashscope/dashscope-agenticx-experts)
的 `apps/<name>/` 下载（默认 `main` 分支）。仅支持 1.0 App Catalog
布局。可通过全局配置中的 `expert_repo` / `expert_branch` 或 `--repo`
覆盖来源。

内置示例：`basic-chat`（带 Web 搜索的极简通用助手）、
`dashscope-sdk-expert`（SDK 问答，带自动生成的知识索引与
`/skill update-sdk` 刷新），以及 `bailian-expert`。

### Expert 与 App Bundle 的区别

Expert 下载包含角色/配置数据 — 不含可执行 Python、Shell 工具或运行
Hook — 且**不会**注册为 App。生产级 App Bundle 额外提供带版本的
`app.toml` 清单、Runtime 兼容范围、权限差异与校验和验证，使用
`agenticx app list|install|info|verify|update` 管理。`app install`
会在写入文件前预览新权限，并将 Bundle 注册到 `.agenticx/apps/` 下，
但不激活其配置档案。校验和只能证明内容一致性，不能证明发布者身份；
本地信任标志也不是经过验证的数字签名。