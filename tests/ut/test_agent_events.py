# -*- coding: utf-8 -*-
"""Typed AgentEvent stream regressions for the 1.0 runtime API."""
from __future__ import annotations

import pytest

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.engine import (
    ModelInvoker,
    RunGuards,
    SessionPersistence,
    ToolCoordinator,
)
from dashscope_agenticx.events import (
    AgentEvent,
    AgentEventEnvelope,
    AgentEventType,
)
from dashscope_agenticx.executor import Executor
from dashscope_agenticx.providers.base import LLMChunk, ToolCall
from dashscope_agenticx.tools.registry import (
    PermissionLevel,
    ToolDefinition,
    ToolRegistry,
)


@pytest.mark.asyncio
async def test_reasoning_events_are_typed_and_redacted():
    class Provider:
        async def chat(self, *args, **kwargs):
            from dashscope_agenticx.providers.base import LLMResponse

            return LLMResponse(content="summary")

        async def chat_stream(self, *args, **kwargs):
            yield LLMChunk(
                delta_reasoning_content="password=private-value",
                delta_content="done",
                finish_reason="stop",
            )

    agent = Agent(Provider(), Executor(), system_prompt="events")
    events = [event async for event in agent.run_events("think")]
    reasoning = [
        event
        for event in events
        if event.type == AgentEventType.REASONING_DELTA
    ]
    assert len(reasoning) == 1
    assert "private-value" not in reasoning[0].payload["text"]


@pytest.mark.asyncio
async def test_agent_context_manager_closes_provider_and_prevents_reuse():
    from unittest.mock import AsyncMock

    provider = AsyncMock()
    agent = Agent(provider, Executor(), system_prompt="events")
    async with agent:
        pass
    await agent.aclose()
    provider.aclose.assert_awaited_once()
    with pytest.raises(RuntimeError, match="closed"):
        await anext(agent.run_events("after close"))


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "name",
    ["create_plan", "complete_step", "clear_plan"],
)
async def test_successful_plan_tools_emit_snapshots(name):
    local_registry = ToolRegistry()
    agent = Agent(
        _TextProvider(),
        Executor(auto_approve=True),
        system_prompt="events",
        tool_registry=local_registry,
    )
    tracker = agent.memory_manager.session.plan
    tracker.create_plan("password=private-value", ["first", "second"])

    async def update():
        if name == "create_plan":
            return tracker.create_plan("password=private-value", ["new"])
        if name == "complete_step":
            tracker.mark_step_complete(0)
        else:
            tracker.clear_plan()
        return "updated"

    local_registry.register(
        ToolDefinition(
            name=name,
            description="plan update",
            func=update,
            permission=PermissionLevel.AUTO,
        ),
    )
    events = []
    agent._event_sink = events.append
    await agent._execute_tool(ToolCall(id="plan", name=name, arguments={}))
    changes = [
        event for event in events if event.type == AgentEventType.PLAN_UPDATED
    ]
    assert len(changes) == 1
    assert "private-value" not in str(changes[0].payload)
    if name == "clear_plan":
        assert changes[0].payload["plan"] is None
    elif name == "complete_step":
        assert changes[0].payload["plan"]["steps"][0]["completed"]


@pytest.mark.asyncio
async def test_compression_emits_message_counts(monkeypatch):
    from unittest.mock import AsyncMock

    agent = Agent(_TextProvider(), Executor(), system_prompt="events")
    agent.messages = [{"role": "user", "content": "old"}] * 4
    compressed = [{"role": "user", "content": "summary"}]
    monkeypatch.setattr(
        "dashscope_agenticx.compression.compress_messages",
        AsyncMock(return_value=compressed),
    )
    events = []
    agent._event_sink = events.append
    await agent._auto_compress()
    assert events[0].type == AgentEventType.COMPRESSION_COMPLETED
    assert events[0].payload == {"old_count": 4, "new_count": 1}


@pytest.mark.asyncio
@pytest.mark.parametrize("streaming", [False, True])
async def test_provider_fallback_emits_redacted_transition(streaming):
    from unittest.mock import AsyncMock

    from dashscope_agenticx.events import bind_provider_event_sink
    from dashscope_agenticx.providers.base import LLMResponse
    from dashscope_agenticx.providers.profile import (
        ProviderChain,
        ProviderProfile,
    )

    class Failing:
        async def chat_stream(self, *args, **kwargs):
            raise RuntimeError("timeout password=private-value")
            yield LLMChunk()

    first = Failing()
    first.chat = AsyncMock(
        side_effect=RuntimeError("timeout password=private-value"),
    )
    second = _TextProvider()
    second.chat = AsyncMock(return_value=LLMResponse(content="done"))
    profiles = [
        ProviderProfile(
            name=name,
            provider="openai",
            model=name,
            api_key="test",
        )
        for name in ("primary", "backup")
    ]
    chain = ProviderChain(profiles)
    chain._instances = {0: first, 1: second}
    events = []
    with bind_provider_event_sink(
        lambda kind, payload: events.append((kind, payload)),
    ):
        if streaming:
            assert [chunk async for chunk in chain.chat_stream([])]
        else:
            await chain.chat([])
    assert len(events) == 1
    assert events[0][0] == AgentEventType.PROVIDER_SWITCHED
    assert events[0][1]["to"]["provider"] == "backup"
    assert "private-value" not in str(events)


class _TextProvider:
    async def chat(self, *args, **kwargs):
        raise AssertionError("streaming path expected")

    async def chat_stream(self, messages, tools=None, response_format=None):
        del messages, tools, response_format
        yield LLMChunk(delta_content="hello ")
        yield LLMChunk(
            delta_content="world",
            usage={
                "input_tokens": 3,
                "output_tokens": 2,
                "total_tokens": 5,
            },
            finish_reason="stop",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("name", ["write_file", "delegate", "subagent_invoke"])
async def test_child_tool_restrictions_are_enforced_at_execution(name):
    from unittest.mock import MagicMock

    action = MagicMock(return_value="executed")
    local_registry = ToolRegistry()
    local_registry.register(
        ToolDefinition(
            name=name,
            description="restricted",
            func=action,
            permission=PermissionLevel.AUTO,
        ),
    )
    agent = Agent(
        _TextProvider(),
        Executor(auto_approve=True),
        system_prompt="restricted",
        tool_registry=local_registry,
        allowed_tools=["read_file"],
        allow_delegate=False,
    )
    result = await agent._execute_tool(
        ToolCall(id="blocked", name=name, arguments={}),
    )
    assert "not allowed" in result
    action.assert_not_called()


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "wrapped,truncated",
    [(True, False), (False, False), (True, True)],
)
async def test_model_invoker_recovers_arguments_before_trace_and_permission(
    wrapped,
    truncated,
):
    from unittest.mock import MagicMock

    runtime_registry = ToolRegistry()
    runtime_registry.register(
        ToolDefinition(
            name="run_command",
            description="run",
            permission=PermissionLevel.CONFIRM,
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        ),
    )
    arguments = (
        {"arguments": '{"command": "pwd"}'} if wrapped else {"command": "pwd"}
    )

    class Provider:
        async def chat_stream(self, *args, **kwargs):
            yield LLMChunk(
                tool_calls=[
                    ToolCall(
                        id="one",
                        name="run_command",
                        arguments=arguments,
                        truncated=truncated,
                    ),
                ],
                finish_reason="tool_calls",
            )

    agent = Agent(
        Provider(),
        Executor(),
        system_prompt="test",
        tool_registry=runtime_registry,
    )
    trace = MagicMock()
    agent.trace_logger = trace
    invocations = [item async for item in ModelInvoker().invoke(agent, [], [])]
    invocation = invocations[-1]
    expected = arguments if truncated else {"command": "pwd"}
    assert invocation.tool_calls[0].arguments == expected
    assert (
        trace.log_llm_call.call_args.kwargs["response"]["tool_calls"][0][
            "arguments"
        ]
        == expected
    )
    assert agent.executor.get_stats()["api_calls"] == 1
    assert agent.executor.get_stats()["usage_calls"] == 0


@pytest.mark.asyncio
async def test_model_usage_chunks_count_one_api_call():
    class Provider(_TextProvider):
        async def chat_stream(self, *args, **kwargs):
            yield LLMChunk(
                usage={
                    "input_tokens": 10,
                    "output_tokens": 1,
                    "total_tokens": 11,
                },
            )
            yield LLMChunk(
                delta_content="done",
                usage={
                    "input_tokens": 10,
                    "output_tokens": 2,
                    "total_tokens": 12,
                },
                finish_reason="stop",
            )

    agent = Agent(
        Provider(),
        Executor(),
        system_prompt="test",
        tool_registry=ToolRegistry(),
    )
    events = [event async for event in agent.run_events("test")]
    stats = agent.executor.get_stats()
    assert stats["api_calls"] == stats["usage_calls"] == 1
    assert stats["token_usage"]["total_tokens"] == 12
    assert (
        len(
            [
                event
                for event in events
                if event.type == AgentEventType.USAGE_UPDATED
            ],
        )
        == 2
    )


def test_event_envelope_name_and_engine_components_are_public():
    assert AgentEvent is AgentEventEnvelope
    event = AgentEventEnvelope(type=AgentEventType.RUN_STARTED)
    assert event.schema_version == "1.0"
    assert AgentEventType.PERMISSION_REQUESTED.value == "permission.requested"
    assert AgentEventType.ARTIFACT_CREATED.value == "artifact.created"

    agent = Agent(
        _TextProvider(),
        Executor(auto_approve=True),
        system_prompt="events",
    )
    assert isinstance(agent.model_invoker, ModelInvoker)
    assert isinstance(agent.tool_coordinator, ToolCoordinator)
    assert isinstance(agent.run_guards, RunGuards)
    assert isinstance(agent.session_persistence, SessionPersistence)


@pytest.mark.asyncio
async def test_run_events_emits_typed_ordered_metadata():
    agent = Agent(
        _TextProvider(),
        Executor(auto_approve=True),
        system_prompt="events",
        runtime_id="runtime-1",
        agent_id="agent-1",
    )

    events = [event async for event in agent.run_events("say hello")]
    types = [event.type for event in events]

    assert types[0] == AgentEventType.RUN_STARTED
    assert AgentEventType.MESSAGE_DELTA in types
    assert AgentEventType.USAGE_UPDATED in types
    assert types[-1] == AgentEventType.TURN_FINISHED
    assert [event.sequence for event in events] == list(
        range(1, len(events) + 1),
    )
    assert {event.runtime_id for event in events} == {"runtime-1"}
    assert {event.agent_id for event in events} == {"agent-1"}
    assert len({event.run_id for event in events}) == 1
    assert len({event.turn_id for event in events}) == 1
    assert events[-1].payload["output"] == "hello world"


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["ok", "x" * 9000])
async def test_run_events_emits_tool_lifecycle(value):
    runtime_registry = ToolRegistry()

    async def echo(value: str):
        return f"echo:{value}"

    runtime_registry.register(
        ToolDefinition(
            name="mcp_event_echo",
            description="echo",
            permission=PermissionLevel.AUTO,
            func=echo,
            parameters={
                "type": "object",
                "properties": {"value": {"type": "string"}},
                "required": ["value"],
            },
        ),
    )

    class Provider:
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            raise AssertionError("streaming path expected")

        async def chat_stream(
            self,
            messages,
            tools=None,
            response_format=None,
        ):
            del messages, tools, response_format
            self.calls += 1
            if self.calls == 1:
                yield LLMChunk(
                    tool_calls=[
                        ToolCall(
                            id="echo-1",
                            name="mcp_event_echo",
                            arguments={"value": value},
                        ),
                    ],
                    finish_reason="tool_calls",
                )
            else:
                yield LLMChunk(delta_content="done", finish_reason="stop")

    agent = Agent(
        Provider(),
        Executor(auto_approve=True),
        system_prompt="events",
        tool_registry=runtime_registry,
    )
    events = [event async for event in agent.run_events("echo")]

    started = [e for e in events if e.type == AgentEventType.TOOL_STARTED]
    finished = [e for e in events if e.type == AgentEventType.TOOL_FINISHED]
    assert len(started) == 1
    assert len(finished) == 1
    assert started[0].payload["tool_call_id"] == "echo-1"
    assert finished[0].payload["success"] is True
    expected = f"echo:{value}"
    assert finished[0].payload["result"] == expected[:8000]
    assert finished[0].payload["result_truncated"] is (len(expected) > 8000)
    tool_deltas = [
        event
        for event in events
        if event.type == AgentEventType.MESSAGE_DELTA
        and event.payload.get("kind") == "tool"
    ]
    assert len(tool_deltas) == 1
    assert tool_deltas[0].payload["tool_call_id"] == "echo-1"
    assert tool_deltas[0].payload["name"] == "mcp_event_echo"
    preview = expected[:200] + "..." if len(expected) > 200 else expected
    assert (
        tool_deltas[0].payload["text"] == f"\n[mcp_event_echo] →\n{preview}\n"
    )


@pytest.mark.asyncio
async def test_run_events_emits_permission_lifecycle():
    runtime_registry = ToolRegistry()

    async def guarded(secret: str):
        return "ok"

    runtime_registry.register(
        ToolDefinition(
            name="guarded",
            description="guarded",
            permission=PermissionLevel.CONFIRM,
            func=guarded,
            parameters={
                "type": "object",
                "properties": {"secret": {"type": "string"}},
                "required": ["secret"],
            },
        ),
    )

    class Provider:
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            raise AssertionError("streaming path expected")

        async def chat_stream(
            self,
            messages,
            tools=None,
            response_format=None,
        ):
            del messages, tools, response_format
            self.calls += 1
            if self.calls == 1:
                yield LLMChunk(
                    tool_calls=[
                        ToolCall(
                            id="guarded-1",
                            name="guarded",
                            arguments={"secret": "sk-sensitive-value"},
                        ),
                    ],
                    finish_reason="tool_calls",
                )
            else:
                yield LLMChunk(delta_content="done", finish_reason="stop")

    agent = Agent(
        Provider(),
        Executor(auto_approve=True),
        system_prompt="events",
        tool_registry=runtime_registry,
    )
    events = [event async for event in agent.run_events("guarded")]
    requested = [
        event
        for event in events
        if event.type == AgentEventType.PERMISSION_REQUESTED
    ]
    resolved = [
        event
        for event in events
        if event.type == AgentEventType.PERMISSION_RESOLVED
    ]
    assert len(requested) == 1
    assert len(resolved) == 1
    assert requested[0].payload["tool"] == "guarded"
    assert "sk-sensitive-value" not in str(requested[0].payload)
    assert resolved[0].payload["allowed"] is True


@pytest.mark.asyncio
async def test_run_events_emits_sandbox_status(monkeypatch):
    from dashscope_agenticx import sandbox

    runtime_registry = ToolRegistry()

    async def run_command(command: str):
        return command

    runtime_registry.register(
        ToolDefinition(
            name="run_command",
            description="shell",
            permission=PermissionLevel.AUTO,
            func=run_command,
            parameters={
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        ),
    )
    monkeypatch.setattr(sandbox, "is_enabled", lambda: True)
    monkeypatch.setattr(sandbox, "detect_backend", lambda: sandbox.BWRAP)

    class Provider:
        def __init__(self):
            self.calls = 0

        async def chat(self, *args, **kwargs):
            raise AssertionError("streaming path expected")

        async def chat_stream(
            self,
            messages,
            tools=None,
            response_format=None,
        ):
            del messages, tools, response_format
            self.calls += 1
            if self.calls == 1:
                yield LLMChunk(
                    tool_calls=[
                        ToolCall(
                            id="shell-1",
                            name="run_command",
                            arguments={"command": "echo ok"},
                        ),
                    ],
                    finish_reason="tool_calls",
                )
            else:
                yield LLMChunk(delta_content="done", finish_reason="stop")

    agent = Agent(
        Provider(),
        Executor(auto_approve=True),
        system_prompt="events",
        tool_registry=runtime_registry,
    )
    events = [event async for event in agent.run_events("shell")]
    statuses = [
        event
        for event in events
        if event.type == AgentEventType.SANDBOX_STATUS
    ]
    assert len(statuses) == 1
    assert statuses[0].payload == {
        "enabled": True,
        "available": True,
        "active": True,
        "backend": "bwrap",
    }


@pytest.mark.asyncio
async def test_run_events_emits_artifact_before_turn_finished(
    tmp_path,
    monkeypatch,
):
    from dashscope_agenticx import deliverable

    artifact = tmp_path / "report.txt"
    artifact.write_text("result", encoding="utf-8")
    monkeypatch.setattr(
        deliverable,
        "collect_deliverables",
        lambda messages: [artifact],
    )
    agent = Agent(
        _TextProvider(),
        Executor(auto_approve=True),
        system_prompt="events",
    )
    events = [event async for event in agent.run_events("report")]
    types = [event.type for event in events]
    artifact_event = next(
        event
        for event in events
        if event.type == AgentEventType.ARTIFACT_CREATED
    )
    assert artifact_event.payload == {
        "path": str(artifact.resolve()),
        "name": "report.txt",
        "size": 6,
    }
    assert types.index(AgentEventType.ARTIFACT_CREATED) < types.index(
        AgentEventType.TURN_FINISHED,
    )


@pytest.mark.asyncio
async def test_run_events_emits_error_before_reraising():
    class Provider:
        async def chat(self, *args, **kwargs):
            raise AssertionError("streaming path expected")

        async def chat_stream(
            self,
            messages,
            tools=None,
            response_format=None,
        ):
            del messages, tools, response_format
            raise ValueError("provider exploded")
            yield  # pragma: no cover

    agent = Agent(
        Provider(),
        Executor(auto_approve=True),
        system_prompt="events",
    )
    events = []
    with pytest.raises(ValueError, match="provider exploded"):
        async for event in agent.run_events("fail"):
            events.append(event)

    error = [event for event in events if event.type == AgentEventType.ERROR]
    assert len(error) == 1
    assert error[0].payload["error_type"] == "ValueError"
    assert "provider exploded" in error[0].payload["message"]
