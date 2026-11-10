# DashScope AgenticX

![DashScope AgenticX](logo.svg)

[English](README.md) | [中文](README_zh.md)

**面向企业的大模型专家与工具运行平台。**
*Enterprise AI experts, built to act.*

AgenticX 1.0 是用于构建和运行领域专家的 Python Agent Runtime。它通过统一运行时
连接模型、工具、Skill、权限和持久化会话，并提供终端界面与嵌入式 API。

你可以直接把它作为本地助手使用，也可以嵌入 Python 应用，或将领域专家打包为
版本化 App Bundle。随源码提供的 DashScope SDK Expert 和 Bailian Expert
是这一应用层的 Expert 模板。

## 核心能力

| 层次 | 能力 |
|---|---|
| Runtime | 工具、Skill、Hook 和 Session 状态由实例持有，入口通过 `RuntimeBuilder` 构建 |
| Agent 执行 | 模型调用、工具协调、运行守卫和会话持久化 |
| 事件协议 | `AgentEventEnvelope` 描述消息、工具、权限、用量和产物事件 |
| 应用 | 版本化 Manifest、Runtime 兼容约束、权限差异和 checksum 校验 |
| 交互入口 | CLI、Textual TUI、Python SDK、嵌入式入口和 Token 鉴权的 localhost Server |
| 模型接入 | 通义 / DashScope，以及可选 Provider SDK 和声明式 Provider 配置 |
| 扩展点 | System Prompt、Skill、HTTP/Shell 工具、MCP 服务和受治理的 Hook |

语音、相机、浏览器、搜索、调度和实验能力为可选项，不是 Core Runtime 的前提。
AgenticX 不等于托管服务、强隔离 OS 沙箱或完整 Workflow 平台。

## 安装

基础运行时需要 **Python 3.10+**。依赖 Pillow 的 `vision`、`media` 扩展需要
**Python 3.11+**；Python 3.10 安装 `all` 时不包含 Pillow。
CI 包含 Python 3.10–3.13 测试以及
Ubuntu/macOS/Windows 冒烟检查；这不代表每一种设备或沙箱后端都支持所有系统。

### 安装 Python 包

以下命令以 distribution 已发布为前提；仅发布源码或尚未发布到包索引时，
请使用下方的「从源码安装」。

```bash
python -m pip install "dashscope-agenticx[tui]"
agenticx --version
```

最小安装使用 `python -m pip install dashscope-agenticx`，
并通过 `agenticx --cli` 启动。

### 从源码安装

开发时，在仓库 checkout 中执行：

```bash
python -m venv .venv
# macOS / Linux
source .venv/bin/activate
# Windows PowerShell: .venv\Scripts\Activate.ps1
python -m pip install -e ".[tui]"
agenticx --version
```

Python distribution 是 **`dashscope-agenticx`**，导入命名空间是
**`dashscope_agenticx`**，唯一官方命令是 **`agenticx`**。
PyPI 上裸名 `agenticx` 属于另一项目。二进制构建与发布要求详见
[发布流程](RELEASE.md)。

### 可选依赖

按需选择依赖；源码 checkout 可使用对应的
`python -m pip install -e ".[<extras>]"` 安装方式。

| Extra | 安装命令 | 用途 |
|---|---|---|
| TUI | `python -m pip install "dashscope-agenticx[tui]"` | 固定输入区和富文本终端输出 |
| Provider SDK | `python -m pip install "dashscope-agenticx[provider-openai,provider-anthropic]"` | Provider SDK 调用路径 |
| 搜索 / 浏览器 | `python -m pip install "dashscope-agenticx[web,browser]"` | 搜索和浏览器工具，浏览器运行环境需另行配置 |
| 多媒体 | `python -m pip install "dashscope-agenticx[voice,camera,vision]"` | 音频、相机和图像依赖 |
| 开发 | `python -m pip install -e ".[dev,all]"` | 测试工具及可选能力依赖 |

后端配置和能力开关详见[使用手册](user-guide_zh.md)。安装依赖不等于授予 App 权限。

## 快速开始

默认使用通义 / DashScope，通过进程环境变量提供 API Key：

```bash
export DASHSCOPE_API_KEY="<your-api-key>"
# Windows PowerShell: $env:DASHSCOPE_API_KEY="<your-api-key>"
agenticx
```

进入会话后，用 `/setup` 配置工作区，或用 `/provider` 选择模型。
不要将真实 API Key 写入 Git 跟踪的配置或示例文件。

```bash
agenticx --cli
agenticx --tui
agenticx "列出当前目录的文件，并概述项目结构"
```

交互会话示例：

```text
agenticx> 先总结项目结构，不要修改文件
agenticx> /help
agenticx> /copy
agenticx> /session
```

单次命令会执行完整 Agent 轮次，可能调用工具，不只是生成文本。
请在合适的工作区运行，并根据任务选择权限策略。

### 终端交互

- 正文按 Markdown 渲染，每次请求有轮次编号和结束状态。
- 工具详情默认折叠，失败调用默认展开；点击摘要或用 `Ctrl+O` 切换保留的详情。
- `Ctrl+G` 展开 Token、API、工具和 Prompt 组成统计。Prompt 大小按字符计，
  不是精确模型 Token 数；cached tokens 已计入 input，不能重复相加。
- macOS 用 `Cmd+C` 复制选区，Windows/Linux 用 `Ctrl+C`。底部 **Copy**
  按钮按捕获的选区快照复制，不依赖终端转发快捷键。框选和正常复制不弹出提示。
- macOS 的 `Ctrl+C` 只负责清空输入、取消或退出，不复制选区。
- `/copy` 复制最近一条回复原文。需要终端原生回滚与框选时，选择 `--cli`，
  而不是使用备用屏的 TUI。

## Python API

便捷 API 会运行完整轮次，包括工具调用：

```python
from dashscope_agenticx import run_once_sync

reply = run_once_sync("只检查项目结构，不修改文件")
print(reply)
```

集成应用时，使用结构化事件，不需要解析终端输出：

```python
import asyncio

from dashscope_agenticx import run_events

async def main():
    async for event in run_events("总结项目结构"):
        print(event.type.value, event.payload)

asyncio.run(main())
```

`create_agent` 接受显式配置、System Prompt 和 Session ID；
`run_interactive` 用于嵌入交互入口。更底层的集成可通过 `RuntimeBuilder`
构建运行时，详见[使用手册](user-guide_zh.md)。

## 配置与 App

工作区配置位于 `.agenticx/`。全局配置使用平台标准的 `AgenticX` 目录，
可通过 `AGENTICX_HOME` 覆盖。

| 文件 | 作用 |
|---|---|
| `config.toml` | 模型、能力和交互配置 |
| `system-prompt.md` | 专家角色和行为约束 |
| `skills/` | Markdown Skill 和目录式 Skill 包 |
| `custom-extensions.toml` | 声明式 Provider 与 HTTP/Shell 工具 |
| `permissions.toml` | 工具和命令的 allow/deny/confirm 策略 |
| `hooks.toml` | 受信任及权限控制的事件 Hook |
| `session/` | 持久化会话与运行记录 |

生产 App Bundle 由版本化 `app.toml` Manifest、`.agenticx/` Profile，
以及可选 Eval / Acceptance 文件组成。本地管理命令为：

```bash
agenticx app list
agenticx app install ./path/to/app-bundle
agenticx app info <app-name>
agenticx app verify <app-name>
agenticx app update <app-name>
```

源码提供 `dashscope-sdk-expert` 和 `bailian-expert` 两个 Bundle。
安装前检查 Manifest 和请求的权限。Checksum 只能验证内容身份，不能证明发布者
真实性；本地 trust 标记也不是经过验证的数字签名。
`/expert` 管理 Expert 角色模板，不能替代版本化 App 安装契约。

## 安全与兼容

- 新凭据通过 OS Keyring 保存，或通过环境变量提供；旧 `ENC:` 不是安全加密。
- App 代码、Shell Hook 和工具执行受信任与权限控制。不要为了绕过检查，
  将未知 Bundle 标记为 verified。
- 默认 `confirm_mode = "all"`：写入及需确认的工具须经批准。
  显式 deny 策略也对 AUTO 工具和 `auto_approve` 生效。
- Expert 下载只导入角色和配置，不安装 Python 工具、Shell 工具或 run Hook；
  App 安装在写入前预览新增权限。信任批准不等于 OS 沙箱或网络白名单隔离。
- OS 沙箱属于纵深防御，可用性与限制由后端决定；自动化前检查报告的执行边界。
- localhost Server 需要 Bearer Token，不接受远程权限批准；即使本机 CLI 开启自动执行，
  Server 仍拒绝需批准的操作。它不是面向公网的部署契约。
- 旧目录迁移是单向的，不合并或覆盖已有目标目录；Python 导入和环境变量需显式更新。

升级及安装第三方应用前，请阅读[安全策略](SECURITY.md)和
[1.0 迁移指南](migration-to-1.0.md)。

## 开发

```bash
python -m pip install -e ".[dev,all]"
python -m pip install pre-commit
python -m pytest tests/ -q
python scripts/release_check.py
pre-commit run --all-files
```

Release contract 检查包身份、版本、导入、CLI 和内置 App。覆盖率门禁、跨平台 CI、
App 校验和供应链检查是独立发布要求；本地单测通过不等于已经发布或认证最终 1.0。

## 文档

| 文档 | 内容 |
|---|---|
| [中文使用手册](user-guide_zh.md) / [English guide](user-guide.md) | 完整命令、配置、工具、模块和开发参考 |
| [迁移指南](migration-to-1.0.md) | 旧命名、目录、凭据和兼容处理 |
| [Changelog](CHANGELOG.md) | 1.0 发布说明、后续改动和归档的历史版本 |
| [贡献指南](CONTRIBUTING.md) | 开发约定与贡献要求 |
| [安全策略](SECURITY.md) | 安全边界与私密漏洞报告 |
| [发布流程](RELEASE.md) | 打包、checksum、SBOM 和发布门禁 |

## 许可证

MIT，以 [pyproject.toml](pyproject.toml) 的声明为准。
