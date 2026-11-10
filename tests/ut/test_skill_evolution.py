# -*- coding: utf-8 -*-
"""Tests for memory/skill_evolution.py — trajectory analysis and skill generation."""

from __future__ import annotations

import pytest

import dashscope_agenticx.memory.skill_evolution as se


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "WORKSPACE_DIR", tmp_path)


def _make_messages(
    tools: list[str],
    last_content: str = "完成操作",
) -> list[dict]:
    msgs = [{"role": "user", "content": "do something with `file.py`"}]
    msgs.append(
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": t}} for t in tools],
            "content": "",
        },
    )
    msgs.append({"role": "assistant", "content": last_content})
    return msgs


# ── analyze_trajectory ────────────────────────────────────────────────────


def test_analyze_trajectory_empty_returns_none():
    assert se.analyze_trajectory([]) is None


def test_analyze_trajectory_single_tool_returns_none():
    msgs = _make_messages(["read_file"])
    assert se.analyze_trajectory(msgs) is None


def test_analyze_trajectory_failed_trajectory_returns_none():
    msgs = _make_messages(
        ["read_file", "write_file"],
        last_content="错误: 操作失败",
    )
    assert se.analyze_trajectory(msgs) is None


def test_analyze_trajectory_successful_returns_analysis():
    msgs = _make_messages(["read_file", "write_file"])
    result = se.analyze_trajectory(msgs)
    assert result is not None
    assert "pattern_name" in result
    assert "tools_sequence" in result
    assert len(result["tools_sequence"]) >= 2


def _make_messages_with_tool_results(
    tools: list[str],
    tool_results: list[str],
    last_content: str = "完成操作",
) -> list[dict]:
    msgs = [{"role": "user", "content": "do something with `file.py`"}]
    msgs.append(
        {
            "role": "assistant",
            "tool_calls": [{"function": {"name": t}} for t in tools],
            "content": "",
        },
    )
    for r in tool_results:
        msgs.append({"role": "tool", "content": r})
    msgs.append({"role": "assistant", "content": last_content})
    return msgs


def test_analyze_trajectory_last_tool_errored_returns_none():
    msgs = _make_messages_with_tool_results(
        ["read_file", "write_file"],
        ["file contents", "Error: disk full"],
    )
    assert se.analyze_trajectory(msgs) is None


def test_analyze_trajectory_recovered_after_early_error():
    # An early tool error followed by a successful final tool call is a
    # recovered workflow and stays skill-worthy.
    msgs = _make_messages_with_tool_results(
        ["read_file", "write_file"],
        ["Error: file locked", "wrote file"],
    )
    result = se.analyze_trajectory(msgs)
    assert result is not None
    assert result["pattern_name"] == "file-edit"


def test_analyze_trajectory_all_tools_ok():
    msgs = _make_messages_with_tool_results(
        ["read_file", "write_file"],
        ["file contents", "wrote file"],
    )
    assert se.analyze_trajectory(msgs) is not None


# ── _identify_pattern ─────────────────────────────────────────────────────


def test_identify_pattern_file_edit():
    assert se._identify_pattern(["read_file", "write_file"]) == "file-edit"


def test_identify_pattern_edit_and_test():
    assert (
        se._identify_pattern(["read_file", "write_file", "run_command"])
        == "edit-and-test"
    )


def test_identify_pattern_no_match():
    assert se._identify_pattern(["some_tool"]) is None


# ── _extract_parameters ───────────────────────────────────────────────────


def test_extract_parameters_finds_file_path():
    params = se._extract_parameters("please edit `main.py` now")
    names = [p["name"] for p in params]
    assert "command" in names or "file_path" in names


def test_extract_parameters_empty_request():
    params = se._extract_parameters("")
    assert isinstance(params, list)


# ── generate_skill_markdown ───────────────────────────────────────────────


def test_generate_skill_markdown_has_frontmatter():
    analysis = {
        "pattern_name": "file-edit",
        "tools_sequence": ["read_file", "write_file"],
        "rationale": "2-tool workflow",
        "parameters": [
            {
                "name": "file_path",
                "description": "目标文件",
                "example": "main.py",
            },
        ],
        "sample_request": "edit main.py",
    }
    md = se.generate_skill_markdown(analysis)
    assert "---" in md
    assert "file-edit" in md
    assert "read_file" in md


def test_generate_skill_markdown_no_params():
    analysis = {
        "pattern_name": "git-workflow",
        "tools_sequence": ["run_command"],
        "rationale": "git ops",
        "parameters": [],
        "sample_request": "commit changes",
    }
    md = se.generate_skill_markdown(analysis)
    assert "git-workflow" in md


def test_generate_skill_markdown_roundtrips_through_loader(tmp_path):
    """The generator must emit frontmatter the skill loader can read:
    single-line arguments list, and a description no nested block clobbers."""
    from dashscope_agenticx.skills.base import _parse_skill_md

    analysis = {
        "pattern_name": "file-edit",
        "tools_sequence": ["read_file", "write_file"],
        "rationale": "Successful 2-tool workflow: read_file → write_file",
        "parameters": [
            {
                "name": "file_path",
                "description": "Target file path",
                "example": "main.py",
            },
            {
                "name": "command",
                "description": "Command to execute",
                "example": "pytest",
            },
        ],
        "sample_request": "edit main.py",
    }
    path = tmp_path / "file-edit.md"
    path.write_text(se.generate_skill_markdown(analysis), encoding="utf-8")

    skill = _parse_skill_md(path)
    assert skill is not None
    assert skill.name == "file-edit"
    assert skill.arguments == ["file_path", "command"]
    assert skill.description == (
        "Automated workflow - Successful 2-tool workflow: "
        "read_file → write_file"
    )
    # Placeholders in the body make the declared arguments renderable.
    assert "{file_path}" in skill.prompt_template


# ── save_generated_skill ──────────────────────────────────────────────────


def test_save_generated_skill_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "WORKSPACE_DIR", tmp_path)
    analysis = {
        "pattern_name": "my-skill",
        "tools_sequence": ["read_file", "write_file"],
        "rationale": "test",
        "parameters": [],
        "sample_request": "do task",
    }
    path = se.save_generated_skill(analysis)
    assert path is not None
    assert path.exists()
    assert path.name == "my-skill.md"


def test_save_generated_skill_existing_returns_none(tmp_path, monkeypatch):
    monkeypatch.setattr(se, "WORKSPACE_DIR", tmp_path)
    (tmp_path / "skills").mkdir(parents=True)
    (tmp_path / "skills" / "existing.md").write_text("exists")
    analysis = {
        "pattern_name": "existing",
        "tools_sequence": ["read_file"],
        "rationale": "x",
        "parameters": [],
        "sample_request": "",
    }
    assert se.save_generated_skill(analysis) is None
