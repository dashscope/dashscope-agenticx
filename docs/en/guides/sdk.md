---
description: "Embed AgenticX in Python applications: run_once, streaming, typed events, sessions, and the interactive loop."
---
# Embedding the SDK

AgenticX exposes a small public SDK in `dashscope_agenticx.sdk`. Every entry
point runs a **complete agent turn** — model invocation plus tool execution —
not a bare chat completion.

```python
from dashscope_agenticx.sdk import (
    load_config,
    create_agent,
    run_once,
    run_once_sync,
    run_stream,
    run_events,
    run_interactive,
)
```

## One-shot turns

`run_once_sync` is the easiest entry point for scripts and CI: it streams the
full turn, executes any tool calls, and returns the final assistant text.

```python
from dashscope_agenticx.sdk import run_once_sync

reply = run_once_sync("Inspect this project's structure without changing files")
print(reply)
```

The async equivalent is `run_once`. Both enable one-shot guards and
acceptance behavior internally; tool execution still respects your
permission policy (default `confirm_mode = "all"` — see
[Security](security-and-platform.md#permissions)).

## Configuration overrides

`load_config` reads the normal layered configuration (global → workspace →
environment); explicit arguments only fill in missing values, they do not
override what configuration or environment already set:

```python
from dashscope_agenticx.sdk import load_config, run_once_sync

config = load_config(provider="tongyi", model="qwen-plus")
reply = run_once_sync("Summarize this directory", config=config)
```

To force a provider/model regardless of configuration, set
`AGENTICX_PROVIDER` / `AGENTICX_MODEL` in the environment. See
[Configuration](../configuration.md).

## Streaming text

`run_stream` yields response text chunks as an async iterator. Tool display
text is interleaved with model output, exactly as the terminal shows it:

```python
import asyncio
from dashscope_agenticx.sdk import run_stream

async def main():
    async for chunk in run_stream("Write a haiku about the sea"):
        print(chunk, end="", flush=True)

asyncio.run(main())
```

If you stop consuming early, close the generator so the agent's runtime is
released:

```python
import contextlib

async with contextlib.aclosing(run_stream("Long task")) as chunks:
    async for chunk in chunks:
        if "STOP" in chunk:
            break
```

The convenience wrappers (`run_once`, `run_stream`, `run_events`) create and
close their own agent. When you manage an agent yourself, use `async with`
or call `await agent.aclose()`.

## Typed events

`run_events` is the canonical execution API. It yields
`AgentEventEnvelope` objects (alias `AgentEvent`) with a `type`, a `payload`
dict, and correlation fields (`session_id`, `run_id`, `turn_id`, `sequence`,
`event_id`, `timestamp`). Consume events instead of parsing terminal output:

```python
import asyncio
from dashscope_agenticx.sdk import run_events, AgentEventType

async def main():
    async for event in run_events("Summarize the project"):
        if event.type == AgentEventType.MESSAGE_DELTA:
            print(event.payload["text"], end="", flush=True)
        elif event.type == AgentEventType.TOOL_STARTED:
            print(f"\n[tool] {event.payload.get('name', '')}")
        elif event.type == AgentEventType.TURN_FINISHED:
            print("\n[done]")

asyncio.run(main())
```

Notes on the event contract:

- `MESSAGE_DELTA` payloads carry `text`; tool display deltas additionally
  carry `kind="tool"`, `tool_call_id`, and `name`. Filter on `kind` if you
  want model text only.
- `TURN_FINISHED` carries the final `output` text and the `tools` used.
- Failures emit an `ERROR` event (`error_type`, `message`) **and then
  re-raise** the exception — handle both if you need graceful degradation.
  There is no unified exception tree; catch standard exceptions around the
  iterator.
- An `Agent` runs one event stream at a time; a second concurrent
  `run_events` on the same agent raises `RuntimeError`.

The full 15-event table is in the
[Agent & Events reference](../reference/agent.md#event-types).

## Sessions and custom agents

`create_agent` gives you an explicit `Agent` with a system prompt override
and optional persistent session:

```python
import asyncio
from dashscope_agenticx.sdk import create_agent

async def main():
    async with create_agent(
        system_prompt="You are a careful code reviewer.",
        session_id="review-2026-11",
    ) as agent:
        async for chunk in agent.run_stream("Review src/app.py"):
            print(chunk, end="")
        # A second turn continues the same conversation history.
        async for chunk in agent.run_stream("Now suggest fixes."):
            print(chunk, end="")

asyncio.run(main())
```

With `session_id`, history persists under
`.agenticx/session/<id>/history.json` and survives process restarts. Without
it, the conversation lives only in memory.

## Embedding the interactive loop

`run_interactive` launches the full AgenticX interactive experience (REPL or
TUI) under your own product identity — this is how the DashScope SDK Expert
is packaged:

```python
from dashscope_agenticx.sdk import run_interactive

run_interactive(
    system_prompt="You are an expert on myproduct.",
    app_name="MyProduct Expert",
    prompt_symbol="myproduct> ",
    command="myproduct",
)
```

It accepts provider/model defaults, `api_key`/`base_url`, an `sdk_index`
for documentation lookup, and `tui=True/False` to force the interface.

## Lower-level integration

For full control, construct a Runtime directly with `RuntimeBuilder` and
create agents from it; see the
[Agent, Runtime & Events reference](../reference/agent.md). The localhost
[Server](security-and-platform.md#local-http-api) exposes the same event
stream over HTTP for non-Python clients.