# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.skills.base — skill parsing, rendering, summary."""

import pytest

from dashscope_agenticx.skills.base import (
    BUILTIN_SKILLS,
    KNOWN_MCP_SERVICES,
    Skill,
    _parse_simple_yaml,
    _parse_skill_md,
    list_known_services,
    list_skills,
    load_skill_files,
    register,
    register_skill_file,
    render_skill,
    skills_summary_for_llm,
)


class TestParseSimpleYaml:
    def test_key_value(self):
        result = _parse_simple_yaml("name: test")
        assert result["name"] == "test"

    def test_quoted_value(self):
        result = _parse_simple_yaml('description: "hello world"')
        assert result["description"] == "hello world"

    def test_single_quoted_value(self):
        result = _parse_simple_yaml("desc: 'val'")
        assert result["desc"] == "val"

    def test_list_value(self):
        result = _parse_simple_yaml("args: [a, b, c]")
        assert result["args"] == ["a", "b", "c"]

    def test_comment_skipped(self):
        result = _parse_simple_yaml("# comment\nname: test")
        assert "name" in result
        assert "# comment" not in result

    def test_empty_line_skipped(self):
        result = _parse_simple_yaml("\nname: test\n")
        assert result["name"] == "test"


class TestParseSkillMd:
    def test_basic(self, tmp_path):
        f = tmp_path / "my_skill.md"
        f.write_text(
            "---\n"
            "name: my_skill\n"
            "description: does stuff\n"
            "arguments: [arg1, arg2]\n"
            "---\n"
            "Do something with {arg1} and {arg2}",
            encoding="utf-8",
        )
        skill = _parse_skill_md(f)
        assert skill is not None
        assert skill.name == "my_skill"
        assert skill.description == "does stuff"
        assert skill.arguments == ["arg1", "arg2"]
        assert "{arg1}" in skill.prompt_template

    def test_name_defaults_to_stem(self, tmp_path):
        f = tmp_path / "auto_name.md"
        f.write_text(
            "---\ndescription: no name field\n---\nbody",
            encoding="utf-8",
        )
        skill = _parse_skill_md(f)
        assert skill.name == "auto_name"

    def test_no_frontmatter(self, tmp_path):
        f = tmp_path / "plain.md"
        f.write_text("just text", encoding="utf-8")
        assert _parse_skill_md(f) is None

    def test_empty_body(self, tmp_path):
        f = tmp_path / "empty.md"
        f.write_text("---\nname: x\n---\n", encoding="utf-8")
        assert _parse_skill_md(f) is None

    def test_arguments_as_json_string(self, tmp_path):
        f = tmp_path / "json_args.md"
        f.write_text(
            '---\nname: s\narguments: ["city", "day"]\n---\nbody',
            encoding="utf-8",
        )
        skill = _parse_skill_md(f)
        assert skill.arguments == ["city", "day"]

    def test_arguments_as_comma_string(self, tmp_path):
        f = tmp_path / "comma_args.md"
        f.write_text(
            "---\nname: s\narguments: a, b, c\n---\nbody",
            encoding="utf-8",
        )
        skill = _parse_skill_md(f)
        assert skill.arguments == ["a", "b", "c"]

    def test_mcp_service(self, tmp_path):
        f = tmp_path / "mcp_skill.md"
        f.write_text(
            "---\nname: s\nmcp_service: time\n---\nbody",
            encoding="utf-8",
        )
        skill = _parse_skill_md(f)
        assert skill.mcp_service == "time"


class TestRenderSkill:
    def test_basic_render(self):
        skill = Skill(
            name="greet",
            description="greet someone",
            mcp_service="",
            prompt_template="Hello {name}!",
            arguments=["name"],
        )
        result = render_skill(skill, ["world"])
        assert result == "Hello world!"

    def test_multi_arg(self):
        skill = Skill(
            name="t",
            description="",
            mcp_service="",
            prompt_template="{a} and {b}",
            arguments=["a", "b"],
        )
        assert render_skill(skill, ["x", "y"]) == "x and y"

    def test_last_arg_joins(self):
        skill = Skill(
            name="t",
            description="",
            mcp_service="",
            prompt_template="{prefix} {rest}",
            arguments=["prefix", "rest"],
        )
        result = render_skill(skill, ["hello", "world", "of", "code"])
        assert result == "hello world of code"

    def test_too_few_args(self):
        skill = Skill(
            name="t",
            description="",
            mcp_service="",
            prompt_template="{a} {b}",
            arguments=["a", "b"],
        )
        assert render_skill(skill, ["only_one"]) is None

    def test_undeclared_placeholder_is_left_literal(self):
        """An authoring mistake must not be reported as missing arguments."""
        skill = Skill(
            name="t",
            description="",
            mcp_service="",
            prompt_template="{missing}",
            arguments=["a"],
        )
        assert render_skill(skill, ["val"]) == "{missing}"

    def test_json_in_the_body_is_not_a_placeholder(self):
        """The shape that broke every bundled skill documenting a payload."""
        body = (
            '| messages | `[{"role":..., "content":...}]` |\n'
            '| response_format | `{"type": "json_object"}` |'
        )
        skill = Skill(
            name="text-generation",
            description="",
            mcp_service="",
            prompt_template=body,
            arguments=[],
        )
        assert render_skill(skill, []) == body

    def test_empty_braces_do_not_raise(self):
        """`{}` raised IndexError straight past the old KeyError guard."""
        skill = Skill(
            name="fine-tuning",
            description="",
            mcp_service="",
            prompt_template="pass params={} to the call",
            arguments=[],
        )
        assert render_skill(skill, []) == "pass params={} to the call"

    def test_declared_arg_is_substituted_inside_json(self):
        skill = Skill(
            name="t",
            description="",
            mcp_service="",
            prompt_template='{"model": "{model}"}',
            arguments=["model"],
        )
        assert render_skill(skill, ["qwen-plus"]) == '{"model": "qwen-plus"}'


class TestSkillsSummaryForLlm:
    def test_empty(self):
        original = dict(BUILTIN_SKILLS)
        BUILTIN_SKILLS.clear()
        try:
            assert skills_summary_for_llm() == ""
        finally:
            BUILTIN_SKILLS.update(original)

    def test_with_skills(self):
        original = dict(BUILTIN_SKILLS)
        BUILTIN_SKILLS.clear()
        try:
            register(Skill("greet", "greet", "", "Hello {name}", ["name"]))
            register(Skill("clock", "time", "time", "What time", []))
            text = skills_summary_for_llm()
            assert "greet" in text
            assert "clock" in text
            assert "MCP:time" in text
            assert "needs mcp_connect" in text
        finally:
            BUILTIN_SKILLS.clear()
            BUILTIN_SKILLS.update(original)


class TestKnownMcpServices:
    def test_has_expected_services(self):
        assert "time" in KNOWN_MCP_SERVICES
        assert "code-interpreter" in KNOWN_MCP_SERVICES
        assert "doc-analysis" in KNOWN_MCP_SERVICES

    def test_no_removed_services(self):
        assert "amap-maps" not in KNOWN_MCP_SERVICES
        assert "quark-search" not in KNOWN_MCP_SERVICES


class TestListKnownServices:
    def test_format(self):
        text = list_known_services()
        assert "time" in text
        assert "code-interpreter" in text
        assert "/mcp add" in text


def _write_skill_md(path, name="rt_skill"):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "---\n"
        f"name: {name}\n"
        "description: runtime registered\n"
        "arguments: [target]\n"
        "---\n"
        "Do the thing with {target}",
        encoding="utf-8",
    )


class TestRuntimeRegistration:
    """M1-3: runtime registration API and deletion propagation."""

    @pytest.fixture(autouse=True)
    def isolate(self, tmp_path, monkeypatch):
        import dashscope_agenticx.skills.base as base

        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path / "ws",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_DIR",
            tmp_path / "cfg",
        )
        original_skills = dict(base.BUILTIN_SKILLS)
        original_file_names = set(base._FILE_SKILL_NAMES)
        base.BUILTIN_SKILLS.clear()
        base._FILE_SKILL_NAMES.clear()
        yield tmp_path
        base.BUILTIN_SKILLS.clear()
        base.BUILTIN_SKILLS.update(original_skills)
        base._FILE_SKILL_NAMES.clear()
        base._FILE_SKILL_NAMES.update(original_file_names)

    def test_register_skill_file_copies_and_registers(self, isolate):
        src = isolate / "incoming" / "rt_skill.md"
        _write_skill_md(src)

        assert register_skill_file(src) is True
        assert "rt_skill" in BUILTIN_SKILLS
        assert (isolate / "ws" / "skills" / "rt_skill.md").is_file()
        assert "rt_skill" in skills_summary_for_llm()

    def test_register_skill_file_already_in_place(self, isolate):
        target = isolate / "ws" / "skills" / "rt_skill.md"
        _write_skill_md(target)

        assert register_skill_file(target) is True
        assert "rt_skill" in BUILTIN_SKILLS

    def test_register_skill_file_unparseable_returns_false(self, isolate):
        bad = isolate / "bad.md"
        bad.write_text("no frontmatter here", encoding="utf-8")

        assert register_skill_file(bad) is False
        assert not BUILTIN_SKILLS

    def test_load_skill_files_deletion_propagates(self, isolate):
        target = isolate / "ws" / "skills" / "gone.md"
        _write_skill_md(target, name="gone")

        load_skill_files()
        assert "gone" in BUILTIN_SKILLS

        target.unlink()
        load_skill_files()
        assert "gone" not in BUILTIN_SKILLS

    def test_rescan_preserves_programmatic_registrations(self, isolate):
        register(Skill("toml_one", "from toml", "", "body", []))
        target = isolate / "ws" / "skills" / "file_one.md"
        _write_skill_md(target, name="file_one")

        load_skill_files()
        assert {"toml_one", "file_one"} <= set(BUILTIN_SKILLS)

        target.unlink()
        load_skill_files()
        assert "file_one" not in BUILTIN_SKILLS
        assert "toml_one" in BUILTIN_SKILLS

    def test_registered_skill_appears_in_system_prompt(self, isolate):
        """M1-3 acceptance: a runtime-registered skill shows up in the same
        session's prompt without a restart."""
        from unittest.mock import MagicMock

        from dashscope_agenticx.agent import Agent
        from dashscope_agenticx.memory.manager import MemoryManager

        src = isolate / "ws" / "skills" / "rt_skill.md"
        _write_skill_md(src)
        assert register_skill_file(src) is True

        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            memory_manager=MemoryManager(isolate / "agent_ws"),
            system_prompt="base prompt",
            session_path=isolate / ".agenticx" / "session" / "history.json",
        )
        prompt = agent._system_prompt_for_turn("hello")
        assert "rt_skill" in prompt
