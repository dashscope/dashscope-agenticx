# -*- coding: utf-8 -*-
"""Tests for the JSON output mode feature.

Covers:
  * ``Agent.__init__`` ``json_mode`` parameter (default + explicit).
  * ``response_format`` passthrough in ``Agent.run`` / ``Agent.run_stream``.
  * ``/json`` and ``/save`` slash-command dispatch in ``dashscope_agenticx.cli.dispatch``.

Disk-polluting side effects (audit log, history store) are mocked/isolated.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.cli.dispatch import _handle_slash_command
from dashscope_agenticx.providers.base import LLMChunk

# ---------------------------------------------------------------------------
# Shared helpers / fixtures
# ---------------------------------------------------------------------------


def _make_real_agent(json_mode: bool = False, provider=None) -> Agent:
    """Build a real Agent wired with mocked dependencies.

    ``memory_manager`` is supplied so the global singleton / WORKSPACE_DIR
    memory store is never touched at construction time.
    """
    provider = provider or MagicMock()
    executor = MagicMock()
    executor.clear_session_trust = MagicMock()
    memory_manager = MagicMock()
    # Keep the reflection tracker quiet so _reflection_section returns "".
    memory_manager.session.reflection.needs_reflection.return_value = False
    memory_manager.session.reflection.get_reflection_hint.return_value = ""
    return Agent(
        provider=provider,
        executor=executor,
        memory_manager=memory_manager,
        system_prompt="",  # avoid reading WORKSPACE_SYSTEM_PROMPT_FILE
        json_mode=json_mode,
    )


class FakeAgent:
    """Lightweight stand-in for Agent with just the attributes the /json and
    /save handlers touch."""

    def __init__(self, json_mode: bool = False, last_output: str = ""):
        self.json_mode = json_mode
        self.last_output = last_output


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Isolate audit logging and history storage so run()/run_stream() tests
    never write to the real workspace."""
    monkeypatch.setattr(
        "dashscope_agenticx.audit.get_audit_logger",
        lambda: MagicMock(),
    )
    from dashscope_agenticx.platforms.local import history

    monkeypatch.setattr(history, "WORKSPACE_DIR", tmp_path / "ws")
    return tmp_path


@pytest.fixture
def patch_prompt(monkeypatch):
    """Short-circuit prompt assembly so the mocked memory_manager never flows
    into the prompt pipeline (which expects real objects). The system-prompt
    content is irrelevant to response_format passthrough tests."""
    monkeypatch.setattr(
        Agent,
        "_system_prompt_for_turn",
        lambda self, _txt: "system",
    )
    monkeypatch.setattr(Agent, "_reflection_section", lambda self: "")
    monkeypatch.setattr(Agent, "_stagnation_section", lambda self: "")
    monkeypatch.setattr(Agent, "_silence_section", lambda self: "")


# ---------------------------------------------------------------------------
# Agent.__init__ json_mode
# ---------------------------------------------------------------------------


class TestAgentJsonModeInit:
    def test_default_is_false(self):
        agent = _make_real_agent()
        assert agent.json_mode is False

    def test_explicit_true(self):
        agent = _make_real_agent(json_mode=True)
        assert agent.json_mode is True

    def test_explicit_false(self):
        agent = _make_real_agent(json_mode=False)
        assert agent.json_mode is False

    def test_json_mode_is_bool_attribute(self):
        agent = _make_real_agent(json_mode=True)
        assert isinstance(agent.json_mode, bool)


# ---------------------------------------------------------------------------
# /json slash command
# ---------------------------------------------------------------------------


class TestJsonSlashCommand:
    @pytest.fixture
    def mock_console(self, monkeypatch):
        console = MagicMock()
        monkeypatch.setattr("dashscope_agenticx.cli.dispatch.console", console)
        return console

    def test_json_on_sets_true(self, mock_console):
        agent = FakeAgent(json_mode=False)
        result = _handle_slash_command("/json on", agent, MagicMock())
        assert result is True
        assert agent.json_mode is True

    def test_json_true_alias(self, mock_console):
        agent = FakeAgent(json_mode=False)
        _handle_slash_command("/json true", agent, MagicMock())
        assert agent.json_mode is True

    def test_json_1_alias(self, mock_console):
        agent = FakeAgent(json_mode=False)
        _handle_slash_command("/json 1", agent, MagicMock())
        assert agent.json_mode is True

    def test_json_off_sets_false(self, mock_console):
        agent = FakeAgent(json_mode=True)
        result = _handle_slash_command("/json off", agent, MagicMock())
        assert result is True
        assert agent.json_mode is False

    def test_json_false_alias(self, mock_console):
        agent = FakeAgent(json_mode=True)
        _handle_slash_command("/json false", agent, MagicMock())
        assert agent.json_mode is False

    def test_json_0_alias(self, mock_console):
        agent = FakeAgent(json_mode=True)
        _handle_slash_command("/json 0", agent, MagicMock())
        assert agent.json_mode is False

    def test_json_no_arg_shows_status_when_off(self, mock_console):
        agent = FakeAgent(json_mode=False)
        result = _handle_slash_command("/json", agent, MagicMock())
        assert result is True
        # Status printed
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "JSON" in printed
        assert "off" in printed

    def test_json_no_arg_shows_status_when_on(self, mock_console):
        agent = FakeAgent(json_mode=True)
        _handle_slash_command("/json", agent, MagicMock())
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "on" in printed

    def test_json_no_arg_does_not_change_state(self, mock_console):
        agent = FakeAgent(json_mode=True)
        _handle_slash_command("/json", agent, MagicMock())
        assert agent.json_mode is True

    def test_json_invalid_arg_shows_usage(self, mock_console):
        agent = FakeAgent(json_mode=False)
        result = _handle_slash_command("/json maybe", agent, MagicMock())
        assert result is True
        assert agent.json_mode is False  # unchanged
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Usage" in printed

    def test_json_returns_true(self, mock_console):
        assert (
            _handle_slash_command("/json on", FakeAgent(), MagicMock()) is True
        )
        assert _handle_slash_command("/json", FakeAgent(), MagicMock()) is True


# ---------------------------------------------------------------------------
# /save slash command
# ---------------------------------------------------------------------------


class TestSaveSlashCommand:
    @pytest.fixture
    def mock_console(self, monkeypatch):
        console = MagicMock()
        monkeypatch.setattr("dashscope_agenticx.cli.dispatch.console", console)
        return console

    def test_save_with_path_writes_file(self, mock_console, tmp_path):
        agent = FakeAgent(last_output="hello world output")
        out = tmp_path / "out.txt"
        result = _handle_slash_command(f"/save {out}", agent, MagicMock())

        assert result is True
        assert out.read_text(encoding="utf-8") == "hello world output"
        # Success message references char count
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Saved" in printed

    def test_save_no_path_generates_timestamped_filename(
        self,
        mock_console,
        tmp_path,
        monkeypatch,
    ):
        # Run in tmp dir so the generated file doesn't pollute the repo.
        monkeypatch.chdir(tmp_path)
        agent = FakeAgent(last_output="some content")
        _handle_slash_command("/save", agent, MagicMock())

        # Exactly one agenticx_output_*.md file should exist in tmp_path.
        candidates = list(tmp_path.glob("agenticx_output_*.md"))
        assert len(candidates) == 1
        assert candidates[0].read_text(encoding="utf-8") == "some content"

    def test_save_no_path_filename_pattern(
        self,
        mock_console,
        tmp_path,
        monkeypatch,
    ):
        monkeypatch.chdir(tmp_path)
        agent = FakeAgent(last_output="x")
        _handle_slash_command("/save", agent, MagicMock())
        candidates = list(tmp_path.glob("agenticx_output_*.md"))
        assert re.match(
            r"agenticx_output_\d{8}_\d{6}\.md",
            candidates[0].name,
        )

    def test_save_no_last_output_shows_warning(self, mock_console, tmp_path):
        agent = FakeAgent(last_output="")
        result = _handle_slash_command(
            f"/save {tmp_path / 'x.txt'}",
            agent,
            MagicMock(),
        )

        assert result is True
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Nothing to save" in printed
        # File must NOT have been written
        assert not (tmp_path / "x.txt").exists()

    def test_save_empty_last_output_treated_as_nothing(
        self,
        mock_console,
        tmp_path,
    ):
        """An empty-string last_output should be treated as 'nothing to save'."""
        agent = FakeAgent(last_output="")
        assert _handle_slash_command("/save", agent, MagicMock()) is True
        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Nothing to save" in printed

    def test_save_returns_true(self, mock_console, tmp_path):
        agent = FakeAgent(last_output="data")
        assert (
            _handle_slash_command(
                f"/save {tmp_path / 'a.txt'}",
                agent,
                MagicMock(),
            )
            is True
        )

    def test_save_multiline_output_preserved(self, mock_console, tmp_path):
        content = "line1\nline2\nline3\n"
        agent = FakeAgent(last_output=content)
        out = tmp_path / "multi.md"
        _handle_slash_command(f"/save {out}", agent, MagicMock())
        assert out.read_text(encoding="utf-8") == content


# ---------------------------------------------------------------------------
# response_format passthrough in Agent.run_stream
# ---------------------------------------------------------------------------


class TestRunStreamResponseFormatPassthrough:
    @pytest.fixture
    def provider(self):
        captured: dict = {}

        async def _stream(*args, **kwargs):
            captured["kwargs"] = kwargs
            yield LLMChunk(delta_content='{"ok": true}', finish_reason="stop")

        p = MagicMock()
        p.chat_stream = _stream
        p.captured = captured
        return p

    @pytest.mark.asyncio
    async def test_run_stream_passes_json_object_when_json_mode_on(
        self,
        provider,
        isolated_env,
        patch_prompt,
    ):
        agent = _make_real_agent(json_mode=True, provider=provider)
        chunks = [c async for c in agent.run_stream("ping")]

        assert chunks == ['{"ok": true}']
        assert provider.captured["kwargs"]["response_format"] == {
            "type": "json_object",
        }

    @pytest.mark.asyncio
    async def test_run_stream_passes_none_when_json_mode_off(
        self,
        provider,
        isolated_env,
        patch_prompt,
    ):
        agent = _make_real_agent(json_mode=False, provider=provider)
        async for _ in agent.run_stream("ping"):
            pass

        assert provider.captured["kwargs"]["response_format"] is None

    @pytest.mark.asyncio
    async def test_json_mode_toggle_changes_passthrough(
        self,
        provider,
        isolated_env,
        patch_prompt,
    ):
        """Toggling agent.json_mode between turns flips response_format."""
        agent = _make_real_agent(json_mode=False, provider=provider)
        async for _ in agent.run_stream("first"):
            pass
        assert provider.captured["kwargs"]["response_format"] is None

        agent.json_mode = True
        async for _ in agent.run_stream("second"):
            pass
        assert provider.captured["kwargs"]["response_format"] == {
            "type": "json_object",
        }


# ---------------------------------------------------------------------------
# Dispatch routing: /json and /save are recognized commands
# ---------------------------------------------------------------------------


class TestDispatchRouting:
    def test_json_command_is_handled(self, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.cli.dispatch.console",
            MagicMock(),
        )
        agent = FakeAgent(json_mode=False)
        assert _handle_slash_command("/json on", agent, MagicMock()) is True

    def test_save_command_is_handled(self, monkeypatch, tmp_path):
        monkeypatch.setattr(
            "dashscope_agenticx.cli.dispatch.console",
            MagicMock(),
        )
        agent = FakeAgent(last_output="data")
        out = tmp_path / "r.txt"
        assert (
            _handle_slash_command(f"/save {out}", agent, MagicMock()) is True
        )
        assert out.exists()

    def test_unknown_command_not_handled(self, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.cli.dispatch.console",
            MagicMock(),
        )
        # An unrecognized slash command returns False.
        assert (
            _handle_slash_command("/nope", FakeAgent(), MagicMock()) is False
        )
