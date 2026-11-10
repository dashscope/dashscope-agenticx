# -*- coding: utf-8 -*-
"""Tests that /help lists every top-level command the dispatcher handles."""

from __future__ import annotations

from dashscope_agenticx.commands import render_help_text

# Commands dispatched by cli/dispatch.py:_handle_slash_command that were
# previously missing from the /help output (regression coverage).
_PREVIOUSLY_MISSING = [
    "/compress",
    "/history",
    "/feedback",
    "/report",
    "/directives",
    "/audit",
    "/debug",
    "/log",
]


def test_help_lists_previously_missing_commands():
    text = render_help_text()
    for cmd in _PREVIOUSLY_MISSING:
        assert cmd in text, f"{cmd} missing from /help output"


def test_help_lists_core_commands():
    text = render_help_text()
    for cmd in [
        "/help",
        "/clear",
        "/info",
        "/stats",
        "/voice",
        "/tts",
        "/camera",
        "/copy",
        "/save",
        "/json",
        "/setup",
        "/capability",
        "/subagents",
        "/provider",
        "/trust",
        "/rule",
        "/privacy",
        "/theme",
        "/profile",
        "/memory",
        "/session",
        "/summarize",
        "/mcp",
        "/skill",
        "/cron",
        "/dev",
        "/expert",
        "/exit",
    ]:
        assert cmd in text, f"{cmd} missing from /help output"
