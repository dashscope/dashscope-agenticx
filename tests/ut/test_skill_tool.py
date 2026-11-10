# -*- coding: utf-8 -*-
"""Tests for the model-invocable use_skill tool."""

from __future__ import annotations

import pytest

from dashscope_agenticx.executor import Executor
from dashscope_agenticx.skills.base import BUILTIN_SKILLS, Skill
from dashscope_agenticx.tools.registry import registry
from dashscope_agenticx.tools.skills import register_skill_tools


@pytest.fixture
def clean_registry():
    before = dict(registry._tools)
    yield registry
    registry._tools.clear()
    registry._tools.update(before)


@pytest.fixture
def demo_skill():
    skill = Skill(
        name="demo-skill",
        description="demo",
        mcp_service="",
        prompt_template="Do {thing} now.",
        arguments=["thing"],
    )
    BUILTIN_SKILLS[skill.name] = skill
    yield skill
    BUILTIN_SKILLS.pop(skill.name, None)


class _Agent:
    def __init__(self):
        self.executor = Executor()
        self.turn_skills = 0


async def test_use_skill_renders_and_counts(clean_registry, demo_skill):
    agent = _Agent()
    register_skill_tools(get_agent=lambda: agent)
    out = await clean_registry.get("use_skill").func(
        name="demo-skill",
        args=["x"],
    )
    assert out == "Do x now."
    assert agent.turn_skills == 1
    stats = agent.executor.get_stats()
    assert stats["skill_calls"] == 1
    assert stats["skill_counts"] == {"demo-skill": 1}


async def test_use_skill_unknown(clean_registry):
    register_skill_tools(get_agent=lambda: None)
    out = await clean_registry.get("use_skill").func(
        name="no-such-skill",
        args=[],
    )
    assert "unknown skill" in out


async def test_use_skill_missing_args(clean_registry, demo_skill):
    agent = _Agent()
    register_skill_tools(get_agent=lambda: agent)
    out = await clean_registry.get("use_skill").func(
        name="demo-skill",
        args=[],
    )
    # The hint must name the argument. It used to come out empty whenever
    # render_skill failed for a reason other than a missing argument, which
    # told the model a no-argument skill was waiting on arguments.
    assert out == "Error: missing args. 'demo-skill' requires: <thing>"
    assert agent.turn_skills == 0
    assert agent.executor.get_stats()["skill_calls"] == 0


async def test_use_skill_renders_a_no_arg_skill_documenting_json(
    clean_registry,
):
    """The bundled text-generation shape: no arguments, JSON in the body."""
    body = (
        '| messages | `[{"role":..., "content":...}]` |\n'
        '| response_format | `{"type": "json_object"}` |'
    )
    skill = Skill(
        name="json-body-skill",
        description="",
        mcp_service="",
        prompt_template=body,
        arguments=[],
    )
    BUILTIN_SKILLS[skill.name] = skill
    try:
        register_skill_tools(get_agent=lambda: None)
        out = await clean_registry.get("use_skill").func(
            name="json-body-skill",
            args=[],
        )
        assert out == body
    finally:
        BUILTIN_SKILLS.pop(skill.name, None)


def test_use_skill_always_in_schema(clean_registry):
    register_skill_tools(get_agent=lambda: None)
    names = {t["name"] for t in clean_registry.to_schema_list("无关输入")}
    assert "use_skill" in names
