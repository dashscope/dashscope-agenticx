# -*- coding: utf-8 -*-
from __future__ import annotations

from dashscope_agenticx.cli.runners import _run_dry_run
from dashscope_agenticx.config import Config, MCPServerConfig


def test_dry_run_minimal_config_no_attribute_error(capsys):
    """Regression: --dry-run crashed on config.mcp_services / config.memory
    (nonexistent attributes)."""
    _run_dry_run(Config())
    out = capsys.readouterr().out
    assert "Readiness" in out


def test_dry_run_lists_mcp_servers(capsys):
    config = Config()
    config.mcp_servers.append(MCPServerConfig(service="time", url="http://x"))
    _run_dry_run(config)
    out = capsys.readouterr().out
    assert "time" in out
    assert "http://x" in out
