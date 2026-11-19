---
description: "AgenticX embeddable SDK entry points and configuration API reference."
---
# SDK & Config

The embeddable SDK lives in `dashscope_agenticx.sdk` — a stable, typed
surface on top of the agent runtime for scripts, CI pipelines, IDE
plugins, and other Python programs. For a task-oriented walkthrough see
[Embedding the SDK](../guides/sdk.md); for config files and environment
variables see [Configuration](../configuration.md).

```python
from dashscope_agenticx.sdk import run_once_sync

print(run_once_sync("Summarize README.md"))
```

!!! note "Import path"
    Import from `dashscope_agenticx.sdk`. The top-level package re-exports
    the most common helpers, but the `sdk` module is the documented,
    complete surface.

## Configuration resolution

`Config.load()` merges three layers, in increasing priority:

1. Global config: `~/.config/AgenticX/config.toml`
2. Workspace config: `./.agenticx/config.toml`
3. Environment variables fill remaining gaps (`DASHSCOPE_API_KEY`,
   `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `AGENTICX_WORKSPACE`,
   `AGENTICX_REGION`, `AGENTICX_MAAS_API_KEY`, …)

`AGENTICX_PROVIDER`, `AGENTICX_MODEL`, `AGENTICX_OPENAI_BASE_URL` and
`AGENTICX_MCP_SERVERS` force-override the resolved values.
`load_config()` in the SDK applies caller-supplied arguments only where
the resolved config still has gaps.

`Config.api_key` is a property that routes to the per-provider key field
(`tongyi_api_key`, `anthropic_api_key`, `openai_api_key`) based on
`Config.provider`.

## SDK entry points

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

## Config

::: dashscope_agenticx.config.Config

Key defaults: `max_turns=50`, `timeout=30` (seconds per model request),
`platform="bailian"`, `memory_enabled=True`, `session_persist=True`.

## Nested config types

::: dashscope_agenticx.config.MCPServerConfig

::: dashscope_agenticx.config.SubagentConfig

::: dashscope_agenticx.config.DelegationConfig

## Model helpers

::: dashscope_agenticx.config.DEFAULT_MODEL

::: dashscope_agenticx.config.context_window_for_model

::: dashscope_agenticx.config.normalize_model_name

::: dashscope_agenticx.config.register_custom_model

::: dashscope_agenticx.config.is_vision_model

::: dashscope_agenticx.config.is_audio_model