---
description: "Agent, Runtime, and typed event stream API reference for AgenticX."
---
# Agent, Runtime & Events

An `Agent` runs one conversation turn at a time and emits typed events.
The `Runtime` wires configuration, providers, tools, and platforms
together; `RuntimeBuilder` is the composition entry point used by both
the CLI and the [SDK](sdk.md).

## Lifecycle

`Agent` supports `async with` and explicit `await agent.aclose()`. The
SDK helpers (`run_once`, `run_stream`, `run_events`) close the agent for
you; when you drive `Agent` directly, close it yourself:

```python
import asyncio

from dashscope_agenticx.sdk import create_agent


async def main():
    async with create_agent(session_id="demo") as agent:
        async for chunk in agent.run_stream("Hello!"):
            print(chunk, end="", flush=True)


asyncio.run(main())
```

Both `run_events()` and `run_stream()` are single-pass async generators.
To stop early, close the generator (`contextlib.aclosing`) — see
[Embedding the SDK](../guides/sdk.md).

## Event types

Every event is an `AgentEventEnvelope` with a type from `AgentEventType`:

| Type | Meaning |
|---|---|
| `run.started` | A turn began |
| `message.delta` | Incremental assistant text |
| `reasoning.delta` | Incremental reasoning/thinking text |
| `plan.updated` | The agent's plan changed |
| `tool.started` / `tool.finished` | Tool execution boundaries |
| `permission.requested` / `permission.resolved` | Approval flow |
| `usage.updated` | Token usage accounting |
| `artifact.created` | A deliverable file was produced |
| `compression.completed` | Context compression ran |
| `provider.switched` | Fallback/routing changed provider |
| `sandbox.status` | Sandbox backend state |
| `turn.finished` | The turn completed |
| `error` | A recoverable or fatal error surfaced |

Errors are delivered as `error` events inside the stream — AgenticX has
no unified exception hierarchy for model/tool failures. See
[Eval](eval.md#exceptions) for the few control-flow exceptions.

## Agent

::: dashscope_agenticx.agent.Agent

## Runtime

::: dashscope_agenticx.runtime.Runtime

::: dashscope_agenticx.runtime.RuntimeBuilder

::: dashscope_agenticx.runtime.RuntimeContext

::: dashscope_agenticx.runtime.import_builtin_tools

::: dashscope_agenticx.runtime.create_runtime_memory

## Events

::: dashscope_agenticx.events.AgentEventType

::: dashscope_agenticx.events.AgentEventEnvelope

::: dashscope_agenticx.events.AgentEvent

::: dashscope_agenticx.events.ToolDisplayText

::: dashscope_agenticx.events.iter_message_deltas