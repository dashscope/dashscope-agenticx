# -*- coding: utf-8 -*-
"""Tests for rules.jsonl loading and /rule command handler."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from dashscope_agenticx.cli.handlers_config import _handle_rule_command
from dashscope_agenticx.config import Config
from dashscope_agenticx.prompt import (
    _load_rules_jsonl,
    discover_project_instructions,
)


class TestLoadRulesJsonl:
    def test_loads_enabled_rules_as_bullets(self, tmp_path):
        rules_file = tmp_path / "rules.jsonl"
        rules_file.write_text(
            json.dumps({"text": "rule one", "enabled": True})
            + "\n"
            + json.dumps({"text": "rule two", "enabled": True})
            + "\n",
            encoding="utf-8",
        )
        result = _load_rules_jsonl(rules_file)
        assert result == "- rule one\n- rule two"

    def test_skips_disabled_and_malformed_lines(self, tmp_path):
        rules_file = tmp_path / "rules.jsonl"
        rules_file.write_text(
            json.dumps({"text": "enabled rule", "enabled": True}) + "\n"
            "not valid json\n"
            + json.dumps({"text": "disabled rule", "enabled": False})
            + "\n"
            + json.dumps({"text": "", "enabled": True})
            + "\n",
            encoding="utf-8",
        )
        result = _load_rules_jsonl(rules_file)
        assert result == "- enabled rule"

    def test_missing_file_returns_empty(self, tmp_path):
        assert _load_rules_jsonl(tmp_path / "missing.jsonl") == ""


class TestDiscoverProjectInstructions:
    def test_prefers_rules_jsonl_over_rules_md(self, tmp_path):
        start = tmp_path / "project"
        agenticx_dir = start / ".agenticx"
        agenticx_dir.mkdir(parents=True)
        (agenticx_dir / "rules.jsonl").write_text(
            json.dumps({"text": "jsonl rule", "enabled": True}) + "\n",
            encoding="utf-8",
        )
        (agenticx_dir / "rules.md").write_text(
            "- md rule\n",
            encoding="utf-8",
        )

        result = discover_project_instructions(start)
        assert result is not None
        assert "jsonl rule" in result
        assert "md rule" not in result

    def test_ignores_rules_md(self, tmp_path):
        start = tmp_path / "project"
        agenticx_dir = start / ".agenticx"
        agenticx_dir.mkdir(parents=True)
        (agenticx_dir / "rules.md").write_text(
            "- md rule\n",
            encoding="utf-8",
        )

        result = discover_project_instructions(start)
        assert result is None


class TestRuleCommand:
    def test_add_and_list(self, tmp_path, capsys):
        ws = tmp_path / ".agenticx"
        ws.mkdir()
        config = Config()

        with patch("dashscope_agenticx.config.WORKSPACE_DIR", ws):
            _handle_rule_command("/rule add do not commit immediately", config)
            _handle_rule_command("/rule list", config)

        rules_file = ws / "rules.jsonl"
        assert rules_file.exists()
        entries = [
            json.loads(line)
            for line in rules_file.read_text(encoding="utf-8")
            .strip()
            .split("\n")
        ]
        assert len(entries) == 1
        assert entries[0]["text"] == "do not commit immediately"
        assert entries[0]["enabled"] is True

    def test_remove(self, tmp_path):
        ws = tmp_path / ".agenticx"
        ws.mkdir()
        rules_file = ws / "rules.jsonl"
        rules_file.write_text(
            json.dumps({"text": "first", "enabled": True})
            + "\n"
            + json.dumps({"text": "second", "enabled": True})
            + "\n",
            encoding="utf-8",
        )
        config = Config()

        with patch("dashscope_agenticx.config.WORKSPACE_DIR", ws):
            _handle_rule_command("/rule remove 1", config)

        entries = [
            json.loads(line)
            for line in rules_file.read_text(encoding="utf-8")
            .strip()
            .split("\n")
        ]
        assert len(entries) == 1
        assert entries[0]["text"] == "second"

    def test_disable(self, tmp_path):
        ws = tmp_path / ".agenticx"
        ws.mkdir()
        rules_file = ws / "rules.jsonl"
        rules_file.write_text(
            json.dumps({"text": "first", "enabled": True}) + "\n",
            encoding="utf-8",
        )
        config = Config()

        with patch("dashscope_agenticx.config.WORKSPACE_DIR", ws):
            _handle_rule_command("/rule disable 1", config)

        entries = [
            json.loads(line)
            for line in rules_file.read_text(encoding="utf-8")
            .strip()
            .split("\n")
        ]
        assert entries[0]["enabled"] is False
