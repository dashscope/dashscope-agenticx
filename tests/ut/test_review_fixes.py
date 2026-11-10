# -*- coding: utf-8 -*-
"""Regression tests for the 2026-10-27 design/code review fixes."""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.config import Config
from dashscope_agenticx.executor import Executor
from dashscope_agenticx.providers.base import LLMChunk, ToolCall
from dashscope_agenticx.session import (
    SessionManager,
    get_session_manager,
    set_session_manager,
)
from dashscope_agenticx.tools.registry import (
    PermissionLevel,
    ToolDefinition,
    registry,
)
from tests.e2e.fake_provider import FakeLLMProvider


@pytest.mark.asyncio
async def test_ephemeral_agent_does_not_write_default_session(tmp_path):
    old_manager = get_session_manager()
    manager = SessionManager(tmp_path / ".agenticx")
    set_session_manager(manager)
    try:
        agent = Agent(
            FakeLLMProvider({"hello": "world"}),
            Executor(auto_approve=True),
            system_prompt="test",
            session_path=None,
        )
        async for _ in agent.run_stream("hello"):
            pass
        assert not (manager.session_dir / "default" / "events.jsonl").exists()
    finally:
        set_session_manager(old_manager)


@pytest.mark.asyncio
async def test_registry_mutation_refreshes_schema_mid_turn():
    old_connect = registry.get("mcp_connect")
    old_dynamic = registry.get("mcp_review_hello")

    async def hello():
        return "hello"

    async def connect(service: str):
        del service
        registry.register_mcp_tool(
            "mcp_review_hello",
            "Dynamically connected test tool",
            {"type": "object", "properties": {}},
            hello,
        )
        return "connected"

    registry.register(
        ToolDefinition(
            name="mcp_connect",
            description="Connect test service",
            permission=PermissionLevel.AUTO,
            func=connect,
            parameters={
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        ),
    )

    class Provider:
        def __init__(self):
            self.calls = 0
            self.toolsets: list[set[str]] = []

        async def chat(self, *args, **kwargs):
            raise AssertionError("streaming path expected")

        async def chat_stream(
            self,
            messages,
            tools=None,
            response_format=None,
        ):
            del messages, response_format
            self.calls += 1
            self.toolsets.append({t["name"] for t in (tools or [])})
            if self.calls == 1:
                yield LLMChunk(
                    tool_calls=[
                        ToolCall(
                            id="connect-1",
                            name="mcp_connect",
                            arguments={"service": "review"},
                        ),
                    ],
                )
            else:
                yield LLMChunk(delta_content="done")
            yield LLMChunk(finish_reason="stop")

    try:
        provider = Provider()
        agent = Agent(
            provider,
            Executor(auto_approve=True),
            system_prompt="test",
            max_turns=3,
        )
        async for _ in agent.run_stream("connect review and call hello"):
            pass
        assert "mcp_review_hello" not in provider.toolsets[0]
        assert "mcp_review_hello" in provider.toolsets[1]
    finally:
        registry.unregister("mcp_connect")
        registry.unregister("mcp_review_hello")
        if old_connect is not None:
            registry.register(old_connect)
        if old_dynamic is not None:
            registry.register(old_dynamic)


def test_plan_tools_are_visible_when_registered():
    names = {t["name"] for t in registry.to_schema_list("plan a refactor")}
    # This test may run before CLI runtime registration, so register minimal
    # placeholders to exercise the visibility policy itself.
    added = []
    for name in ("create_plan", "complete_step", "get_plan_status"):
        if registry.get(name) is None:
            registry.register(ToolDefinition(name=name, description=name))
            added.append(name)
    try:
        names = {t["name"] for t in registry.to_schema_list("plan a refactor")}
        assert {"create_plan", "complete_step", "get_plan_status"} <= names
    finally:
        for name in added:
            registry.unregister(name)


def test_sdk_create_agent_uses_runtime_builder(monkeypatch):
    import dashscope_agenticx.runtime as runtime
    import dashscope_agenticx.sdk as sdk

    captured = {}
    expected = SimpleNamespace()

    class FakeRuntime:
        def create_agent(self, **kwargs):
            captured["create"] = kwargs
            return expected

    class FakeBuilder:
        def __init__(self, config):
            captured["config"] = config

        def build(self):
            captured["built"] = True
            return FakeRuntime()

    monkeypatch.setattr(runtime, "RuntimeBuilder", FakeBuilder)
    config = Config(memory_enabled=False, enabled_capabilities=[])
    agent = sdk.create_agent(config=config, system_prompt="sdk", oneshot=True)

    assert agent is expected
    assert captured["config"] is config
    assert captured["built"] is True
    assert captured["create"]["system_prompt"] == "sdk"
    assert captured["create"]["session_id"] is None
    assert captured["create"]["oneshot"] is True


@pytest.mark.asyncio
async def test_sdk_run_once_is_ephemeral_by_default(monkeypatch):
    import dashscope_agenticx.sdk as sdk

    captured = {}

    class FakeAgent:
        async def run_stream(self, prompt):
            captured["prompt"] = prompt
            yield "ok"

    def create_agent(**kwargs):
        captured.update(kwargs)
        return FakeAgent()

    monkeypatch.setattr(sdk, "create_agent", create_agent)
    assert await sdk.run_once("hello") == "ok"
    assert captured["session_id"] is None
    assert captured["oneshot"] is True


def test_sdk_rejects_unsafe_session_id(monkeypatch, tmp_path):
    import dashscope_agenticx.runtime as runtime
    import dashscope_agenticx.sdk as sdk

    manager = SessionManager(tmp_path / ".agenticx")

    class FakeRuntime:
        def create_agent(self, *, session_id=None, **_kwargs):
            manager.get_history_path(session_id)
            return SimpleNamespace()

    class FakeBuilder:
        def __init__(self, _config):
            pass

        def build(self):
            return FakeRuntime()

    monkeypatch.setattr(runtime, "RuntimeBuilder", FakeBuilder)
    config = Config(memory_enabled=False, enabled_capabilities=[])
    with pytest.raises(ValueError, match="unsafe session topic"):
        sdk.create_agent(config=config, session_id="../escape")
