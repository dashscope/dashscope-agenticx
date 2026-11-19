---
description: "AgenticX 的 Agent、Runtime 与带类型事件流 API 参考。"
---
# Agent、运行时与事件

`Agent` 同一时间运行一轮对话，并发出带类型的事件。`Runtime` 将配置、
Provider、工具与平台连接在一起；`RuntimeBuilder` 是 CLI 与
[SDK](sdk.md) 共用的组装入口。

## 生命周期

`Agent` 支持 `async with` 与显式的 `await agent.aclose()`。SDK 辅助
函数（`run_once`、`run_stream`、`run_events`）会替你关闭 Agent；当你
直接驱动 `Agent` 时，请自行关闭：

```python
import asyncio

from dashscope_agenticx.sdk import create_agent


async def main():
    async with create_agent(session_id="demo") as agent:
        async for chunk in agent.run_stream("Hello!"):
            print(chunk, end="", flush=True)


asyncio.run(main())
```

`run_events()` 与 `run_stream()` 都是单遍异步生成器。如需提前停止，
请关闭生成器（`contextlib.aclosing`）— 参见
[嵌入式 SDK](../guides/sdk.md)。

## 事件类型 { #event-types }

每个事件都是 `AgentEventEnvelope`，其类型来自 `AgentEventType`：

| 类型 | 含义 |
|---|---|
| `run.started` | 一轮对话开始 |
| `message.delta` | 助手文本增量 |
| `reasoning.delta` | 推理/思考文本增量 |
| `plan.updated` | Agent 的计划发生变化 |
| `tool.started` / `tool.finished` | 工具执行边界 |
| `permission.requested` / `permission.resolved` | 批准流程 |
| `usage.updated` | token 用量统计 |
| `artifact.created` | 产出了交付文件 |
| `compression.completed` | 执行了上下文压缩 |
| `provider.switched` | 回退/路由切换了 Provider |
| `sandbox.status` | 沙箱后端状态 |
| `turn.finished` | 本轮对话完成 |
| `error` | 出现了可恢复或致命错误 |

错误以流内 `error` 事件的形式传递 — AgenticX 对模型/工具失败没有统一
的异常体系。少数控制流异常参见[评测](eval.md#exceptions)。

## Agent

::: dashscope_agenticx.agent.Agent

## Runtime

::: dashscope_agenticx.runtime.Runtime

::: dashscope_agenticx.runtime.RuntimeBuilder

::: dashscope_agenticx.runtime.RuntimeContext

::: dashscope_agenticx.runtime.import_builtin_tools

::: dashscope_agenticx.runtime.create_runtime_memory

## 事件

::: dashscope_agenticx.events.AgentEventType

::: dashscope_agenticx.events.AgentEventEnvelope

::: dashscope_agenticx.events.AgentEvent

::: dashscope_agenticx.events.ToolDisplayText

::: dashscope_agenticx.events.iter_message_deltas