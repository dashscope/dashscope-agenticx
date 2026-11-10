# -*- coding: utf-8 -*-
"""End-to-end tests for basic chat and streaming."""

from __future__ import annotations

import pytest

import dashscope_agenticx.config
from dashscope_agenticx.providers.base import LLMResponse, ToolCall
from dashscope_agenticx.sdk import run_once

pytestmark = pytest.mark.e2e


class TestChat:
    async def test_run_once_sync_returns_fake_response(self, make_agent):
        agent, fake, config = make_agent({"hello": "Hello, test passes"})
        result = await run_once("hello", config=config)
        assert "test passes" in result

    async def test_run_stream_accumulates_chunks(self, make_agent):
        agent, fake, config = make_agent({"hello": "Hello"})
        chunks = []
        async for chunk in agent.run_stream("hello"):
            chunks.append(chunk)
        full = "".join(chunks)
        assert "Hello" in full

    async def test_chat_round_is_saved_to_session(self, make_agent):
        agent, fake, config = make_agent({"hello": "Hello"})
        agent.session_path = (
            dashscope_agenticx.config.WORKSPACE_DIR
            / "session"
            / "default"
            / "history.json"
        )
        async for _ in agent.run_stream("hello"):
            pass
        assert agent.session_path.exists()
        data = agent.session_path.read_text(encoding="utf-8")
        assert "hello" in data
        assert "Hello" in data

    async def test_json_mode_response(self, make_agent):
        agent, fake, config = make_agent({"report": '{"status": "ok"}'})
        agent.json_mode = True
        chunks = []
        async for chunk in agent.run_stream("report"):
            chunks.append(chunk)
        assert '{"status": "ok"}' in "".join(chunks)
