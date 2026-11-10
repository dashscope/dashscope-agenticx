# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.memory.tool_chains — query-gated chain injection."""

from dashscope_agenticx.memory.tool_chains import (
    ToolChainLibrary,
    get_fallback_hints,
    get_relevant_chains,
)


class TestRelevantChains:
    def test_empty_query_injects_nothing(self):
        assert get_relevant_chains("") == ""
        assert get_relevant_chains("   ") == ""

    def test_trigger_phrase_matches(self):
        section = get_relevant_chains("帮我修复登录报错")
        assert "Full flow for fixing a bug" in section

    def test_english_trigger_matches(self):
        section = get_relevant_chains("please refactor this module")
        assert "Full flow for refactoring code" in section

    def test_unrelated_query_injects_nothing(self):
        assert get_relevant_chains("今天天气怎么样") == ""

    def test_generic_file_word_does_not_match_everything(self):
        # "文件" appears in several chain descriptions but must not
        # single-handedly activate them.
        assert get_relevant_chains("这个文件在哪里") == ""

    def test_at_most_two_chains(self):
        # Triggers for bug_fix + code_refactor + file_edit all present.
        section = get_relevant_chains("修复并重构这个模块，顺便修改配置")
        assert section.count("###") <= 2

    def test_steps_only_no_examples(self):
        section = get_relevant_chains("帮我修复登录报错")
        assert "User:" not in section
        assert "1." in section  # steps present

    def test_keyword_overlap_requires_two_hits(self):
        # "search"+"context" both appear in bug_fix steps → overlap match.
        section = get_relevant_chains("search related context")
        assert "Full flow for fixing a bug" in section


class TestToolChainLibrary:
    def test_default_query_keeps_legacy_callers_quiet(self, tmp_path):
        lib = ToolChainLibrary(tmp_path)
        assert lib.get_relevant_chains() == ""

    def test_library_delegates_query(self, tmp_path):
        lib = ToolChainLibrary(tmp_path)
        assert "Full flow for refactoring code" in lib.get_relevant_chains(
            "重构一下",
        )

    def test_fallback_hints_delegate(self, tmp_path):
        lib = ToolChainLibrary(tmp_path)
        assert lib.get_fallback_hints("read_file") == get_fallback_hints(
            "read_file",
        )
