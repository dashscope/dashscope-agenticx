# -*- coding: utf-8 -*-
"""Edge-case tests for the context layer: compression, session, prompt
discovery, local history/memory stores.

Multi-angle: boundary values, malformed on-disk data, path-safety attacks,
state-machine transitions.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from dashscope_agenticx import compression
from dashscope_agenticx.compression import (
    compress_messages,
    estimate_tokens,
    preserve_recent_messages,
    safety_compress_if_needed,
    should_compress,
)
from dashscope_agenticx.prompt import discover_project_instructions
from dashscope_agenticx.session import SessionManager


def _chat_stub(text="摘要"):
    async def _chat(messages, tools):
        return SimpleNamespace(content=text)

    return _chat


def _failing_chat():
    async def _chat(messages, tools):
        raise RuntimeError("llm down")

    return _chat


# ===========================================================================
# compression
# ===========================================================================


class TestEstimateTokens:
    def test_empty(self):
        assert estimate_tokens("") == 0

    def test_ascii(self):
        assert estimate_tokens("a" * 400) == 100

    def test_cjk_one_per_char(self):
        assert estimate_tokens("汉" * 50) == 50

    def test_mixed(self):
        n = estimate_tokens("hello 世界")
        assert 3 <= n <= 8

    def test_emoji_other_bucket(self):
        # emoji is neither ascii nor CJK -> ~1 token per 2 chars
        assert estimate_tokens("😀" * 10) == 5


class TestShouldCompress:
    def test_too_few_messages(self):
        msgs = [{"role": "user", "content": "x" * 100000}] * 3
        should, reason = should_compress(msgs, context_window=1000)
        assert should is False and reason == ""

    def test_soft_threshold(self):
        msgs = [{"role": "user", "content": "x" * 1000}] * 4  # ~1016 tokens
        should, reason = should_compress(msgs, context_window=2000)
        assert should is True and "soft" in reason

    def test_hard_threshold(self):
        msgs = [{"role": "user", "content": "x" * 2000}] * 4
        should, reason = should_compress(msgs, context_window=2000)
        assert should is True and "hard" in reason

    def test_extra_tokens_tip_over(self):
        msgs = [{"role": "user", "content": "x" * 100}] * 4
        should, _ = should_compress(
            msgs,
            context_window=1000,
            extra_tokens=900,
        )
        assert should is True


class TestPreserveBoundary:
    def test_small_list_untouched(self):
        msgs = [{"role": "user", "content": "a"}] * 3
        older, recent = preserve_recent_messages(msgs, keep=4)
        assert older == [] and recent == msgs

    def test_tail_not_starting_with_tool_result(self):
        msgs = [
            {"role": "user", "content": "q"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "f"}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": "r"},
            {"role": "user", "content": "u2"},
            {"role": "assistant", "content": "a2"},
        ]
        older, recent = preserve_recent_messages(msgs, keep=2)
        assert recent[0]["role"] != "tool"
        # the orphaned assistant+tool pair must stay in `older`
        assert all(m.get("role") != "tool" for m in recent[:1])

    def test_dangling_tool_calls_advance(self):
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [{"id": "1", "function": {"name": "f"}}],
            },
            {"role": "tool", "tool_call_id": "1", "content": "r"},
            {"role": "user", "content": "u2"},
        ]
        older, recent = preserve_recent_messages(msgs, keep=3)
        # tail must not start with an assistant whose tool_calls are unanswered
        first = recent[0]
        if first.get("role") == "assistant" and first.get("tool_calls"):
            ids = {
                m.get("tool_call_id")
                for m in recent[1:]
                if m.get("role") == "tool"
            }
            assert all(tc["id"] in ids for tc in first["tool_calls"])


class TestCompressMessages:
    async def test_not_needed_returns_none(self):
        msgs = [{"role": "user", "content": "short"}] * 4
        assert (
            await compress_messages(msgs, _chat_stub(), context_window=10**9)
            is None
        )

    async def test_summary_replaces_older(self):
        msgs = [
            {"role": "user", "content": f"msg{i} " + "x" * 800}
            for i in range(8)
        ]
        out = await compress_messages(
            msgs,
            _chat_stub("总结"),
            context_window=1000,
        )
        assert out is not None
        assert "总结" in out[0]["content"]
        assert len(out) < len(msgs)

    async def test_summarizer_failure_omission_marker(self):
        msgs = [
            {"role": "user", "content": f"m{i} " + "x" * 800} for i in range(8)
        ]
        out = await compress_messages(
            msgs,
            _failing_chat(),
            context_window=1000,
        )
        assert out is not None
        assert "Omitted" in out[0]["content"]

    async def test_safety_compress_below_threshold(self):
        msgs = [{"role": "user", "content": "x"}] * 6
        assert (
            await safety_compress_if_needed(
                msgs,
                _chat_stub(),
                context_window=10**9,
            )
            is None
        )

    async def test_safety_compress_above_threshold(self):
        msgs = [{"role": "user", "content": "x" * 2000}] * 6
        out = await safety_compress_if_needed(
            msgs,
            _chat_stub(),
            context_window=2000,
        )
        assert out is not None

    def test_dump_caps_long_tool_output(self):
        msgs = [{"role": "tool", "content": "y" * 5000}]
        dump = compression._prepare_compress_dump(msgs)
        assert len(dump) < 3000


# ===========================================================================
# session
# ===========================================================================


@pytest.fixture
def sm(tmp_path):
    return SessionManager(tmp_path / "ws")


class TestSessionSafety:
    @pytest.mark.parametrize("bad", ["../x", "a/b", "a\\b", "..", "", "/abs"])
    def test_unsafe_topic_names_rejected(self, sm, bad):
        assert sm.create_topic(bad) is False
        assert sm.set_current_topic(bad) is False
        assert sm.delete_topic(bad) is False

    def test_rename_to_existing_fails(self, sm):
        sm.create_topic("a")
        sm.create_topic("b")
        assert sm.rename_topic("a", "b") is False

    def test_rename_missing_fails(self, sm):
        assert sm.rename_topic("ghost", "x") is False

    def test_rename_updates_current(self, sm):
        sm.create_topic("old")
        assert sm.rename_topic("old", "new") is True
        assert sm.get_current_topic() == "new"
        meta = json.loads((sm.session_dir / "new" / "meta.json").read_text())
        assert meta["topic"] == "new"

    def test_delete_default_refused(self, sm):
        assert sm.delete_topic("default") is False

    def test_delete_current_falls_back_to_default(self, sm):
        sm.create_topic("work")
        assert sm.delete_topic("work") is True
        assert sm.get_current_topic() == "default"

    def test_list_topics_corrupt_meta_fallback(self, sm):
        d = sm.session_dir / "broken"
        d.mkdir()
        (d / "meta.json").write_text("{not json", encoding="utf-8")
        topics = {t.topic for t in sm.list_topics()}
        assert "broken" in topics

    def test_list_topics_missing_meta_uses_mtime(self, sm):
        d = sm.session_dir / "nometa"
        d.mkdir()
        topics = {t.topic: t for t in sm.list_topics()}
        assert "nometa" in topics
        assert topics["nometa"].created  # derived, non-empty

    def test_update_message_count_corrupt_meta_no_crash(self, sm):
        d = sm.session_dir / "default"
        (d / "meta.json").write_text("{bad", encoding="utf-8")
        sm.update_message_count(5)  # must not raise

    def test_double_create_fails(self, sm):
        assert sm.create_topic("x") is True
        assert sm.create_topic("x") is False


# ===========================================================================
# prompt discovery
# ===========================================================================


class TestPromptDiscovery:
    def test_rules_jsonl_malformed_lines_skipped(self, tmp_path):
        rules = tmp_path / ".agenticx"
        rules.mkdir()
        (rules / "rules.jsonl").write_text(
            "\n".join(
                [
                    "{bad json",
                    json.dumps(["not", "object"]),
                    json.dumps({"enabled": False, "text": "关闭的规则"}),
                    json.dumps({"text": "  "}),
                    json.dumps({"text": "生效规则"}),
                ],
            ),
            encoding="utf-8",
        )
        out = discover_project_instructions(tmp_path)
        assert out == "- 生效规则"

    def test_rules_jsonl_priority_over_claude(self, tmp_path):
        (tmp_path / ".agenticx").mkdir()
        (tmp_path / ".agenticx" / "rules.jsonl").write_text(
            json.dumps({"text": "规则优先"}),
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text("claude 内容", encoding="utf-8")
        assert "规则优先" in discover_project_instructions(tmp_path)

    def test_empty_rules_falls_through(self, tmp_path):
        (tmp_path / ".agenticx").mkdir()
        (tmp_path / ".agenticx" / "rules.jsonl").write_text(
            json.dumps({"enabled": False, "text": "x"}),
            encoding="utf-8",
        )
        (tmp_path / "CLAUDE.md").write_text("claude 内容", encoding="utf-8")
        assert discover_project_instructions(tmp_path) == "claude 内容"

    def test_upward_search(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text("根级指令", encoding="utf-8")
        nested = tmp_path / "a" / "b"
        nested.mkdir(parents=True)
        assert discover_project_instructions(nested) == "根级指令"

    def test_frontmatter_stripped(self, tmp_path):
        (tmp_path / "CLAUDE.md").write_text(
            "---\nkey: value\n---\n正文内容",
            encoding="utf-8",
        )
        out = discover_project_instructions(tmp_path)
        assert "key: value" not in out
        assert "正文内容" in out

    def test_huge_file_truncated(self, tmp_path):
        big = "头" * 4000 + "中" * 8000 + "尾" * 4000
        (tmp_path / "CLAUDE.md").write_text(big, encoding="utf-8")
        out = discover_project_instructions(tmp_path)
        assert len(out) <= 8200  # 8000 cap + truncation marker
        assert "中" * 4000 not in out

    def test_nothing_found_returns_none(self, tmp_path):
        assert discover_project_instructions(tmp_path) is None


# ===========================================================================
# local history / memory stores
# ===========================================================================


@pytest.fixture
def history_ws(tmp_path, monkeypatch):
    from dashscope_agenticx.platforms.local import history

    ws = tmp_path / "ws"
    ws.mkdir()
    monkeypatch.setattr(history, "WORKSPACE_DIR", ws)
    return history


class TestHistoryEdge:
    def test_list_limit_zero_returns_empty(self, history_ws):
        history_ws._save(
            [{"id": "1", "summary": "s", "created_at": "", "turns": 1}],
        )
        assert history_ws.list_history(limit=0) == []
        assert history_ws.list_history(limit=-3) == []

    def test_corrupt_store_loads_empty(self, history_ws):
        path = history_ws._history_file()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{corrupt", encoding="utf-8")
        assert history_ws.list_history() == []
        assert history_ws.stats()["count"] == 0

    def test_store_without_user_turns_returns_none(self, history_ws):
        out = history_ws.store_history(
            [{"role": "assistant", "content": "solo"}],
        )
        assert out is None

    def test_search_blank_query(self, history_ws):
        history_ws._save(
            [{"id": "1", "summary": "写脚本", "created_at": "", "turns": 1}],
        )
        assert history_ws.search_history("   ") == []


class TestLocalMemoryEdge:
    async def test_search_min_score_filters(self, tmp_path, monkeypatch):
        from dashscope_agenticx.platforms.local import memory as mem_mod

        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path,
        )
        client = mem_mod.LocalMemoryClient(user_id="u")
        await client.add([], custom_content="我喜欢 Python 异步编程")
        hits = await client.search("完全无关的内容 zzz", min_score=0.9)
        assert hits == []

    async def test_pagination(self, tmp_path, monkeypatch):
        from dashscope_agenticx.platforms.local import memory as mem_mod

        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path,
        )
        client = mem_mod.LocalMemoryClient(user_id="u")
        for i in range(15):
            await client.add([], custom_content=f"我的偏好第{i}条")
        page1 = await client.list(page_num=1, page_size=10)
        page2 = await client.list(page_num=2, page_size=10)
        assert len(page1) == 10
        assert len(page2) == 5
        assert {n.id for n in page1}.isdisjoint({n.id for n in page2})

    async def test_update_delete_missing_id(self, tmp_path, monkeypatch):
        from dashscope_agenticx.platforms.local import memory as mem_mod

        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path,
        )
        client = mem_mod.LocalMemoryClient(user_id="u")
        assert await client.update("ghost", "x") is False
        assert await client.delete("ghost") is False

    async def test_duplicate_content_not_added(self, tmp_path, monkeypatch):
        from dashscope_agenticx.platforms.local import memory as mem_mod

        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path,
        )
        client = mem_mod.LocalMemoryClient(user_id="u")
        await client.add([], custom_content="我用 vim")
        again = await client.add([], custom_content="我用 vim")
        assert again == []
        assert len(await client.list()) == 1


# ===========================================================================
# trace report / history mutations
# ===========================================================================


class TestTraceReport:
    def _logger(self, tmp_path):
        from dashscope_agenticx.memory.trace import TraceLogger

        return TraceLogger(tmp_path)

    def test_missing_file_returns_none(self, tmp_path):
        from dashscope_agenticx.memory.trace import generate_report

        logger = self._logger(tmp_path)
        assert generate_report(logger) is None

    def test_none_logger_returns_none(self):
        from dashscope_agenticx.memory.trace import generate_report

        assert generate_report(None) is None

    def test_aggregation(self, tmp_path):
        from dashscope_agenticx.memory.trace import generate_report

        logger = self._logger(tmp_path)
        logger.log_llm_call(
            [{"role": "user", "content": "x"}],
            [],
            {"content": "y"},
        )
        logger.log_tool_execution("read_file", {}, "ok", 100, True)
        logger.log_tool_execution("write_file", {}, "ok", 200, False)
        logger.log_tool_execution("read_file", {}, "ok", 50, True)
        report = generate_report(logger)
        assert report["total_llm_calls"] == 1
        assert report["total_tool_calls"] == 3
        assert abs(report["tool_success_rate"] - 2 / 3) < 0.01
        assert report["top_tools"][0] == ("read_file", 2)
        assert report["avg_response_time"] > 0

    def test_malformed_lines_skipped(self, tmp_path):
        from dashscope_agenticx.memory.trace import generate_report

        logger = self._logger(tmp_path)
        logger.log_tool_execution("f", {}, "r", 10, True)
        with open(logger.trace_file, "a", encoding="utf-8") as fh:
            fh.write("{broken\n\n")
        report = generate_report(logger)
        assert report["total_tool_calls"] == 1

    def test_zero_events_returns_none(self, tmp_path):
        from dashscope_agenticx.memory.trace import generate_report

        logger = self._logger(tmp_path)
        logger.log_decision("plan", {"x": 1})
        assert generate_report(logger) is None


class TestHistoryMutations:
    def test_search_ranks_relevance(self, history_ws):
        """Entry covering all query keywords outranks partial coverage."""
        history_ws._save(
            [
                {
                    "id": "1",
                    "summary": "登录页面重构",
                    "created_at": "",
                    "turns": 1,
                },
                {
                    "id": "2",
                    "summary": "修复登录 bug",
                    "created_at": "",
                    "turns": 1,
                },
            ],
        )
        hits = history_ws.search_history("登录 bug")
        assert [h["id"] for h in hits] == ["2", "1"]

    def test_delete_existing_and_missing(self, history_ws):
        history_ws._save(
            [{"id": "1", "summary": "s", "created_at": "", "turns": 1}],
        )
        assert history_ws.delete_history("1") is True
        assert history_ws.delete_history("1") is False

    def test_clear_returns_count(self, history_ws):
        history_ws._save(
            [
                {"id": str(i), "summary": "s", "created_at": "", "turns": 1}
                for i in range(3)
            ],
        )
        assert history_ws.clear_history() == 3
        assert history_ws.clear_history() == 0
