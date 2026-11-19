---
description: "Permissions, sandbox, audit, hooks, scheduling, and the execution engine API reference."
---
# Governance & Engine

Governance covers permission policy, OS sandboxing, audit logging, event
hooks, and scheduled tasks. The engine layer is the turn execution
kernel: model invocation, tool coordination, prompt assembly, acceptance
gates, and deliverable collection.

See [Security, Platform & MCP](../guides/security-and-platform.md) and
[Subagents, Hooks & Memory](../guides/agent-patterns.md) for the
user-facing flows (`permissions.toml`, `hooks.toml`, `/cron`, `/rule`).

## Permissions

::: dashscope_agenticx.permission.PermissionPolicy

::: dashscope_agenticx.permission.load_permission_policy

::: dashscope_agenticx.permission.load_permissions_file

::: dashscope_agenticx.permission.get_permission_policy

::: dashscope_agenticx.permission.set_permission_policy

::: dashscope_agenticx.permission.configure_permission_policy

## Sandbox

::: dashscope_agenticx.sandbox.detect_backend

::: dashscope_agenticx.sandbox.available

::: dashscope_agenticx.sandbox.is_enabled

::: dashscope_agenticx.sandbox.set_enabled

::: dashscope_agenticx.sandbox.build_argv

::: dashscope_agenticx.sandbox.seatbelt_profile

::: dashscope_agenticx.sandbox.bwrap_argv

::: dashscope_agenticx.sandbox.is_sandboxed_path

## Audit

::: dashscope_agenticx.audit.AuditEvent

::: dashscope_agenticx.audit.AuditLogger

::: dashscope_agenticx.audit.get_audit_logger

::: dashscope_agenticx.audit.configure_audit_logger

## Hooks

::: dashscope_agenticx.hooks.Hook

::: dashscope_agenticx.hooks.HookBus

::: dashscope_agenticx.hooks.HookContext

::: dashscope_agenticx.hooks.HookActionResult

::: dashscope_agenticx.hooks.HookDispatchResult

::: dashscope_agenticx.hooks.create_hook_bus

## Scheduling

::: dashscope_agenticx.scheduler.Scheduler

::: dashscope_agenticx.scheduler.CronJob

::: dashscope_agenticx.scheduler.ScheduleSpec

::: dashscope_agenticx.scheduler.SkillInvocation

::: dashscope_agenticx.scheduler.parse_cron_add

::: dashscope_agenticx.scheduler.next_cron_fire

## Execution kernel

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

## Subagents & delegation

::: dashscope_agenticx.agents.subagents.SubagentDef

::: dashscope_agenticx.agents.subagents.discover_subagents

::: dashscope_agenticx.agents.subagent.bind_subagent_context

::: dashscope_agenticx.agents.subagent.register_subagent_tool

::: dashscope_agenticx.agents.delegate.bind_delegate_context