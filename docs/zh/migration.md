---
description: "从旧版 AgenticCLI 迁移到 DashScope AgenticX 1.0：命名、目录、凭据与兼容性。"
---
# 迁移到 1.0

本指南介绍如何从旧版 AgenticCLI 迁移到
DashScope AgenticX `{{ agenticx_version }}`。

## 当前标识

| 方面 | AgenticX 1.0 |
|---|---|
| 产品 | AgenticX / DashScope AgenticX |
| 发行包 | `pip install dashscope-agenticx`（按需安装扩展，如 `"dashscope-agenticx[all]"`） |
| 导入 | `dashscope_agenticx`、`dashscope_agenticx.sdk` |
| 命令 | `agenticx` — 没有官方短别名 |
| 应用 | `agenticx app install`；`/expert` 管理角色模板 |
| 工作区 | `.agenticx/` |
| 环境变量 | `AGENTICX_*` 变量 |
| 全局配置 | 应用名 `AgenticX` 对应的平台标准目录（通过 `platformdirs`）；可用 `AGENTICX_HOME` 覆盖 |
| 事件 | 通过 `run_events()` 消费 `AgentEventEnvelope` |

!!! warning "不要安装名为 `agenticx` 的 PyPI 包"
    无关的 `agenticx` 发行包是另一个项目。
    官方发行包是 `dashscope-agenticx`。

请将旧版 `AGENTICCLI_*` 与 `ACLI_*` 环境变量重命名为对应的
`AGENTICX_*` 变量。可选扩展包（TUI、Provider SDK、web、browser、
media）需显式安装 — 参见[快速开始](quickstart.md#optional-extras)。

专家下载默认使用
`https://github.com/dashscope/dashscope-agenticx-experts.git` 的 `main` 分支。
显式设置 `expert_repo` 或使用 `--repo` 标志可选择其他来源。
目录仓库仅使用 `apps/<name>/`；不支持旧版顶层 Expert 目录。

## 单向目录迁移

工作区迁移优先选择 `.agenticcli/`，否则回退到 `.acli/`。全局迁移优先
选择旧版平台标准的 `AgenticCLI` 目录，否则回退到 `~/.acli/`。选定的
源目录会被一次性迁移到 `.agenticx/`（工作区）或平台标准的 `AgenticX`
目录（全局）。

- 当源与目标支持时，使用原子重命名。
- 否则进行带校验的复制。复制后源目录会被保留；确认结果无误后请手动
  删除。
- 如果目标目录已存在，两个目录都保持原样：不合并、不覆盖、不向其
  迁移。
- 写入只发生在新的位置。不会向旧目录双写；迁移不会重复执行，也不会
  把变更复制回去。

## 凭据

新凭据以操作系统 keyring 引用的形式写入，服务名为
`dashscope-agenticx`。读取时保留对旧版 `agenticcli` keyring 服务的
回退，因此现有引用仍可解析；旧版 `ENC:` 值也可读取以便迁移。

新的保存操作不会向旧服务双写密钥，也不会在 TOML 中持久化可逆的
API Key 密文。不要将旧版 `ENC:` 值视为安全加密 — 请重新保存密钥，
将其移入操作系统 keyring。

## 不会自动迁移的内容

- 导入语句（`agenticcli` → `dashscope_agenticx`）与环境变量名需要在
  你的代码和 Shell 配置文件中显式更新。
- 已存在的目标目录绝不会被合并；请手动解决冲突。
- 专家/App 的安装契约已变更：角色模板位于目录仓库的 `apps/<name>/`
  下；带版本的 App Bundle 使用 `agenticx app …` 管理，而非 `/expert`。

治理已迁移配置的信任与权限模型参见
[安全与兼容性](guides/security-and-platform.md)。