---
description: "安装 AgenticX、配置 API Key，运行首个终端会话与首个 Python SDK 调用。"
---
# 快速开始

## 安装

需要 Python **3.10+**。

=== "安装包"

    ```shell
    python -m pip install "dashscope-agenticx[tui]"
    agenticx --version
    ```

    如需最小化的非 TUI 安装：

    ```shell
    python -m pip install dashscope-agenticx
    agenticx --cli
    ```

=== "从源码安装"

    ```shell
    python -m venv .venv
    # macOS / Linux
    source .venv/bin/activate
    # Windows PowerShell: .venv\Scripts\Activate.ps1
    python -m pip install -e ".[tui]"
    agenticx --version
    ```

发行包名为 **`dashscope-agenticx`**，导入命名空间为
**`dashscope_agenticx`**，唯一的官方命令是 **`agenticx`**。
PyPI 上名为 `agenticx` 的包是另一个不同的项目。

### 可选扩展 { #optional-extras }

| 扩展 | 用途 |
|---|---|
| `tui` | 带固定输入面板与丰富输出的 Textual 界面 |
| `provider-openai`, `provider-anthropic` | 各 Provider 专属的 SDK 路由 |
| `web`, `browser` | 搜索与浏览器工具（浏览器运行时需单独安装） |
| `voice`, `camera`, `vision`（或 `media`） | 音频、摄像头与图像依赖 |
| `dev`, `all` | 测试/构建工具与全部可选功能 |

```shell
python -m pip install "dashscope-agenticx[web,browser]"
```

安装依赖并不会为 App 授予权限。

## 设置 API Key

对于默认的 Tongyi/DashScope Provider：

```shell
export DASHSCOPE_API_KEY="<your-api-key>"
# Windows PowerShell: $env:DASHSCOPE_API_KEY="<your-api-key>"
```

其他 Provider 使用 `ANTHROPIC_API_KEY` / `OPENAI_API_KEY`，或由
`/provider` 管理的各 Provider 密钥槽位（存储在操作系统 keyring 中）。
参见[配置](configuration.md#global-config-api-keys)。不要将真实密钥粘贴到
纳入版本管理的配置文件中。

## 首次终端运行

```shell
agenticx
```

在没有 `.agenticx/config.toml` 的新工作区中，会启动 `/setup`
向导：用户名、配置模式（Default — Alibaba/Bailian，或 Custom）、
Provider/模型、API Key、能力，以及可选的专家模板。已完成配置的
工作区会直接进入提示符。

```text
agenticx> Summarize this project's structure before making any edits
agenticx> /help
agenticx> /provider
agenticx> /session
```

其他入口模式：

```shell
agenticx --cli                      # inline REPL with native scrollback
agenticx --tui                      # Textual interface (tui extra)
agenticx -c "List the files here and summarize the project"
agenticx --dry-run                  # preview loaded configuration
```

一次性运行可以执行工具。请在合适的工作区中启动，并在信任该任务之前
保持默认的 `confirm_mode = "all"`。

## 首次 SDK 调用

```python
from dashscope_agenticx.sdk import run_once_sync

reply = run_once_sync("Summarize the key points of README.md")
print(reply)
```

`run_once_sync` 会执行完整的一轮对话 — 包括工具调用 — 并返回最终的
助手文本。流式输出与带类型的事件参见[嵌入式 SDK](guides/sdk.md)。

## 下一步

- [配置](configuration.md) — 配置文件、Provider、密钥。
- [命令行与斜杠命令](cli.md) — 完整的命令接口。
- [工具、技能与专家](guides/tools-and-skills.md) — 扩展 Agent。
- [安全、平台与 MCP](guides/security-and-platform.md) — 权限与平台能力。