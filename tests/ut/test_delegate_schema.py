# -*- coding: utf-8 -*-
"""M14: delegate/subagent_invoke must reach the provider tool schema.

Before the fix, ToolRegistry.to_schema_list() only exposed a hardcoded
allowlist (core filesystem/shell tools, a few session tools, mcp_*), so
capability tools registered under local.delegate / local.subagent were
never sent to the model — sub-agents could never auto-start.

These tests build the agent the way the boot path (cli/repl.py) does —
set_parent_agent + set_config + register_platform_tools — and inspect the
schema handed to a fake provider, then drive a full delegate tool_call
end-to-end with a fake child provider.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

import dashscope_agenticx.agents.delegate as delegate_module
import dashscope_agenticx.agents.subagent as subagent_module
from dashscope_agenticx.agent import Agent
from dashscope_agenticx.config import Config
from dashscope_agenticx.executor import Executor
from dashscope_agenticx.memory.manager import MemoryManager
from dashscope_agenticx.providers.base import LLMChunk, LLMResponse, ToolCall
from dashscope_agenticx.tools.platform import (
    register_platform_tools,
    unregister_capability_tools,
)
from dashscope_agenticx.tools.registry import (
    ToolDefinition,
    ToolRegistry,
    registry,
)

_DELEGATE_FAMILY = {
    "delegate",
    "delegate_parallel",
    "subagent_invoke",
    "review_code",
    "write_tests",
    "generate_docs",
}


@pytest.fixture(autouse=True)
def clean_registry_and_parents():
    """Isolate the global registry + delegate/subagent parent refs.

    Boot-style registration mutates the module-level registry; snapshot it
    and start each test without any delegate-family tools so results do not
    depend on test ordering.
    """
    snapshot = dict(registry._tools)
    for name in _DELEGATE_FAMILY:
        registry.unregister(name)
    delegate_module.set_parent_agent(None)
    delegate_module.set_config(None)
    delegate_module._delegate_semaphores.clear()
    subagent_module.set_parent_agent(None)
    subagent_module.set_config(None)
    yield
    registry._tools.clear()
    registry._tools.update(snapshot)
    delegate_module.set_parent_agent(None)
    delegate_module.set_config(None)
    delegate_module._delegate_semaphores.clear()
    subagent_module.set_parent_agent(None)
    subagent_module.set_config(None)


class _CaptureProvider:
    """Fake provider: chat() answers with plain content, capturing the tool
    schema of every request; chat_stream() streams one canned chunk (used by
    child agents spawned via delegate/subagent_invoke)."""

    def __init__(self, child_answer: str = "子代理结论"):
        self.tools_seen: list[list[dict]] = []
        self.chat_calls = 0
        self._child_answer = child_answer

    async def chat(self, messages, tools=None, response_format=None):
        self.chat_calls += 1
        self.tools_seen.append(list(tools or []))
        return LLMResponse(content="完成", tool_calls=[])

    async def chat_stream(self, messages, tools=None, response_format=None):
        self.tools_seen.append(list(tools or []))
        yield LLMChunk(delta_content=self._child_answer)


class _DelegatingProvider(_CaptureProvider):
    """Stream 1 emits one canned tool_call, stream 2 answers the spawned
    child (children share the parent provider), later streams answer the
    main agent plainly."""

    def __init__(self, tool_call: ToolCall, child_answer: str = "子代理结论"):
        super().__init__(child_answer=child_answer)
        self._tool_call = tool_call
        self._stream_calls = 0

    async def chat_stream(self, messages, tools=None, response_format=None):
        self.tools_seen.append(list(tools or []))
        self._stream_calls += 1
        if self._stream_calls == 1:
            yield LLMChunk(tool_calls=[self._tool_call])
        elif self._stream_calls == 2:
            yield LLMChunk(delta_content=self._child_answer)
        else:
            yield LLMChunk(delta_content="汇总完成")


def _make_agent(provider, tmp_path, **kwargs) -> Agent:
    kwargs.setdefault("memory_manager", MemoryManager(tmp_path / "mm"))
    # Explicit system prompt: never touch workspace/global system-prompt.md.
    kwargs.setdefault("system_prompt", "test prompt")
    return Agent(
        provider=provider,
        executor=Executor(auto_approve=True),
        session_path=tmp_path / "session.json",
        **kwargs,
    )


def _boot_register(config: Config, agent: Agent) -> None:
    """Mirror the cli/repl.py boot sequence for sub-agent capabilities."""
    subagent_module.set_parent_agent(agent)
    subagent_module.set_config(config)
    delegate_module.set_parent_agent(agent)
    delegate_module.set_config(config)
    register_platform_tools(config, connect_mcp_fn=None)


async def _drain(agent: Agent, text: str = "查看这 2 个文件") -> None:
    async for _ in agent.run_stream(text):
        pass


# ── schema wiring ──────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_schema_includes_delegate_tools_after_boot_registration(
    tmp_path,
):
    config = Config(enabled_capabilities=["local.delegate", "local.subagent"])
    provider = _CaptureProvider()
    agent = _make_agent(provider, tmp_path)
    _boot_register(config, agent)

    await _drain(agent)

    assert provider.tools_seen, "provider was never called"
    names = {t["name"] for t in provider.tools_seen[0]}
    assert {"delegate", "delegate_parallel", "subagent_invoke"} <= names


@pytest.mark.asyncio
async def test_schema_excludes_delegate_tools_when_capability_disabled(
    tmp_path,
):
    config = Config(enabled_capabilities=[])
    provider = _CaptureProvider()
    agent = _make_agent(provider, tmp_path)
    _boot_register(config, agent)

    await _drain(agent)

    assert provider.tools_seen, "provider was never called"
    names = {t["name"] for t in provider.tools_seen[0]}
    assert not ({"delegate", "delegate_parallel", "subagent_invoke"} & names)
    # Nothing was registered in the first place (registration is the gate).
    assert registry.get("delegate") is None
    assert registry.get("subagent_invoke") is None


@pytest.mark.asyncio
async def test_schema_drops_delegate_tools_after_mid_session_disable(tmp_path):
    """/capability disable unregisters the tools; the next turn's schema must
    no longer offer them (while still-enabled capabilities remain visible)."""
    config = Config(enabled_capabilities=["local.delegate", "local.subagent"])
    provider = _CaptureProvider()
    agent = _make_agent(provider, tmp_path)
    _boot_register(config, agent)

    removed = unregister_capability_tools("local.delegate")
    assert removed >= 2  # delegate + delegate_parallel

    await _drain(agent)

    names = {t["name"] for t in provider.tools_seen[0]}
    assert "delegate" not in names
    assert "delegate_parallel" not in names
    assert "subagent_invoke" in names  # local.subagent still enabled


def test_to_schema_list_includes_registered_capability_agent_tools():
    """Registry-level: delegate-family tools appear once registered."""
    reg = ToolRegistry()
    reg.register(
        ToolDefinition(name="delegate", description="d", parameters={}),
    )
    reg.register(
        ToolDefinition(name="subagent_invoke", description="s", parameters={}),
    )
    names = {s["name"] for s in reg.to_schema_list()}
    assert {"delegate", "subagent_invoke"} <= names


def test_child_agent_schema_hides_delegate_and_subagent_tools(tmp_path):
    """Child agents (allow_delegate=False) must not see any nesting-capable
    tool — no delegate, no delegate_parallel, no subagent_invoke."""
    child = _make_agent(_CaptureProvider(), tmp_path, allow_delegate=False)
    schema = [
        {"name": "read_file"},
        {"name": "delegate"},
        {"name": "delegate_parallel"},
        {"name": "subagent_invoke"},
    ]
    names = {t["name"] for t in child._filter_tools_schema(schema)}
    assert names == {"read_file"}


# ── end-to-end execution ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delegate_tool_call_executes_end_to_end(tmp_path):
    """Model calls delegate → child agent runs on a fake provider → the JSON
    result is appended as the tool response."""
    config = Config(enabled_capabilities=["local.delegate", "local.subagent"])
    provider = _DelegatingProvider(
        ToolCall(
            id="call-delegate-1",
            name="delegate",
            arguments={"task": "查看文件A并总结"},
        ),
        child_answer="文件A的结论",
    )
    agent = _make_agent(provider, tmp_path)
    _boot_register(config, agent)

    await _drain(agent)

    # The delegate schema was actually offered to the model.
    names = {t["name"] for t in provider.tools_seen[0]}
    assert "delegate" in names

    tool_msgs = [
        m
        for m in agent.messages
        if m.get("role") == "tool" and m.get("name") == "delegate"
    ]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call-delegate-1"
    parsed = json.loads(tool_msgs[0]["content"])
    assert parsed["status"] == "completed"
    assert "文件A的结论" in parsed["result"]

    # Turn finished: final assistant reply recorded after the tool response.
    assert agent.messages[-1] == {"role": "assistant", "content": "汇总完成"}


@pytest.mark.asyncio
async def test_subagent_invoke_tool_call_executes_end_to_end(tmp_path):
    config = Config(enabled_capabilities=["local.delegate", "local.subagent"])
    provider = _DelegatingProvider(
        ToolCall(
            id="call-subagent-1",
            name="subagent_invoke",
            arguments={"prompt": "审查 src/auth.py"},
        ),
        child_answer="审查结论：无严重问题",
    )
    agent = _make_agent(provider, tmp_path)
    _boot_register(config, agent)

    await _drain(agent)

    tool_msgs = [
        m
        for m in agent.messages
        if m.get("role") == "tool" and m.get("name") == "subagent_invoke"
    ]
    assert len(tool_msgs) == 1
    assert tool_msgs[0]["tool_call_id"] == "call-subagent-1"
    assert "审查结论：无严重问题" in tool_msgs[0]["content"]


# ── A6: the child's model is the parent's, not the caller's ────────────


async def _one_chunk(prompt):
    """Stand-in for a child Agent's run_stream: one chunk, then done."""
    yield "done"


def test_delegate_schema_no_longer_advertises_a_model_knob(tmp_path):
    """A subagent shares the parent's provider, whose model is fixed when
    the provider is constructed. Offering `model` advertised a knob that
    could only rename the child without changing what served it."""
    config = Config(enabled_capabilities=["local.delegate"])
    _boot_register(config, _make_agent(_CaptureProvider(), tmp_path))

    delegate = registry.get("delegate")
    assert "model" not in delegate.parameters["properties"]
    assert "model" not in delegate.description
    assert "model" not in registry.get("delegate_parallel").description


@pytest.mark.asyncio
async def test_a_retired_model_argument_neither_raises_nor_renames(
    tmp_path,
):
    """The kwarg stays in the signature so a model that already learned to
    send it gets no TypeError — but model_name must stay the parent's, since
    it picks vision/audio normalization and the truncation window."""
    config = Config(enabled_capabilities=["local.delegate"])
    parent = _make_agent(_CaptureProvider(), tmp_path, model_name="qwen-max")
    _boot_register(config, parent)

    with patch("dashscope_agenticx.agent.Agent") as MockAgent:
        MockAgent.return_value.run_stream = _one_chunk
        result = json.loads(
            await delegate_module._delegate("t", model="qwen-vl-max"),
        )

    assert result["status"] == "completed"
    assert MockAgent.call_args.kwargs["model_name"] == "qwen-max"


@pytest.mark.asyncio
async def test_parallel_task_dicts_cannot_rename_the_child_either(tmp_path):
    config = Config(enabled_capabilities=["local.delegate"])
    parent = _make_agent(_CaptureProvider(), tmp_path, model_name="qwen-max")
    _boot_register(config, parent)

    with patch("dashscope_agenticx.agent.Agent") as MockAgent:
        MockAgent.return_value.run_stream = _one_chunk
        await delegate_module._delegate_parallel(
            [{"task": "t", "model": "qwen-vl-max"}],
        )

    assert MockAgent.call_args.kwargs["model_name"] == "qwen-max"
