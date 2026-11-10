# -*- coding: utf-8 -*-
"""Command Registry behavior for the Phase 7 dispatch seam."""
from dashscope_agenticx.cli.command_registry import (
    CommandContext,
    CommandRegistry,
)


def test_command_registry_dispatches_by_first_token():
    registry = CommandRegistry()
    registry.register("/hello", lambda context: context.command.upper())
    context = CommandContext("/hello world", object(), object())
    assert registry.handles(context.command)
    assert registry.dispatch(context) == "/HELLO WORLD"


def test_command_registry_rejects_non_slash_name():
    registry = CommandRegistry()
    try:
        registry.register("hello", lambda context: True)
    except ValueError as exc:
        assert "slash command" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("invalid command name accepted")
