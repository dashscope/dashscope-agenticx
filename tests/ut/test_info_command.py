# -*- coding: utf-8 -*-
"""Tests for the /info slash command."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from dashscope_agenticx.cli.dispatch import _handle_slash_command


class FakeAgent:
    """Lightweight stand-in for Agent with the attributes /info touches."""

    def __init__(
        self,
        provider_name: str = "tongyi",
        model_name: str = "qwen3.7-max",
        json_mode: bool = False,
    ):
        self.provider_name = provider_name
        self.model_name = model_name
        self.json_mode = json_mode


def _make_config(**overrides) -> SimpleNamespace:
    defaults = {
        "provider": "tongyi",
        "model": "qwen3.7-max",
        "protocol": "openai",
        "base_url": "",
        "user_name": "tester",
        "memory_enabled": True,
        "enabled_capabilities": ["bailian.mcp"],
        "loop_mode": "auto",
        "max_turns": 1000,
        "timeout": 30,
        "tui": True,
        "privacy_mode": False,
        "tongyi_api_key": "",
        "anthropic_api_key": "",
        "openai_api_key": "",
        "fallback_providers": None,
    }
    defaults.update(overrides)
    return SimpleNamespace(**defaults)


class TestInfoCommand:
    @pytest.fixture
    def mock_console(self, monkeypatch):
        console = MagicMock()
        monkeypatch.setattr("dashscope_agenticx.cli.dispatch.console", console)
        return console

    def test_info_returns_true(self, mock_console):
        agent = FakeAgent()
        config = _make_config()
        assert _handle_slash_command("/info", agent, config) is True

    def test_info_shows_the_enforced_command_timeout(
        self,
        mock_console,
        monkeypatch,
    ):
        """config.timeout is the LLM request timeout, not run_command's.

        Printing it alone under the label "Timeout" reported a limit nothing
        enforced and hid the one that was actually killing commands.
        """
        monkeypatch.setenv("AGENTICX_COMMAND_TIMEOUT", "45")
        config = _make_config(timeout=30)
        _handle_slash_command("/info", FakeAgent(), config)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "LLM timeout" in printed
        assert "Cmd timeout" in printed
        assert "45s" in printed

    def test_info_prints_provider_and_model(self, mock_console):
        agent = FakeAgent(provider_name="ideatalk", model_name="qwen3.7-plus")
        config = _make_config(provider="ideatalk", model="qwen3.7-plus")
        _handle_slash_command("/info", agent, config)

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "ideatalk" in printed
        assert "qwen3.7-plus" in printed

    def test_info_prints_config_files(
        self,
        mock_console,
        tmp_path,
        monkeypatch,
    ):
        # Create dummy config/extension files so /info shows them as existing.
        ws = tmp_path / "ws"
        ws.mkdir()
        cfg = ws / "config.toml"
        cfg.write_text("provider = 'tongyi'\n", encoding="utf-8")
        ext = ws / "custom-extensions.toml"
        ext.write_text("", encoding="utf-8")

        # /info imports these constants locally from their source modules.
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "global_config.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            cfg,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.GLOBAL_EXTENSIONS_FILE",
            tmp_path / "global_ext.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.WORKSPACE_EXTENSIONS_FILE",
            ext,
        )

        _handle_slash_command("/info", FakeAgent(), _make_config())

        printed = " ".join(str(c) for c in mock_console.print.call_args_list)
        assert "Workspace config" in printed
        assert "Workspace extensions" in printed
