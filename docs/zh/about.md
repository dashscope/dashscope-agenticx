---
description: "关于 DashScope AgenticX：产品标识、环境要求、定位与项目边界。"
---
# 关于

**DashScope AgenticX** 是一个用于构建和运行领域专家的 Python Agent
Runtime。它通过统一的运行时将模型、工具、Skill、权限与持久化会话连接
起来，并提供终端界面以及面向嵌入式应用的 API。

当前版本：**{{ agenticx_version }}**。

## 产品标识

| 方面 | 值 |
|---|---|
| 产品 | AgenticX / DashScope AgenticX |
| 发行包 | [`dashscope-agenticx`](https://pypi.org/project/dashscope-agenticx/) |
| 导入命名空间 | `dashscope_agenticx` |
| 命令 | `agenticx`（无官方短别名） |
| 工作区 | `.agenticx/` |
| 环境变量 | `AGENTICX_*` |
| 全局应用名 | `AgenticX`（通过 `platformdirs` 使用平台标准目录） |
| 许可证 | MIT |
| 源码 | [github.com/dashscope/dashscope-agenticx](https://github.com/dashscope/dashscope-agenticx) |

PyPI 上名为 `agenticx` 的发行包是**另一个无关的项目**。
请安装 `dashscope-agenticx`。

## 环境要求 { #requirements }

- 核心 Runtime、CLI 与 SDK 需要 **Python 3.10+**。
- 基于 Pillow 的 `vision` 与 `media` 扩展需要 **Python 3.11+**；
  在 Python 3.10 上，`all` 扩展不包含 Pillow。
- 云端 Provider 需要模型 Provider 的 API Key（例如默认
  Tongyi/DashScope Provider 的 `DASHSCOPE_API_KEY`），或使用
  `ollama` 等本地 Provider 实现完全离线运行。
- 可选能力（TUI、Provider SDK、web、browser、语音、摄像头）以扩展
  形式安装，并非核心 Runtime 的前置条件。

CI 在 Python 3.10–3.13 上测试，并在 Ubuntu/macOS/Windows 上进行冒烟
检查。这些检查并不保证每个可选设备或沙箱后端在所有操作系统上都能
工作。

## AgenticX 是什么

- 一个可直接在终端中运行的本地助手。
- 一个可嵌入的 Python 运行时（`run_once`、`run_events`、`create_agent`、
  `RuntimeBuilder`），用于应用集成。
- 一个将领域专家打包为带版本 App Bundle 的打包层，内置
  DashScope SDK Expert 与 Bailian Expert 模板。

## AgenticX 不是什么

- **不是托管服务。** 它运行在你的进程中；localhost Server 是一个
  基于 token 认证的本地 API，而非面向互联网的部署。
- **不是加固的操作系统沙箱。** 操作系统沙箱支持（Seatbelt/bubblewrap）
  属于纵深防御；可用性与限制取决于具体后端。
- **不是完整的工作流平台。** 语音、摄像头、浏览器、Web 搜索、调度器
  与实验性功能均为可选项。

校验和只能证明内容一致性，不能证明发布者身份；本地信任标志也不是
经过验证的数字签名。安装第三方应用前，请阅读
[安全指南](guides/security-and-platform.md)与仓库中的 `SECURITY.md`。

## 依赖

核心安装保持精简。可选扩展包会引入 Provider SDK、Textual TUI、
web/browser 工具以及媒体（语音/摄像头/视觉）依赖。安装依赖并不会为
App 授予权限。扩展列表参见[快速开始](quickstart.md#optional-extras)。

## 许可证

MIT，声明于 `pyproject.toml`。