---
description: "Memory managers, session persistence, local history, and context compression API reference."
---
# Memory & Sessions

AgenticX keeps conversation state at three levels: in-turn session
history, persistent per-session files under `.agenticx/session/<id>/`,
and long-term memory via the configured platform (Bailian by default,
local otherwise). Context compression keeps long runs inside the model
window.

See [Subagents, Hooks & Memory](../guides/agent-patterns.md) for the
user-facing `/memory`, `/session`, and `/profile` flows.

## Memory manager

::: dashscope_agenticx.memory.manager.MemoryManager

::: dashscope_agenticx.memory.manager.SessionMemory

::: dashscope_agenticx.memory.manager.PersistentMemory

## Sessions

::: dashscope_agenticx.session.SessionManager

::: dashscope_agenticx.session.SessionMeta

::: dashscope_agenticx.session.get_session_manager

::: dashscope_agenticx.session.set_session_manager

## Session event log

::: dashscope_agenticx.session_events.SessionEventLog

::: dashscope_agenticx.session_events.project_session_state

::: dashscope_agenticx.session_events.latest_snapshot_messages

## Local platform storage

::: dashscope_agenticx.platforms.local.memory.LocalMemoryClient

::: dashscope_agenticx.platforms.local.history.store_history

::: dashscope_agenticx.platforms.local.history.list_history

::: dashscope_agenticx.platforms.local.history.search_history

::: dashscope_agenticx.platforms.local.history.export_history

## Context compression

::: dashscope_agenticx.compression.compress_messages

::: dashscope_agenticx.compression.should_compress

::: dashscope_agenticx.compression.estimate_tokens

::: dashscope_agenticx.compression.estimate_message_tokens

::: dashscope_agenticx.compression.preserve_recent_messages

::: dashscope_agenticx.compression.shrink_old_tool_messages