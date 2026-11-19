---
description: "记忆管理器、会话持久化、本地历史与上下文压缩 API 参考。"
---
# 记忆与会话

AgenticX 在三个层面保存对话状态：轮内会话历史、
`.agenticx/session/<id>/` 下按会话持久化的文件，以及通过所配置平台
（默认百炼，否则本地）实现的长期记忆。上下文压缩使长时运行保持在
模型窗口之内。

面向用户的 `/memory`、`/session` 与 `/profile` 流程参见
[子代理、Hooks 与记忆](../guides/agent-patterns.md)。

## 记忆管理器

::: dashscope_agenticx.memory.manager.MemoryManager

::: dashscope_agenticx.memory.manager.SessionMemory

::: dashscope_agenticx.memory.manager.PersistentMemory

## 会话

::: dashscope_agenticx.session.SessionManager

::: dashscope_agenticx.session.SessionMeta

::: dashscope_agenticx.session.get_session_manager

::: dashscope_agenticx.session.set_session_manager

## 会话事件日志

::: dashscope_agenticx.session_events.SessionEventLog

::: dashscope_agenticx.session_events.project_session_state

::: dashscope_agenticx.session_events.latest_snapshot_messages

## 本地平台存储

::: dashscope_agenticx.platforms.local.memory.LocalMemoryClient

::: dashscope_agenticx.platforms.local.history.store_history

::: dashscope_agenticx.platforms.local.history.list_history

::: dashscope_agenticx.platforms.local.history.search_history

::: dashscope_agenticx.platforms.local.history.export_history

## 上下文压缩

::: dashscope_agenticx.compression.compress_messages

::: dashscope_agenticx.compression.should_compress

::: dashscope_agenticx.compression.estimate_tokens

::: dashscope_agenticx.compression.estimate_message_tokens

::: dashscope_agenticx.compression.preserve_recent_messages

::: dashscope_agenticx.compression.shrink_old_tool_messages