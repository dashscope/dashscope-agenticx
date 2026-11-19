---
description: "DashScope AgenticX — Python Agent 运行时：终端 CLI、可嵌入 SDK、工具、技能与受治理的执行。"
---
# DashScope AgenticX

<p class="hero-badges" markdown>
[**v{{ agenticx_version }}**](release-notes.md){ .hero-badge }
[**Python 3.10+**](about.md#requirements){ .hero-badge }
[**MIT**](https://github.com/dashscope/dashscope-agenticx/blob/main/pyproject.toml){ .hero-badge }
</p>

**企业级 AI 专家，为行动而生。** AgenticX 是一个用于构建和运行领域专家的
Python Agent 运行时。它通过统一的运行时将模型、工具、技能、权限与持久化
会话连接起来，并提供终端界面以及面向嵌入式应用的 API。

<div class="hero-cta" markdown>
[快速开始](quickstart.md){ .md-button .md-button--primary }
[嵌入 SDK](guides/sdk.md){ .md-button }
</div>

你可以直接把它当作本地助手使用，嵌入到 Python 应用中，或将领域专家打包为
带版本的 App Bundle。DashScope SDK Expert 与 Bailian Expert 是内置的专家
模板，展示了这一应用层的用法。

## 特性

<div class="grid cards" markdown>

-   :material-robot:{ .lg .middle } **统一运行时**

    ---

    工具、技能、Hook 与会话状态归实例所有；通过 `RuntimeBuilder`
    共享构建流程。

-   :material-console:{ .lg .middle } **终端与 TUI**

    ---

    功能完整的交互式 CLI，提供 40+ 条斜杠命令，另有可选的
    Textual TUI，支持丰富的 Markdown 输出。

-   :material-code-braces:{ .lg .middle } **可嵌入 SDK**

    ---

    `run_once`、`run_stream` 与带类型的 `run_events`，适用于脚本、CI、
    IDE 插件和应用。

-   :material-toolbox:{ .lg .middle } **工具与技能**

    ---

    文件系统、Shell、Web、浏览器、摄像头、MCP 服务、Markdown
    技能，以及声明式 TOML 扩展 — 无需编写代码。

-   :material-shield-check:{ .lg .middle } **受治理的执行**

    ---

    权限分级、策略文件、操作系统沙箱后端、审计日志、隐私模式
    与事件 Hook。

-   :material-package-variant:{ .lg .middle } **应用与专家**

    ---

    带版本的 App 清单，支持校验和验证与权限差异对比；一条命令
    即可初始化专家工作区。

</div>

## 环境要求

- Python 3.10+（基于 Pillow 的 `vision`/`media` 扩展需要 3.11+）。
- 所用 Provider 的模型 API Key — 对于默认的 Tongyi/DashScope
  Provider：

```shell
export DASHSCOPE_API_KEY="<your-api-key>"
```

## 安装

```shell
python -m pip install "dashscope-agenticx[tui]"
agenticx --version
```

如需最小化的非 TUI 安装，使用 `pip install dashscope-agenticx` 并通过
`agenticx --cli` 启动。可选扩展（Provider SDK、web、browser、media）
见[快速开始](quickstart.md#optional-extras)。

## 示例

### 在终端中运行

```shell
agenticx                                    # interactive session
agenticx -c "Summarize this project"        # one-shot, then exit
```

### 在 Python 中运行

```python
from dashscope_agenticx.sdk import run_once_sync

reply = run_once_sync("Inspect this project's structure without changing files")
print(reply)
```

### 消费带类型的事件

```python
import asyncio

from dashscope_agenticx.sdk import run_events


async def main():
    async for event in run_events("Summarize the project"):
        print(event.type.value, event.payload)


asyncio.run(main())
```

一次性运行会执行工具，而不仅仅是生成文本 — 请在合适的工作区中启动，
并为任务选择恰当的权限。

## 下一步

- [快速开始](quickstart.md) — 安装、首次运行、首次 SDK 调用。
- [配置](configuration.md) — 配置层级、Provider、密钥。
- [命令行与斜杠命令](cli.md) — 所有斜杠命令与 CLI 选项。
- [参考](reference/sdk.md) — 完整的 API 接口。

AgenticX 不是托管服务、不是加固的操作系统沙箱，也不是完整的工作流平台。
语音、摄像头、浏览器、Web 搜索、调度器与实验性功能均为可选项，
并非核心运行时的前置条件。