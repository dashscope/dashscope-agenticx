---
description: "在 Python 应用中嵌入 AgenticX：run_once、流式输出、带类型的事件、会话与交互式循环。"
---
# 嵌入式 SDK

AgenticX 在 `dashscope_agenticx.sdk` 中暴露了一个精简的公共 SDK。每个
入口都会执行**完整的一轮 Agent 对话** — 模型调用加上工具执行 — 而不是
单纯的聊天补全。

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

## 一次性对话

`run_once_sync` 是脚本与 CI 最简单的入口：它会流式执行完整的一轮对话、
执行所有工具调用，并返回最终的助手文本。

```python
from dashscope_agenticx.sdk import run_once_sync

reply = run_once_sync("Inspect this project's structure without changing files")
print(reply)
```

对应的异步版本是 `run_once`。两者都会在内部启用一次性运行的守护与
验收行为；工具执行仍遵循你的权限策略（默认 `confirm_mode = "all"` —
参见[安全](security-and-platform.md#permissions)）。

## 配置覆盖

`load_config` 读取常规的分层配置（全局 → 工作区 → 环境）；显式参数
只填补缺失值，不会覆盖配置或环境已设置的内容：

```python
from dashscope_agenticx.sdk import load_config, run_once_sync

config = load_config(provider="tongyi", model="qwen-plus")
reply = run_once_sync("Summarize this directory", config=config)
```

如需无视配置强制指定 Provider/模型，请在环境中设置
`AGENTICX_PROVIDER` / `AGENTICX_MODEL`。参见
[配置](../configuration.md)。

## 流式文本

`run_stream` 以异步迭代器的方式产出响应文本块。工具展示文本与模型输出
交错出现，与终端中的显示完全一致：

```python
import asyncio
from dashscope_agenticx.sdk import run_stream

async def main():
    async for chunk in run_stream("Write a haiku about the sea"):
        print(chunk, end="", flush=True)

asyncio.run(main())
```

如果提前停止消费，请关闭生成器以释放 Agent 的运行时：

```python
import contextlib

async with contextlib.aclosing(run_stream("Long task")) as chunks:
    async for chunk in chunks:
        if "STOP" in chunk:
            break
```

便捷封装（`run_once`、`run_stream`、`run_events`）会自行创建并关闭
Agent。当你自行管理 Agent 时，请使用 `async with` 或调用
`await agent.aclose()`。

## 带类型的事件

`run_events` 是规范的执行 API。它产出 `AgentEventEnvelope` 对象
（别名 `AgentEvent`），包含 `type`、`payload` 字典以及关联字段
（`session_id`、`run_id`、`turn_id`、`sequence`、`event_id`、
`timestamp`）。请消费事件，而不是解析终端输出：

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

事件契约要点：

- `MESSAGE_DELTA` 载荷携带 `text`；工具展示增量还额外携带
  `kind="tool"`、`tool_call_id` 与 `name`。如果只需要模型文本，请按
  `kind` 过滤。
- `TURN_FINISHED` 携带最终的 `output` 文本与所使用的 `tools`。
- 失败时会发出 `ERROR` 事件（`error_type`、`message`），**随后重新
  抛出**异常 — 如需优雅降级，两者都要处理。没有统一的异常体系；请在
  迭代器外围捕获标准异常。
- 一个 `Agent` 同一时间只运行一个事件流；在同一 Agent 上并发发起
  第二个 `run_events` 会抛出 `RuntimeError`。

完整的 15 种事件表见
[Agent 与事件参考](../reference/agent.md#event-types)。

## 会话与自定义 Agent

`create_agent` 提供一个显式的 `Agent`，支持覆盖系统提示词与可选的
持久化会话：

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

指定 `session_id` 后，历史会持久化到
`.agenticx/session/<id>/history.json`，进程重启后仍然保留。不指定时，
对话仅存在于内存中。

## 嵌入交互式循环

`run_interactive` 以你自己的产品标识启动完整的 AgenticX 交互体验
（REPL 或 TUI）— DashScope SDK Expert 正是这样打包的：

```python
from dashscope_agenticx.sdk import run_interactive

run_interactive(
    system_prompt="You are an expert on myproduct.",
    app_name="MyProduct Expert",
    prompt_symbol="myproduct> ",
    command="myproduct",
)
```

它接受 Provider/模型默认值、`api_key`/`base_url`、用于文档查询的
`sdk_index`，以及强制指定界面的 `tui=True/False`。

## 更底层的集成

如需完全控制，可直接用 `RuntimeBuilder` 构建 Runtime 并从中创建
Agent；参见
[Agent、Runtime 与事件参考](../reference/agent.md)。localhost
[Server](security-and-platform.md#local-http-api) 通过 HTTP 暴露同样的
事件流，供非 Python 客户端使用。