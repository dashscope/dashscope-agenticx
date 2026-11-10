# -*- coding: utf-8 -*-
"""Tests for agents/subagent.py — specialist invocation with mocked _subagent_invoke."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

import dashscope_agenticx.agents.subagent as subagent_module
from dashscope_agenticx.executor import Executor
from dashscope_agenticx.tools.registry import ToolRegistry
from dashscope_agenticx.agents.subagent import (
    _specialist_invoke,
    _subagent_invoke,
    set_config,
    set_parent_agent,
)

# ── _subagent_invoke with no parent ───────────────────────────────────────


@pytest.mark.asyncio
async def test_subagent_invoke_no_parent_returns_error():
    set_parent_agent(None)
    result = await _subagent_invoke("do something")
    assert "Error" in result
    assert "parent" in result or "subagent" in result


# ── _specialist_invoke (mocked) ───────────────────────────────────────────


@pytest.mark.asyncio
async def test_specialist_invoke_unknown_type():
    result = await _specialist_invoke("unknown_type", "some task")
    assert "Error" in result
    assert "unknown" in result


@pytest.mark.asyncio
async def test_specialist_invoke_code_reviewer():
    with patch(
        "dashscope_agenticx.agents.subagent._subagent_invoke",
        new=AsyncMock(return_value="代码审查结果"),
    ):
        result = await _specialist_invoke("code_reviewer", "review auth.py")
    assert result == "代码审查结果"


@pytest.mark.asyncio
async def test_specialist_invoke_test_writer():
    with patch(
        "dashscope_agenticx.agents.subagent._subagent_invoke",
        new=AsyncMock(return_value="测试代码"),
    ):
        result = await _specialist_invoke(
            "test_writer",
            "write tests for utils.py",
        )
    assert result == "测试代码"


@pytest.mark.asyncio
async def test_specialist_invoke_doc_generator():
    with patch(
        "dashscope_agenticx.agents.subagent._subagent_invoke",
        new=AsyncMock(return_value="文档内容"),
    ):
        result = await _specialist_invoke("doc_generator", "document api.py")
    assert result == "文档内容"


# ── set_parent_agent / set_config ─────────────────────────────────────────


def test_set_parent_agent():
    class FakeAgent:
        provider = None
        executor = None

    set_parent_agent(FakeAgent())
    assert subagent_module._parent_agent is not None
    set_parent_agent(None)  # cleanup


def test_set_config():
    class FakeConfig:
        subagents = {}

    set_config(FakeConfig())
    assert subagent_module._config is not None
    set_config(None)  # cleanup


# ── _subagent_invoke with fake parent (smoke test) ────────────────────────


@pytest.mark.asyncio
async def test_subagent_invoke_with_fake_parent_calls_run_stream():
    """Verify _subagent_invoke drains run_stream when a parent is set."""

    async def fake_stream(prompt):
        yield "result text"

    class FakeProvider:
        pass

    class FakeExecutor(Executor):
        pass

    class FakeAgent:
        provider = FakeProvider()
        executor = FakeExecutor()
        tool_registry = ToolRegistry()
        user_name = "test"
        provider_name = "test"
        model_name = "test"
        messages = []
        disabled_caps_provider = None
        directives_provider = None
        memory_manager = None

        def run_stream(self, prompt):
            return fake_stream(prompt)

    set_parent_agent(FakeAgent())

    with patch("dashscope_agenticx.agent.Agent") as MockAgent:
        mock_instance = MockAgent.return_value

        async def mock_run_stream(prompt):
            yield "mocked response"

        mock_instance.run_stream = mock_run_stream
        mock_instance.messages = []

        result = await _subagent_invoke("do task")

    set_parent_agent(None)
    assert "mocked response" in result


# ── isolation: system prompt composition + derived memory manager ────────


def _fake_parent(memory_manager=None, system_prompt="BASE PROMPT"):
    class FakeProvider:
        pass

    class FakeExecutor(Executor):
        pass

    class FakeAgent:
        provider = FakeProvider()
        executor = FakeExecutor()
        tool_registry = ToolRegistry()
        user_name = "test"
        provider_name = "test"
        model_name = "test"
        disabled_caps_provider = None
        directives_provider = None
        hook_bus = None

    agent = FakeAgent()
    agent.memory_manager = memory_manager
    agent.system_prompt = system_prompt
    return agent


@pytest.mark.asyncio
async def test_subagent_composes_single_system_prompt():
    """A specialization must be folded into ONE ctor system_prompt argument
    (parent base + specialization), not appended as a second system message."""
    set_parent_agent(_fake_parent())
    try:
        with patch("dashscope_agenticx.agent.Agent") as MockAgent:
            mock_instance = MockAgent.return_value

            async def mock_run_stream(prompt):
                yield "done"

            mock_instance.run_stream = mock_run_stream

            await _subagent_invoke("task", system_prompt="SPECIALIZATION")

        kwargs = MockAgent.call_args.kwargs
        assert kwargs["system_prompt"] == "BASE PROMPT\n\nSPECIALIZATION"
    finally:
        set_parent_agent(None)


@pytest.mark.asyncio
async def test_subagent_without_specialization_passes_none_prompt():
    """No specialization → system_prompt=None so the child ctor resolves the
    default prompt itself (still exactly one system message)."""
    set_parent_agent(_fake_parent())
    try:
        with patch("dashscope_agenticx.agent.Agent") as MockAgent:
            mock_instance = MockAgent.return_value

            async def mock_run_stream(prompt):
                yield "done"

            mock_instance.run_stream = mock_run_stream

            await _subagent_invoke("task")

        assert MockAgent.call_args.kwargs["system_prompt"] is None
    finally:
        set_parent_agent(None)


@pytest.mark.asyncio
async def test_subagent_gets_derived_memory_manager(tmp_path):
    """The child must receive an isolated session tier, not the parent's
    memory manager (child failures would move parent reflection counters)."""
    from dashscope_agenticx.memory.manager import MemoryManager

    parent_mm = MemoryManager(workspace_dir=tmp_path)
    set_parent_agent(_fake_parent(memory_manager=parent_mm))
    try:
        with patch("dashscope_agenticx.agent.Agent") as MockAgent:
            mock_instance = MockAgent.return_value

            async def mock_run_stream(prompt):
                yield "done"

            mock_instance.run_stream = mock_run_stream

            await _subagent_invoke("task")

        child_mm = MockAgent.call_args.kwargs["memory_manager"]
        assert child_mm is not parent_mm
        assert child_mm.session is not parent_mm.session
        assert child_mm.persistent is parent_mm.persistent
    finally:
        set_parent_agent(None)
