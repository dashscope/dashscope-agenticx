# -*- coding: utf-8 -*-
"""Live status line in the TUI spinner: elapsed time, token counts, percentage."""

from __future__ import annotations

from dashscope_agenticx.ui.tui import Spinner, estimate_tokens, fmt_chars


def test_fmt_chars_small():
    assert fmt_chars(0) == "0"
    assert fmt_chars(320) == "320"
    assert fmt_chars(999) == "999"


def test_fmt_chars_thousands():
    assert fmt_chars(1000) == "1.0k"
    assert fmt_chars(15230) == "15.2k"


def test_estimate_tokens_empty():
    assert estimate_tokens("") == 0


def test_estimate_tokens_ascii():
    assert estimate_tokens("hello world") == 3  # 11 chars -> ceil(11/4)


def test_estimate_tokens_cjk():
    assert estimate_tokens("你好世界") == 4


def test_estimate_tokens_mixed():
    # 2 CJK (1 each) + 7 ascii (ceil(7/4)=2)
    assert estimate_tokens("你好 world") == 4


class TestSpinnerStatsSuffix:
    def test_empty_when_not_started(self):
        assert Spinner().stats_suffix() == ""

    def test_elapsed_and_tokens(self, monkeypatch):
        clock = [100.0]
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        sp = Spinner()
        sp.start()
        clock[0] = 112.3
        sp.set_input_tokens(1024)
        sp.note_output("你好 world")  # -> 4 est. tokens

        suffix = sp.stats_suffix()

        assert "12.3s" in suffix
        assert "↑1024" in suffix
        assert "↓~4" in suffix
        assert "%" not in suffix  # no budget -> no percentage

    def test_percentage_with_budget(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        sp = Spinner()
        sp.max_out_tokens = 200
        sp.start()
        sp.note_output("你" * 50)  # 50 est. tokens -> 25%

        assert "↓~50 25%" in sp.stats_suffix()

    def test_percentage_capped_at_99(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        sp = Spinner()
        sp.max_out_tokens = 100
        sp.start()
        sp.note_output("你" * 500)  # way over budget

        assert "99%" in sp.stats_suffix()

    def test_start_resets_counters(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        sp = Spinner()
        sp.start()
        sp.note_output("hello")
        sp.set_input_tokens(10)
        sp.set_tool_stats(3, 2)

        sp.start()

        suffix = sp.stats_suffix()
        assert "↑" not in suffix
        assert "↓" not in suffix
        assert "tools" not in suffix
        assert "sub-agents" not in suffix

    def test_tool_and_subagent_counts(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        sp = Spinner()
        sp.start()
        sp.set_tool_stats(3, 2)

        suffix = sp.stats_suffix()

        assert "3 tools" in suffix
        assert "2 sub-agents" in suffix

    def test_api_mcp_skill_counts(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        sp = Spinner()
        sp.start()
        sp.set_api_calls(4)
        sp.set_tool_stats(5, 0, mcp=2, skills=1)

        suffix = sp.stats_suffix()

        assert "4 api" in suffix
        assert "5 tools (2 mcp)" in suffix
        assert "1 skills" in suffix
        assert "sub-agents" not in suffix

    def test_tool_counts_hidden_when_zero(self, monkeypatch):
        clock = [0.0]
        monkeypatch.setattr("time.monotonic", lambda: clock[0])
        sp = Spinner()
        sp.start()

        suffix = sp.stats_suffix()

        assert "tools" not in suffix
        assert "sub-agents" not in suffix
