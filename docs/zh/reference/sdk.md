---
description: "AgenticX 可嵌入 SDK 入口与配置 API 参考。"
---
# SDK 与配置

可嵌入 SDK 位于 `dashscope_agenticx.sdk` — 在 Agent 运行时之上提供
稳定、带类型的接口，供脚本、CI 流水线、IDE 插件及其他 Python 程序
使用。面向任务的演练参见[嵌入式 SDK](../guides/sdk.md)；配置文件与
环境变量参见[配置](../configuration.md)。

```python
from dashscope_agenticx.sdk import run_once_sync

print(run_once_sync("Summarize README.md"))
```

!!! note "导入路径"
    请从 `dashscope_agenticx.sdk` 导入。顶层包重新导出了最常用的辅助
    函数，但 `sdk` 模块才是文档化的完整接口。

## 配置解析

`Config.load()` 按优先级从低到高合并三层：

1. 全局配置：`~/.config/AgenticX/config.toml`
2. 工作区配置：`./.agenticx/config.toml`
3. 环境变量填补剩余空缺（`DASHSCOPE_API_KEY`、
   `ANTHROPIC_API_KEY`、`OPENAI_API_KEY`、`AGENTICX_WORKSPACE`、
   `AGENTICX_REGION`、`AGENTICX_MAAS_API_KEY`、…）

`AGENTICX_PROVIDER`、`AGENTICX_MODEL`、`AGENTICX_OPENAI_BASE_URL` 与
`AGENTICX_MCP_SERVERS` 强制覆盖解析结果。SDK 中的 `load_config()`
仅在解析后的配置仍有空缺时应用调用方提供的参数。

`Config.api_key` 是一个属性，根据 `Config.provider` 路由到对应
Provider 的密钥字段（`tongyi_api_key`、`anthropic_api_key`、
`openai_api_key`）。

## SDK 入口

::: dashscope_agenticx.sdk
    options:
      heading_level: 3
      members:
        - load_config
        - create_agent
        - run_events
        - run_stream
        - run_once
        - run_once_sync
        - run_interactive

## 配置（Config） { #config }

::: dashscope_agenticx.config.Config

关键默认值：`max_turns=50`、`timeout=30`（每次模型请求的秒数）、
`platform="bailian"`、`memory_enabled=True`、`session_persist=True`。

## 嵌套配置类型

::: dashscope_agenticx.config.MCPServerConfig

::: dashscope_agenticx.config.SubagentConfig

::: dashscope_agenticx.config.DelegationConfig

## 模型辅助函数

::: dashscope_agenticx.config.DEFAULT_MODEL

::: dashscope_agenticx.config.context_window_for_model

::: dashscope_agenticx.config.normalize_model_name

::: dashscope_agenticx.config.register_custom_model

::: dashscope_agenticx.config.is_vision_model

::: dashscope_agenticx.config.is_audio_model