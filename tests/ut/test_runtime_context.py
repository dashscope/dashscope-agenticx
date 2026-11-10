# -*- coding: utf-8 -*-
"""Runtime-local registry isolation regressions for the 1.0 migration."""
from __future__ import annotations

import asyncio

import pytest

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.executor import Executor
from dashscope_agenticx.providers.base import LLMChunk
from dashscope_agenticx.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    bind_tool_registry,
    registry,
)


@pytest.mark.asyncio
@pytest.mark.parametrize("stage", ["initialize", "list_tools", "list_prompts"])
async def test_mcp_discovery_failure_closes_unregistered_client(
    monkeypatch,
    stage,
):
    from unittest.mock import AsyncMock

    from dashscope_agenticx.cli import mcp
    from dashscope_agenticx.config import Config

    client = AsyncMock()
    client.initialize.return_value = True
    client.list_tools.return_value = [{"name": "read", "inputSchema": {}}]
    getattr(client, stage).side_effect = ValueError("discovery failed")
    monkeypatch.setattr(mcp, "MCPClient", lambda **kwargs: client)
    clients = {}
    local_registry = ToolRegistry()
    with mcp.bind_mcp_clients(clients), bind_tool_registry(local_registry):
        with pytest.raises(ValueError, match="discovery failed"):
            await mcp._connect_mcp("broken", Config())
    client.close.assert_awaited_once()
    assert not clients
    assert not local_registry.get("mcp_broken_read")


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["repl", "oneshot"])
async def test_cli_early_failure_closes_runtime(monkeypatch, mode):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from dashscope_agenticx.cli import repl, runners
    from dashscope_agenticx.config import Config

    runtime = SimpleNamespace(activate=nullcontext, aclose=AsyncMock())
    monkeypatch.setattr(
        "dashscope_agenticx.runtime.RuntimeBuilder",
        lambda *args, **kwargs: SimpleNamespace(build=lambda: runtime),
    )
    helper = "_run_session" if mode == "repl" else "_run_oneshot_session"
    module = repl if mode == "repl" else runners
    monkeypatch.setattr(
        module,
        helper,
        AsyncMock(side_effect=ValueError("early failure")),
    )
    with pytest.raises(ValueError, match="early failure"):
        if mode == "repl":
            await repl._run_loop(Config())
        else:
            config = Config()
            config.api_key = "test"
            await runners._run_oneshot(config, "hello")
    runtime.aclose.assert_awaited_once()


@pytest.mark.asyncio
@pytest.mark.parametrize("api", ["run_stream", "run_events"])
async def test_sdk_early_stream_close_unwinds_agent_and_runtime(
    monkeypatch,
    api,
):
    from unittest.mock import AsyncMock

    from dashscope_agenticx import sdk
    from dashscope_agenticx.config import Config
    from dashscope_agenticx.runtime import Runtime, RuntimeContext

    class Provider:
        aclose = AsyncMock()

        async def chat(self, *args, **kwargs):
            from dashscope_agenticx.providers.base import LLMResponse

            return LLMResponse(content="summary")

        async def chat_stream(self, *args, **kwargs):
            yield LLMChunk(delta_content="hello", finish_reason="stop")

    provider = Provider()
    runtime = Runtime(
        Config(),
        provider,
        Executor(),
        RuntimeContext(tool_registry=ToolRegistry()),
    )
    agent = Agent(provider, runtime.executor, system_prompt="test")
    agent.runtime = runtime
    monkeypatch.setattr(sdk, "create_agent", lambda **kwargs: agent)
    close = AsyncMock(wraps=runtime.aclose)
    monkeypatch.setattr(runtime, "aclose", close)
    stream = getattr(sdk, api)("hello")
    if api == "run_events":
        await anext(stream)
    await anext(stream)
    await stream.aclose()
    assert agent._event_sink is None
    assert agent._closed
    close.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_closes_providers_replaced_during_configuration():
    from unittest.mock import AsyncMock

    from dashscope_agenticx.config import Config
    from dashscope_agenticx.runtime import Runtime, RuntimeContext

    first, second, third = AsyncMock(), AsyncMock(), AsyncMock()
    runtime = Runtime(
        Config(),
        first,
        Executor(),
        RuntimeContext(tool_registry=ToolRegistry()),
    )
    agent = Agent(first, runtime.executor, system_prompt="test")
    agent.runtime = runtime
    agent.provider = second
    agent.provider = third
    runtime.provider = third
    await runtime.aclose()
    for provider in (first, second, third):
        provider.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_mcp_connections_are_isolated_and_close_is_idempotent():
    from unittest.mock import AsyncMock

    from dashscope_agenticx.cli.mcp import _mcp_clients
    from dashscope_agenticx.config import Config
    from dashscope_agenticx.runtime import Runtime, RuntimeContext

    first_client = AsyncMock()
    second_client = AsyncMock()
    first_provider = AsyncMock()
    first = Runtime(
        Config(),
        first_provider,
        object(),
        RuntimeContext(tool_registry=ToolRegistry()),
    )
    second = Runtime(
        Config(),
        AsyncMock(),
        object(),
        RuntimeContext(tool_registry=ToolRegistry()),
    )

    async def register(runtime, client):
        with runtime.activate():
            _mcp_clients["shared-service"] = client
            await asyncio.sleep(0)
            assert _mcp_clients["shared-service"] is client
        assert "shared-service" not in _mcp_clients

    await asyncio.gather(
        register(first, first_client),
        register(second, second_client),
    )
    await first.aclose()
    await first.aclose()
    first_client.aclose.assert_awaited_once()
    first_provider.aclose.assert_awaited_once()
    second_client.aclose.assert_not_awaited()
    assert second.context.mcp_clients["shared-service"] is second_client
    with pytest.raises(RuntimeError, match="closed"):
        with first.activate():
            pass
    await second.aclose()


@pytest.mark.asyncio
async def test_runtime_context_manager_closes_all_resources_on_error():
    from unittest.mock import AsyncMock

    from dashscope_agenticx.config import Config
    from dashscope_agenticx.runtime import Runtime, RuntimeContext

    provider = AsyncMock()
    client = AsyncMock()
    runtime = Runtime(
        Config(),
        provider,
        object(),
        RuntimeContext(mcp_clients={"service": client}),
    )
    with pytest.raises(ValueError, match="run failed"):
        async with runtime:
            raise ValueError("run failed")
    provider.aclose.assert_awaited_once()
    client.aclose.assert_awaited_once()


@pytest.mark.asyncio
async def test_runtime_close_continues_after_a_client_error():
    from unittest.mock import AsyncMock

    from dashscope_agenticx.config import Config
    from dashscope_agenticx.runtime import Runtime, RuntimeContext

    failing = AsyncMock()
    failing.aclose.side_effect = RuntimeError("close failed")
    healthy = AsyncMock()
    runtime = Runtime(
        Config(),
        AsyncMock(),
        object(),
        RuntimeContext(mcp_clients={"first": failing, "second": healthy}),
    )
    with pytest.raises(RuntimeError, match="close failed"):
        await runtime.aclose()
    healthy.aclose.assert_awaited_once()
    assert not runtime.context.mcp_clients


@pytest.mark.asyncio
async def test_runtime_binds_sessions_permissions_and_extensions_per_task(
    tmp_path,
):
    from dashscope_agenticx.config import Config
    from dashscope_agenticx.extensions import (
        CustomExtensions,
        CustomProvider,
        current,
    )
    from dashscope_agenticx.permission import (
        PermissionPolicy,
        get_permission_policy,
    )
    from dashscope_agenticx.runtime import Runtime, RuntimeContext
    from dashscope_agenticx.session import SessionManager, get_session_manager

    async def observe(label):
        extensions = CustomExtensions(
            providers=[
                CustomProvider(name=label, base_url="https://example.com/v1"),
            ],
        )
        manager = SessionManager(tmp_path / label)
        policy = PermissionPolicy(tool_rules={"write_file": label})
        context = RuntimeContext(
            tool_registry=ToolRegistry(),
            session_manager=manager,
            permission_policy=policy,
            extensions=extensions,
        )
        runtime = Runtime(Config(), object(), object(), context)
        with runtime.activate():
            await asyncio.sleep(0)
            assert get_session_manager() is manager
            assert get_permission_policy() is policy
            assert current().providers[0].name == label
        await runtime.aclose()

    await asyncio.gather(observe("first"), observe("second"))


def test_agents_in_one_runtime_have_independent_permission_caches(monkeypatch):
    from dashscope_agenticx.config import Config
    from dashscope_agenticx.runtime import Runtime, RuntimeContext

    monkeypatch.setattr(
        "dashscope_agenticx.runtime.wire_agent_runtime",
        lambda *args, **kwargs: None,
    )
    runtime = Runtime(
        Config(memory_enabled=False),
        object(),
        Executor(),
        RuntimeContext(tool_registry=ToolRegistry()),
    )
    first = runtime.create_agent("first")
    first.executor._always_allow.add("write_file")
    second = runtime.create_agent("second")
    assert first.executor is not second.executor
    assert not second.executor._always_allow
    assert first.executor._permission_observer.__self__ is first
    assert second.executor._permission_observer.__self__ is second


@pytest.mark.asyncio
async def test_provider_chain_releases_every_instantiated_sdk_client():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock

    from dashscope_agenticx.providers.hardening import HardenedProvider
    from dashscope_agenticx.providers.profile import ProviderChain

    first = AsyncMock()
    second = AsyncMock()
    chain = ProviderChain([])
    chain._instances = {
        0: HardenedProvider(SimpleNamespace(client=first)),
        1: HardenedProvider(SimpleNamespace(client=second)),
    }
    await chain.aclose()
    await chain.aclose()
    first.aclose.assert_awaited_once()
    second.aclose.assert_awaited_once()
    assert not chain._instances


def test_registry_clone_does_not_mutate_source():
    source = ToolRegistry()
    source.register(ToolDefinition(name="mcp_source", description="source"))
    cloned = source.clone()

    cloned.unregister("mcp_source")
    cloned.register(ToolDefinition(name="mcp_clone", description="clone"))

    assert source.get("mcp_source") is not None
    assert source.get("mcp_clone") is None
    assert cloned.get("mcp_source") is None
    assert cloned.get("mcp_clone") is not None


@pytest.mark.asyncio
async def test_child_plan_tools_never_update_parent_tracker():
    from unittest.mock import AsyncMock

    from dashscope_agenticx.providers.base import ToolCall
    from dashscope_agenticx.runtime import wire_child_tools

    parent = Agent(
        AsyncMock(),
        Executor(auto_approve=True),
        system_prompt="parent",
        tool_registry=ToolRegistry(),
    )
    parent.memory_manager.session.plan.create_plan("parent", ["keep"])
    child = Agent(
        parent.provider,
        parent.executor.fork_for_child(),
        system_prompt="child",
        tool_registry=parent.tool_registry,
    )
    wire_child_tools(parent, child)
    await child._execute_tool(
        ToolCall(
            id="child-plan",
            name="create_plan",
            arguments={"goal": "child", "steps": ["isolated"]},
        ),
    )
    assert parent.memory_manager.session.plan.snapshot()["goal"] == "parent"
    assert child.memory_manager.session.plan.snapshot()["goal"] == "child"
    assert child.tool_registry is not parent.tool_registry
    assert parent.tool_registry.get("create_plan") is None


def test_runtime_loads_admin_rules_without_mutating_global_policy(
    monkeypatch,
    tmp_path,
):
    from dashscope_agenticx.config import Config
    from dashscope_agenticx.permission import get_permission_policy
    from dashscope_agenticx.runtime import RuntimeContext

    directory = tmp_path / "workspace"
    directory.mkdir()
    (directory / "permissions.toml").write_text(
        '[tools]\nwrite_file = "deny"\n',
    )
    monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", directory)
    monkeypatch.setattr(
        "dashscope_agenticx.config.CONFIG_DIR",
        tmp_path / "global",
    )
    previous = dict(get_permission_policy().tool_rules)
    context = RuntimeContext.create(Config())
    assert context.permission_policy.check_tool("write_file") == "deny"
    assert get_permission_policy().tool_rules == previous


@pytest.mark.asyncio
async def test_bound_registry_is_context_local_across_tasks():
    first = ToolRegistry()
    second = ToolRegistry()

    async def register_into(target, name):
        with bind_tool_registry(target):
            await asyncio.sleep(0)
            registry.register(
                ToolDefinition(name=name, description=name),
            )
            await asyncio.sleep(0)
            assert registry.get(name) is not None

    await asyncio.gather(
        register_into(first, "mcp_first"),
        register_into(second, "mcp_second"),
    )

    assert first.get("mcp_first") is not None
    assert first.get("mcp_second") is None
    assert second.get("mcp_second") is not None
    assert second.get("mcp_first") is None
    assert registry.get("mcp_first") is None
    assert registry.get("mcp_second") is None


@pytest.mark.asyncio
async def test_agent_exposes_only_its_injected_registry():
    runtime_registry = ToolRegistry()
    runtime_registry.register(
        ToolDefinition(
            name="mcp_runtime_only",
            description="runtime-local tool",
            parameters={"type": "object", "properties": {}},
        ),
    )

    class Provider:
        def __init__(self):
            self.tool_names = set()

        async def chat(self, *args, **kwargs):
            raise AssertionError("streaming path expected")

        async def chat_stream(
            self,
            messages,
            tools=None,
            response_format=None,
        ):
            del messages, response_format
            self.tool_names = {tool["name"] for tool in (tools or [])}
            yield LLMChunk(delta_content="ok", finish_reason="stop")

    provider = Provider()
    agent = Agent(
        provider,
        Executor(auto_approve=True),
        system_prompt="runtime isolation",
        tool_registry=runtime_registry,
    )

    async for _ in agent.run_stream("use the runtime tool"):
        pass

    assert "mcp_runtime_only" in provider.tool_names
    assert registry.get("mcp_runtime_only") is None


@pytest.mark.asyncio
async def test_subagent_and_delegate_parent_bindings_are_task_local():
    from dashscope_agenticx.agents import delegate as delegate_module
    from dashscope_agenticx.agents import subagent as subagent_module

    first_parent = object()
    second_parent = object()
    first_config = object()
    second_config = object()

    async def observe(parent, config):
        with (
            subagent_module.bind_subagent_context(parent, config),
            delegate_module.bind_delegate_context(parent, config),
        ):
            await asyncio.sleep(0)
            assert subagent_module._current_parent() is parent
            assert subagent_module._current_config() is config
            assert delegate_module._current_parent() is parent
            assert delegate_module._current_config() is config
            await asyncio.sleep(0)
            return (
                subagent_module._current_parent(),
                delegate_module._current_parent(),
            )

    first, second = await asyncio.gather(
        observe(first_parent, first_config),
        observe(second_parent, second_config),
    )

    assert first == (first_parent, first_parent)
    assert second == (second_parent, second_parent)


@pytest.mark.asyncio
async def test_capability_tool_tracking_is_task_local():
    from dashscope_agenticx.tools.platform import (
        bind_capability_tools,
        current_capability_tools,
    )

    first: dict[str, set[str]] = {}
    second: dict[str, set[str]] = {}

    async def record(target, capability, tool_name):
        with bind_capability_tools(target):
            await asyncio.sleep(0)
            current_capability_tools().setdefault(capability, set()).add(
                tool_name,
            )
            await asyncio.sleep(0)
            return current_capability_tools()

    first_seen, second_seen = await asyncio.gather(
        record(first, "first.cap", "first_tool"),
        record(second, "second.cap", "second_tool"),
    )

    assert first_seen == {"first.cap": {"first_tool"}}
    assert second_seen == {"second.cap": {"second_tool"}}
    assert first == {"first.cap": {"first_tool"}}
    assert second == {"second.cap": {"second_tool"}}
