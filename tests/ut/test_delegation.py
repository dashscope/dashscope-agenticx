# -*- coding: utf-8 -*-
"""Tests for the delegate tool and child-agent restrictions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.agents import delegate as delegate_module
from dashscope_agenticx.executor import classify_tool_text


class AsyncIterator:
    """Wrap a list of values in an async iterator."""

    def __init__(self, items: list):
        self._items = items
        self._index = 0

    def __aiter__(self):
        return self

    async def __anext__(self):
        if self._index >= len(self._items):
            raise StopAsyncIteration
        item = self._items[self._index]
        self._index += 1
        return item


@pytest.fixture
def mock_provider():
    provider = MagicMock()
    provider.chat_stream.return_value = AsyncIterator(
        [
            MagicMock(
                delta_content="子代理结果",
                delta_reasoning_content="",
                tool_calls=[],
                usage=None,
            ),
        ],
    )
    provider.chat.return_value = MagicMock(
        content="压缩结果",
        tool_calls=[],
        usage=None,
    )
    return provider


@pytest.fixture
def mock_executor():
    executor = MagicMock()

    async def _fake_execute(*args, **kwargs):
        return classify_tool_text("ok")

    executor.execute_detailed = MagicMock(side_effect=_fake_execute)
    executor.clear_session_trust = MagicMock()
    return executor


@pytest.fixture
def agent(tmp_path, mock_provider, mock_executor):
    return Agent(
        provider=mock_provider,
        executor=mock_executor,
        session_path=tmp_path / ".agenticx" / "session" / "history.json",
    )


class TestAgentToolFiltering:
    """Verify allow_delegate and allowed_tools filter the tool schema."""

    def test_child_agent_cannot_see_delegate_tools(self, agent):
        child = Agent(
            provider=agent.provider,
            executor=agent.executor,
            allow_delegate=False,
            session_path=None,
        )
        schema = [
            {"name": "read_file"},
            {"name": "delegate"},
            {"name": "delegate_parallel"},
        ]
        filtered = child._filter_tools_schema(schema)
        names = {t["name"] for t in filtered}
        assert names == {"read_file"}

    def test_allowed_tools_whitelist(self, agent):
        restricted = Agent(
            provider=agent.provider,
            executor=agent.executor,
            allowed_tools=["read_file", "list_directory"],
            session_path=None,
        )
        schema = [
            {"name": "read_file"},
            {"name": "write_file"},
            {"name": "list_directory"},
            {"name": "delegate"},
        ]
        filtered = restricted._filter_tools_schema(schema)
        names = {t["name"] for t in filtered}
        assert names == {"read_file", "list_directory"}

    def test_parent_agent_sees_delegate_tools_by_default(self, agent):
        schema = [
            {"name": "read_file"},
            {"name": "delegate"},
        ]
        filtered = agent._filter_tools_schema(schema)
        names = {t["name"] for t in filtered}
        assert names == {"read_file", "delegate"}


class TestDelegateTool:
    """Verify delegate and delegate_parallel behavior."""

    @pytest.mark.asyncio
    async def test_parallel_delegates_refuse_write_tools(self, agent):
        delegate_module._parent_agent = agent
        result = await delegate_module._delegate_parallel(
            [
                {"task": "overwrite a file", "tools": ["write_file"]},
            ],
        )
        assert json.loads(result)[0]["status"] == "failed"
        assert "read-only" in json.loads(result)[0]["result"]

    @pytest.fixture(autouse=True)
    def reset_delegate_state(self):
        """Clear parent/config between tests."""
        delegate_module._parent_agent = None
        delegate_module._config = None
        delegate_module._delegate_semaphores.clear()
        yield
        delegate_module._parent_agent = None
        delegate_module._config = None
        delegate_module._delegate_semaphores.clear()

    @pytest.mark.asyncio
    async def test_delegate_returns_json_result(self, agent, tmp_path):
        delegate_module._parent_agent = agent
        result = await delegate_module._delegate("分析 src/auth.py 的安全性")
        parsed = json.loads(result)
        assert parsed["status"] == "completed"
        assert "task_id" in parsed
        assert "result" in parsed

    @pytest.mark.asyncio
    async def test_delegate_parallel_returns_array(self, agent):
        delegate_module._parent_agent = agent
        tasks = [
            {"task": "任务1"},
            {"task": "任务2"},
        ]
        result = await delegate_module._delegate_parallel(tasks)
        parsed = json.loads(result)
        assert isinstance(parsed, list)
        assert len(parsed) == 2
        assert all(r["status"] == "completed" for r in parsed)

    @pytest.mark.asyncio
    async def test_delegate_without_parent_fails_gracefully(self):
        delegate_module._parent_agent = None
        result = await delegate_module._delegate("任务")
        parsed = json.loads(result)
        assert parsed["status"] == "failed"
        assert "not initialized" in parsed["result"]

    @pytest.mark.asyncio
    async def test_delegate_timeout(self, agent):
        """A provider that never yields should result in timeout status."""

        class HangingProvider:
            async def chat_stream(self, *args, **kwargs):
                await asyncio.sleep(10)
                if False:
                    yield None

            async def chat(self, *args, **kwargs):
                return MagicMock(content="", tool_calls=[], usage=None)

        hanging_agent = Agent(
            provider=HangingProvider(),
            executor=agent.executor,
            session_path=None,
        )
        delegate_module._parent_agent = hanging_agent
        result = await delegate_module._delegate("hang", timeout=1)
        parsed = json.loads(result)
        assert parsed["status"] == "timeout"


class TestDelegateLoopSafety:
    """M10: loop-bound semaphore and orphan-child regression tests."""

    @pytest.fixture(autouse=True)
    def reset_delegate_state(self):
        delegate_module._parent_agent = None
        delegate_module._config = None
        delegate_module._delegate_semaphores.clear()
        yield
        delegate_module._parent_agent = None
        delegate_module._config = None
        delegate_module._delegate_semaphores.clear()

    def test_semaphore_is_per_event_loop(self, agent):
        """Two asyncio.run() loops must get distinct semaphores, and both
        delegates must succeed (no cross-loop 'bound to a different event
        loop' error)."""
        delegate_module._parent_agent = agent

        async def _run_once():
            sem = delegate_module._get_delegate_semaphore()
            result = await delegate_module._delegate("任务")
            parsed = json.loads(result)
            assert parsed["status"] == "completed"
            return sem

        sem1 = asyncio.run(_run_once())
        sem2 = asyncio.run(_run_once())
        assert sem1 is not sem2

    @pytest.mark.asyncio
    async def test_cancelling_parent_cancels_child_stream(
        self,
        agent,
        monkeypatch,
    ):
        """Cancelling the delegate task must cancel (and await) the spawned
        child stream task instead of abandoning it."""
        delegate_module._parent_agent = agent
        started = asyncio.Event()
        child_cancelled = asyncio.Event()

        async def spy_drain(stream, chunks):
            started.set()
            try:
                await asyncio.sleep(60)
            except asyncio.CancelledError:
                child_cancelled.set()
                raise

        monkeypatch.setattr(delegate_module, "_drain_stream", spy_drain)

        task = asyncio.create_task(
            delegate_module._delegate("任务", timeout=300),
        )
        await asyncio.wait_for(started.wait(), timeout=5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        assert child_cancelled.is_set()


class TestConfigDelegation:
    """Verify delegation config loads/persists correctly."""

    def test_default_delegation_config(self):
        from dashscope_agenticx.config import Config, DelegationConfig

        cfg = Config()
        assert cfg.delegation == DelegationConfig()
        assert cfg.delegation.max_concurrent == 5
        assert cfg.delegation.default_timeout == 120
        assert cfg.delegation.allow_nested is False

    def test_save_and_load_delegation_config(self, tmp_path):
        from dashscope_agenticx.config import (
            WORKSPACE_CONFIG_FILE,
            Config,
            DelegationConfig,
        )

        cfg = Config()
        cfg.delegation = DelegationConfig(max_concurrent=3, default_timeout=60)

        with patch(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "config.toml",
        ):
            cfg.save_workspace()
            text = (tmp_path / "config.toml").read_text(encoding="utf-8")
            assert (
                'delegation = {"max_concurrent" = 3, "default_timeout" = 60'
                in text
            )

            loaded = Config()
            loaded._load_workspace_from(tmp_path / "config.toml")
            assert loaded.delegation.max_concurrent == 3
            assert loaded.delegation.default_timeout == 60
