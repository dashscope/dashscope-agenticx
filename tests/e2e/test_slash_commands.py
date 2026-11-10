# -*- coding: utf-8 -*-
"""End-to-end tests for slash command dispatch."""

from __future__ import annotations

import pytest

from dashscope_agenticx.providers.base import LLMResponse, ToolCall

pytestmark = pytest.mark.e2e


class TestSlashCommands:
    async def test_info_shows_provider_and_model(self, dispatch):
        _, _, _, captured = await dispatch("/info")
        assert "tongyi" in captured.out
        assert "qwen3.7-plus" in captured.out
        assert "Config files" in captured.out

    async def test_help_renders_command_list(self, dispatch):
        _, _, _, captured = await dispatch("/help")
        assert "/info" in captured.out
        assert "/help" in captured.out
        assert "/clear" in captured.out

    async def test_clear_empties_history(self, dispatch):
        agent, _, _, _ = await dispatch("/clear")
        assert agent.messages == []

    async def test_json_toggles_mode(self, dispatch):
        agent, _, _, _ = await dispatch("/json on")
        assert agent.json_mode is True
        agent, _, _, _ = await dispatch("/json off")
        assert agent.json_mode is False

    async def test_stats_renders(self, dispatch):
        _, _, _, captured = await dispatch("/stats")
        assert "Session stats" in captured.out

    async def test_provider_wizard_switches_model_and_protocol(
        self,
        dispatch,
        monkeypatch,
    ):
        answers = iter(["", "qwen3.7-max", "y", "", "anthropic"])
        monkeypatch.setattr("builtins.input", lambda *a, **k: next(answers))
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: "")
        agent, _, config, captured = await dispatch("/provider")
        assert config.model == "qwen3.7-max"
        assert config.protocol == "anthropic"
        assert "qwen3.7-max" in captured.out

    async def test_provider_wizard_enter_keeps_current(
        self,
        dispatch,
        monkeypatch,
    ):
        monkeypatch.setattr("builtins.input", lambda *a, **k: "")
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: "")
        agent, _, config, captured = await dispatch("/provider")
        assert config.provider == "tongyi"
        assert config.model == "qwen3.7-plus"
        assert "tongyi" in captured.out

    async def test_dev_test_provider_returns_ok(self, dispatch):
        _, _, _, captured = await dispatch("/dev test provider tongyi")
        assert "is reachable" in captured.out

    async def test_session_new_creates_topic(self, dispatch):
        agent, _, config, captured = await dispatch("/session new e2e-topic")
        assert "Created and switched to new session" in captured.out
        assert "e2e-topic" in captured.out

    async def test_session_list_shows_topics(self, dispatch):
        await dispatch("/session new e2e-topic")
        _, _, _, captured = await dispatch("/session list")
        assert "e2e-topic" in captured.out

    async def test_audit_clear(self, dispatch):
        _, _, _, captured = await dispatch("/audit clear")
        assert "Audit log cleared" in captured.out
