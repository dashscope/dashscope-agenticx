# -*- coding: utf-8 -*-
"""Tests for .agenticx/references auto-loading (_load_references/_compose_system_prompt)."""

from __future__ import annotations

from unittest.mock import patch

from dashscope_agenticx.cli.startup import (
    _compose_system_prompt,
    _load_references,
)


def _ws_cfg(tmp_path):
    ws_dir = tmp_path / "ws"
    cfg_dir = tmp_path / "cfg"
    ws_dir.mkdir()
    cfg_dir.mkdir()
    return ws_dir, cfg_dir


class TestLoadReferences:
    def test_missing_dirs_returns_none(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            assert _load_references() is None

    def test_workspace_references_loaded(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        ref_dir = ws_dir / "references"
        ref_dir.mkdir()
        (ref_dir / "python-sdk.md").write_text(
            "# Python SDK\n\nGeneration.call",
            encoding="utf-8",
        )

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            result = _load_references()

        assert result == "# Python SDK\n\nGeneration.call"

    def test_global_and_workspace_both_loaded_in_order(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        (cfg_dir / "references").mkdir()
        (ws_dir / "references").mkdir()
        (cfg_dir / "references" / "global.md").write_text(
            "GLOBAL",
            encoding="utf-8",
        )
        (ws_dir / "references" / "ws.md").write_text(
            "WORKSPACE",
            encoding="utf-8",
        )

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            result = _load_references()

        assert result == "GLOBAL\n\n---\n\nWORKSPACE"

    def test_frontmatter_stripped(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        ref_dir = ws_dir / "references"
        ref_dir.mkdir()
        (ref_dir / "ref.md").write_text(
            "---\nname: python-sdk\nsdk_version: 1.26.3\n---\n\n# Body\n",
            encoding="utf-8",
        )

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            result = _load_references()

        assert result == "# Body"

    def test_same_name_deduped_workspace_wins(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        (cfg_dir / "references").mkdir()
        (ws_dir / "references").mkdir()
        (cfg_dir / "references" / "sdk.md").write_text(
            "GLOBAL SDK",
            encoding="utf-8",
        )
        (ws_dir / "references" / "sdk.md").write_text(
            "WORKSPACE SDK",
            encoding="utf-8",
        )

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            result = _load_references()

        assert result == "WORKSPACE SDK"

    def test_empty_body_skipped(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        ref_dir = ws_dir / "references"
        ref_dir.mkdir()
        (ref_dir / "empty.md").write_text(
            "---\nname: x\n---\n\n  \n",
            encoding="utf-8",
        )
        (ref_dir / "real.md").write_text("REAL", encoding="utf-8")

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            result = _load_references()

        assert result == "REAL"


class TestComposeSystemPrompt:
    def test_no_references_returns_base(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            assert _compose_system_prompt("base") == "base"
            assert _compose_system_prompt(None) is None

    def test_references_appended_to_base(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        ref_dir = ws_dir / "references"
        ref_dir.mkdir()
        (ref_dir / "sdk.md").write_text("SDK DOCS", encoding="utf-8")

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            result = _compose_system_prompt("persona")

        assert result is not None
        assert result.startswith(
            "persona\n\n---\n\n## References (auto-loaded)",
        )
        assert "SDK DOCS" in result

    def test_references_alone_when_no_base(self, tmp_path):
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        ref_dir = ws_dir / "references"
        ref_dir.mkdir()
        (ref_dir / "sdk.md").write_text("SDK DOCS", encoding="utf-8")

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            result = _compose_system_prompt(None)

        assert result == "## References (auto-loaded)\n\nSDK DOCS"

    def test_embedded_prompt_still_gets_references(self, tmp_path):
        """Embedded mode passes an explicit prompt that bypasses
        _load_system_prompt; references must still be appended."""
        ws_dir, cfg_dir = _ws_cfg(tmp_path)
        ref_dir = cfg_dir / "references"
        ref_dir.mkdir()
        (ref_dir / "sdk.md").write_text("SDK DOCS", encoding="utf-8")

        with (
            patch("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir),
            patch("dashscope_agenticx.config.CONFIG_DIR", cfg_dir),
        ):
            embedded = "You are DashScope SDK Expert"
            result = _compose_system_prompt(embedded)

        assert result is not None
        assert result.startswith(embedded)
        assert "SDK DOCS" in result
