---
description: "权限、沙箱、审计、Hook、调度与执行引擎 API 参考。"
---
# 治理与执行引擎

治理涵盖权限策略、操作系统沙箱、审计日志、事件 Hook 与定时任务。
引擎层是轮次执行内核：模型调用、工具协调、提示词组装、验收门控与
交付物收集。

面向用户的流程（`permissions.toml`、`hooks.toml`、`/cron`、`/rule`）
参见[安全、平台与 MCP](../guides/security-and-platform.md)与
[子代理、Hooks 与记忆](../guides/agent-patterns.md)。

## 权限

::: dashscope_agenticx.permission.PermissionPolicy

::: dashscope_agenticx.permission.load_permission_policy

::: dashscope_agenticx.permission.load_permissions_file

::: dashscope_agenticx.permission.get_permission_policy

::: dashscope_agenticx.permission.set_permission_policy

::: dashscope_agenticx.permission.configure_permission_policy

## 沙箱

::: dashscope_agenticx.sandbox.detect_backend

::: dashscope_agenticx.sandbox.available

::: dashscope_agenticx.sandbox.is_enabled

::: dashscope_agenticx.sandbox.set_enabled

::: dashscope_agenticx.sandbox.build_argv

::: dashscope_agenticx.sandbox.seatbelt_profile

::: dashscope_agenticx.sandbox.bwrap_argv

::: dashscope_agenticx.sandbox.is_sandboxed_path

## 审计

::: dashscope_agenticx.audit.AuditEvent

::: dashscope_agenticx.audit.AuditLogger

::: dashscope_agenticx.audit.get_audit_logger

::: dashscope_agenticx.audit.configure_audit_logger

## Hook

::: dashscope_agenticx.hooks.Hook

::: dashscope_agenticx.hooks.HookBus

::: dashscope_agenticx.hooks.HookContext

::: dashscope_agenticx.hooks.HookActionResult

::: dashscope_agenticx.hooks.HookDispatchResult

::: dashscope_agenticx.hooks.create_hook_bus

## 调度

::: dashscope_agenticx.scheduler.Scheduler

::: dashscope_agenticx.scheduler.CronJob

::: dashscope_agenticx.scheduler.ScheduleSpec

::: dashscope_agenticx.scheduler.SkillInvocation

::: dashscope_agenticx.scheduler.parse_cron_add

::: dashscope_agenticx.scheduler.next_cron_fire

## 执行内核

::: dashscope_agenticx.engine.ModelInvoker

::: dashscope_agenticx.engine.RunGuards

::: dashscope_agenticx.engine.ToolCoordinator

::: dashscope_agenticx.engine.SessionPersistence

::: dashscope_agenticx.executor.Executor

::: dashscope_agenticx.executor.ToolOutcome

::: dashscope_agenticx.executor.classify_tool_text

::: dashscope_agenticx.prompt_pipeline.PromptContext

::: dashscope_agenticx.prompt_pipeline.default_pipeline

::: dashscope_agenticx.acceptance.AcceptanceGate

::: dashscope_agenticx.acceptance.GateVerdict

::: dashscope_agenticx.acceptance.discover_acceptance_command

::: dashscope_agenticx.deliverable.collect_deliverables

::: dashscope_agenticx.deliverable.surface_deliverables

## 子代理与委托

::: dashscope_agenticx.agents.subagents.SubagentDef

::: dashscope_agenticx.agents.subagents.discover_subagents

::: dashscope_agenticx.agents.subagent.bind_subagent_context

::: dashscope_agenticx.agents.subagent.register_subagent_tool

::: dashscope_agenticx.agents.delegate.bind_delegate_context