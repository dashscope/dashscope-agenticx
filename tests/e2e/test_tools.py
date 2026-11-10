# -*- coding: utf-8 -*-
"""End-to-end tests for tool execution with a fake LLM."""

from __future__ import annotations

import json

import pytest

import dashscope_agenticx.config
from dashscope_agenticx.providers.base import LLMResponse, ToolCall

pytestmark = pytest.mark.e2e


def _build_tool_responder(tool_calls: list[ToolCall], final: str):
    """Return a callable that emits tool calls on turn 1 and ``final`` text afterwards."""

    def _respond(messages: list[dict], turn: int) -> LLMResponse:
        if turn == 1:
            return LLMResponse(tool_calls=tool_calls)
        return LLMResponse(content=final)

    return _respond


class TestTools:
    async def test_fake_llm_can_emit_tool_call(self, make_agent):
        agent, fake, config = make_agent(
            _build_tool_responder(
                [
                    ToolCall(
                        id="1",
                        name="read_file",
                        arguments={"path": "README.md"},
                    ),
                ],
                "File read",
            ),
        )
        async for _ in agent.run_stream("read the file"):
            pass
        assert any(msg.get("role") == "tool" for msg in agent.messages)

    async def test_read_file_tool(self, tmp_path, make_agent):
        test_file = tmp_path / "demo.txt"
        test_file.write_text("hello e2e", encoding="utf-8")

        agent, fake, config = make_agent(
            _build_tool_responder(
                [
                    ToolCall(
                        id="1",
                        name="read_file",
                        arguments={"path": str(test_file)},
                    ),
                ],
                "File content is hello e2e",
            ),
        )
        async for _ in agent.run_stream("read the file"):
            pass

        tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
        assert tool_messages
        assert "hello e2e" in tool_messages[0].get("content", "")

    async def test_write_file_tool(self, make_agent):
        # write_file validates the path is under the current workspace.
        target = dashscope_agenticx.config.WORKSPACE_DIR / "written.txt"

        agent, fake, config = make_agent(
            _build_tool_responder(
                [
                    ToolCall(
                        id="1",
                        name="write_file",
                        arguments={
                            "path": str(target),
                            "content": "written by fake llm",
                        },
                    ),
                ],
                "File written",
            ),
        )
        async for _ in agent.run_stream("write a file"):
            pass

        assert target.exists()
        assert target.read_text(encoding="utf-8") == "written by fake llm"

    async def test_shell_tool(self, make_agent):
        agent, fake, config = make_agent(
            _build_tool_responder(
                [
                    ToolCall(
                        id="1",
                        name="run_command",
                        arguments={"command": "echo shell-ok"},
                    ),
                ],
                "Command executed",
            ),
        )
        async for _ in agent.run_stream("run a command"):
            pass

        tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
        assert any("shell-ok" in m.get("content", "") for m in tool_messages)

    async def test_web_search_with_mocked_ddgs(self, monkeypatch, make_agent):
        """Mock DuckDuckGo so web_search runs without real HTTP."""

        class FakeDDGS:
            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

            def text(self, query, region, max_results):
                return [
                    {
                        "title": f"Result for {query}",
                        "href": "https://example.com",
                        "body": "mock result body",
                    },
                ]

        monkeypatch.setattr(
            "dashscope_agenticx.tools.web_search._get_ddgs_class",
            lambda: FakeDDGS,
        )

        agent, fake, config = make_agent(
            _build_tool_responder(
                [
                    ToolCall(
                        id="1",
                        name="web_search",
                        arguments={"query": "pytest"},
                    ),
                ],
                "Search complete",
            ),
        )
        async for _ in agent.run_stream("search the web"):
            pass

        tool_messages = [m for m in agent.messages if m.get("role") == "tool"]
        assert any("pytest" in m.get("content", "") for m in tool_messages)
