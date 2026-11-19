---
description: "DashScope AgenticX 1.0.2、1.0.1 与 1.0.0 发行说明。"
---
# 发行说明

AgenticX 遵循[语义化版本](https://semver.org/spec/v2.0.0.html)。
完整历史记录见仓库中的
[`CHANGELOG.md`](https://github.com/dashscope/dashscope-agenticx/blob/main/CHANGELOG.md)；
本页汇总稳定的 1.0 系列发布。

## 1.0.2 — 2026-11-19

### 新增

- 双语（English / 中文）文档站：快速开始、配置、命令行与斜杠命令参考、
  迁移指南、专题指南（嵌入式 SDK、工具与技能、子代理与记忆、安全与平台）、
  开发者指南，以及由源码生成的 API 参考。
- GitHub Actions 工作流：拉取请求时以 `mkdocs build --strict` 构建站点，
  推送到 `main` 时部署到 GitHub Pages。
- README 增加指向已发布文档站的链接。

### 变更

- 文档与代码一致性守护接受 README 使用的远程 DashScope logo URL。

### 修复

- 浏览器工具 docstring 参数缩进，使 API 参考在 `mkdocs build --strict`
  下干净解析。

## 1.0.1 — 2026-11-11

### 新增

- 端到端的 App Catalog 安装与验证快速入门，包括独立的 Expert 激活
  步骤与一次性 `agenticx -c` 用法。
- 通过 `x-dashscope-sdk-client` 与 `x-dashscope-sdk-session-id` 实现
  DashScope 与 MaaS 的请求归因，覆盖 Native、OpenAI 兼容、
  Anthropic 兼容、MCP、ASR 与 TTS 请求。第三方端点不会收到这些
  请求头。

### 变更

- 专家管理统一使用 `agenticx expert`、`/expert`、`expert_repo` 与
  `expert_branch`；移除了旧版 Example 命令与配置名称。
- 中英文指南覆盖当前的 CLI、交互、Skill、Session 与 Server 命令，
  并厘清了 App 安装与 Expert 激活的区别。

## 1.0.0 — 2026-11-10

DashScope AgenticX Runtime 的首个稳定版本。

### 新增

- TUI 对话记录块：Markdown 回复、轮次编号、可折叠的工具输出，以及
  展开的每轮/会话统计。
- 工具结果截断元数据，以及提示词构成/用量调用详情。
- 完整的单元测试矩阵，以及在 main 分支推送与 Pull Request 上运行的
  全仓库 pre-commit 检查。
- Tag 构建会准备 wheel/源码发行包与平台二进制文件，并附带 SHA-256
  校验和，放入草稿 GitHub Release 供最终审核。

### 变更

- 包、CLI、SDK 与内置 App 的运行时版本要求统一使用稳定版本
  `1.0.0`。
- 专家下载默认使用公开的 `dashscope/dashscope-agenticx-experts`
  仓库 `main` 分支；仍支持显式覆盖仓库。App Catalog 下载仅读取
  `apps/<name>/`，不再回退到旧版顶层 Expert 目录。
- 公开指南与迁移文档位于仓库根目录；源码发行包排除内部的 `md/`
  目录。

### 修复（安全与运行时要点）

- MCP/会话/权限/扩展状态归运行时本地所有，子代理授权缓存同样隔离，
  并在异常与流提前关闭时进行确定性清理。
- 默认确认覆盖写入类工具；显式拒绝策略无法通过 AUTO 或自动批准绕过。
  并行委托默认仅使用只读工具，且不继承授权许可。
- App Bundle 路径校验、清单权限类型，以及安装写入前的权限确认。
  即使请求覆盖，专家模板也会拒绝隐式可执行代码。
- 本地 Server 执行拒绝自动本地授权，并拒绝非法的 HTTP 正文长度；
  覆盖 IPv6 回环与错误响应脱敏。
- 运行 Hook 与自定义 Shell 工具将模型可控的值作为原生进程参数传递，
  而非 Shell 语法；参数化管道、Shell 解释器以及动态可执行文件/代码/
  模块名均被拒绝。
- 无论长度或已知密钥模式如何，凭据赋值与私钥块都会从文本、事件与
  会话载荷中脱敏；TUI 输入历史在保存与迁移时对凭据脱敏。
- 专家下载/恢复拒绝路径穿越、符号链接，以及对受管备份目录的覆盖。
- 结构化的推理、计划、压缩与 Provider 回退事件，与消息/工具/权限
  事件互补，载荷均经过脱敏。
- 基于 Pillow 的 vision/media 需要 Python 3.11+；Python 3.10 保留
  基础 Runtime 支持，且该版本上的 `all` 扩展不包含 Pillow。

## 升级

旧版命名、单向目录迁移与凭据处理参见[迁移到 1.0](migration.md)。