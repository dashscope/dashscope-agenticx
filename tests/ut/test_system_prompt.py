# -*- coding: utf-8 -*-
"""Tests for system prompt multi-level loading priority."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from dashscope_agenticx.cli import _load_system_prompt

# ---------------------------------------------------------------------------
# _load_system_prompt: workspace > global > None
# ---------------------------------------------------------------------------


class TestLoadSystemPromptPriority:
    """Verify _load_system_prompt reads from WORKSPACE first, then CONFIG."""

    def test_workspace_takes_precedence(self, tmp_path):
        ws_dir = tmp_path / "ws"
        cfg_dir = tmp_path / "cfg"
        ws_dir.mkdir()
        cfg_dir.mkdir()

        (ws_dir / "system-prompt.md").write_text(
            "workspace prompt",
            encoding="utf-8",
        )
        (cfg_dir / "system-prompt.md").write_text(
            "global prompt",
            encoding="utf-8",
        )

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            assert _load_system_prompt() == "workspace prompt"

    def test_fallback_to_config(self, tmp_path):
        ws_dir = tmp_path / "ws"
        cfg_dir = tmp_path / "cfg"
        ws_dir.mkdir()
        cfg_dir.mkdir()

        (cfg_dir / "system-prompt.md").write_text(
            "global prompt",
            encoding="utf-8",
        )

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            assert _load_system_prompt() == "global prompt"

    def test_returns_none_when_both_missing(self, tmp_path):
        ws_dir = tmp_path / "ws"
        cfg_dir = tmp_path / "cfg"
        ws_dir.mkdir()
        cfg_dir.mkdir()

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            assert _load_system_prompt() is None

    def test_empty_workspace_file_returns_none(self, tmp_path):
        """A whitespace-only workspace file is read first and returns None
        (strip() == ''). It does NOT fallback to CONFIG_DIR — the function
        returns on the first file found."""
        ws_dir = tmp_path / "ws"
        cfg_dir = tmp_path / "cfg"
        ws_dir.mkdir()
        cfg_dir.mkdir()

        (ws_dir / "system-prompt.md").write_text("   \n  ", encoding="utf-8")
        (cfg_dir / "system-prompt.md").write_text(
            "global fallback",
            encoding="utf-8",
        )

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            assert _load_system_prompt() is None


# ---------------------------------------------------------------------------
# Real scenario: cwd at src/dashscope_agenticx/examples/dashscope-sdk-expert picks up its prompt
# ---------------------------------------------------------------------------


class TestRealWorkspaceScenario:
    """End-to-end test using the actual project layout."""

    DASHSCOPE_EXPERT_DIR = (
        Path(__file__).resolve().parent.parent.parent
        / "examples"
        / "dashscope-sdk-expert"
    )

    def test_cwd_dashscope_expert_loads_prompt(self):
        """chdir to examples/dashscope-sdk-expert, verify the prompt is read."""
        if not self.DASHSCOPE_EXPERT_DIR.is_dir():
            pytest.skip("examples/dashscope-sdk-expert not present")

        agenticx_dir = self.DASHSCOPE_EXPERT_DIR / ".agenticx"
        prompt_file = agenticx_dir / "system-prompt.md"
        if not prompt_file.is_file():
            pytest.skip(
                ".agenticx/system-prompt.md not present in dashscope-sdk-expert",
            )

        expected = prompt_file.read_text(encoding="utf-8").strip()

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", agenticx_dir),
            patch(
                "dashscope_agenticx.config.CONFIG_DIR",
                self.DASHSCOPE_EXPERT_DIR / "_nonexistent_cfg",
            ),
            patch(
                "dashscope_agenticx.prompt.discover_project_instructions",
                return_value=None,
            ),
        ):
            result = _load_system_prompt()

        assert result == expected
        assert "DashScope SDK Expert" in result

    def test_cwd_dashscope_expert_prompt_has_content(self):
        """Sanity: the real prompt file is non-empty and contains key sections."""
        prompt_file = (
            self.DASHSCOPE_EXPERT_DIR / ".agenticx" / "system-prompt.md"
        )
        if not prompt_file.is_file():
            pytest.skip("prompt file not present")

        content = prompt_file.read_text(encoding="utf-8").strip()
        assert len(content) > 100
        assert "DashScope" in content
        assert "Response Strategy" in content


# ---------------------------------------------------------------------------
# Agent final prompt priority: embedded > _load_system_prompt > SYSTEM_PROMPT
# ---------------------------------------------------------------------------


class TestAgentPromptPriority:
    """Verify Agent uses the right base prompt after construction."""

    def _make_agent(self, system_prompt: str | None = None):
        from pathlib import Path
        from unittest.mock import MagicMock

        from dashscope_agenticx.agent import Agent

        provider = MagicMock()
        executor = MagicMock()
        return Agent(
            provider=provider,
            executor=executor,
            system_prompt=system_prompt,
            # Pass a non-existent workspace dir to avoid loading .agenticx/system-prompt.md
            session_path=Path(
                "/tmp/nonexistent/.agenticx/session/history.json",
            ),
        )

    def test_explicit_prompt_wins(self):
        agent = self._make_agent(system_prompt="explicit prompt")
        assert agent.system_prompt == "explicit prompt"

    def test_none_prompt_falls_back_in_run(self, tmp_path):
        """When no workspace system-prompt.md exists, system_prompt stays None."""
        from unittest.mock import MagicMock, patch

        from dashscope_agenticx.agent import Agent

        provider = MagicMock()
        executor = MagicMock()
        # Point WORKSPACE_DIR and CONFIG_DIR to empty dirs so _load_system_prompt returns None
        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", tmp_path),
            patch("dashscope_agenticx.config.CONFIG_DIR", tmp_path),
        ):
            agent = Agent(
                provider=provider,
                executor=executor,
                system_prompt=None,
                session_path=tmp_path
                / ".agenticx"
                / "session"
                / "history.json",
            )
            assert agent.system_prompt is None

    def test_cli_instantiation_order(self, tmp_path):
        """Simulate cli.py's Agent creation:
        config._embedded_system_prompt  >  _load_system_prompt()  >  default.
        """
        ws_dir = tmp_path / "ws"
        cfg_dir = tmp_path / "cfg"
        ws_dir.mkdir()
        cfg_dir.mkdir()

        (ws_dir / "system-prompt.md").write_text(
            "file prompt",
            encoding="utf-8",
        )

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            # Case 1: embedded prompt present → it wins
            resolved = "embedded prompt" or _load_system_prompt()
            agent = self._make_agent(system_prompt=resolved)
            assert agent.system_prompt == "embedded prompt"

            # Case 2: no embedded → file prompt used
            resolved2 = None or _load_system_prompt()
            agent2 = self._make_agent(system_prompt=resolved2)
            assert agent2.system_prompt == "file prompt"

            # Case 3: nothing → Agent.run() falls back to SYSTEM_PROMPT at runtime
            # Point to non-existent file so system_prompt stays None
            with patch("dashscope_agenticx.config.WORKSPACE_DIR", tmp_path):
                agent3 = self._make_agent(system_prompt=None)
                assert agent3.system_prompt is None
