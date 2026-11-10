# -*- coding: utf-8 -*-
"""Tests for memory/directives_learning.py — pattern recording, analysis, proposal lifecycle."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest

import dashscope_agenticx.memory.directives_learning as dl


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    monkeypatch.setattr(dl, "WORKSPACE_DIR", tmp_path)


# ── record_tool_sequence ───────────────────────────────────────────────────


def test_record_tool_sequence_single_tool_ignored():
    dl.record_tool_sequence(["read_file"])
    patterns = dl._load_patterns()
    assert len(patterns["sequences"]) == 0


def test_record_tool_sequence_two_tools_recorded():
    dl.record_tool_sequence(["read_file", "write_file"])
    patterns = dl._load_patterns()
    assert len(patterns["sequences"]) == 1


def test_record_tool_sequence_truncates_at_100():
    for i in range(105):
        dl.record_tool_sequence([f"tool_{i}", f"tool_{i+1}"])
    patterns = dl._load_patterns()
    assert len(patterns["sequences"]) == 100


# ── analyze_patterns ──────────────────────────────────────────────────────


def test_analyze_patterns_insufficient_data():
    for i in range(4):
        dl.record_tool_sequence(["read_file", "write_file"])
    result = dl.analyze_patterns()
    assert result == []


def test_analyze_patterns_detects_frequent_pair():
    for _ in range(6):
        dl.record_tool_sequence(["read_file", "write_file"])
        dl.record_tool_sequence(["read_file", "other"])
    proposals = dl.analyze_patterns()
    tools_lists = [p["tools"] for p in proposals]
    assert ["read_file", "write_file"] in tools_lists


def test_analyze_patterns_returns_confidence_and_frequency():
    for _ in range(5):
        dl.record_tool_sequence(["run_command", "write_file"])
    props = dl.analyze_patterns()
    if props:
        p = props[0]
        assert "confidence" in p
        assert "frequency" in p
        assert p["frequency"] >= 3


def test_recency_weight_fresh_and_half_life():
    now = datetime.now(timezone.utc)
    fresh = now.isoformat()
    aged = (now - timedelta(days=dl._DECAY_HALF_LIFE_DAYS)).isoformat()
    assert dl._recency_weight(fresh) == pytest.approx(1.0, abs=1e-6)
    assert dl._recency_weight(aged) == pytest.approx(0.5, abs=1e-6)
    # Missing / malformed timestamps degrade to no decay.
    assert dl._recency_weight("") == 1.0
    assert dl._recency_weight("not-a-date") == 1.0


def _write_sequences(pairs_with_ts: list[tuple[list[str], str]]) -> None:
    patterns = {
        "sequences": [
            {"tools": tools, "timestamp": ts} for tools, ts in pairs_with_ts
        ],
        "preferences": {},
        "proposed_directives": [],
    }
    dl._save_patterns(patterns)


def test_analyze_patterns_stale_repetitions_decay_below_threshold():
    now = datetime.now(timezone.utc)
    stale = (now - timedelta(days=90)).isoformat()
    # 5 stale repetitions of the same pair: raw count >= 3 but the
    # recency-weighted score decays far below the threshold.
    _write_sequences([(["read_file", "write_file"], stale)] * 5)
    assert dl.analyze_patterns() == []


def test_analyze_patterns_recent_repetitions_propose():
    now = datetime.now(timezone.utc).isoformat()
    _write_sequences([(["read_file", "write_file"], now)] * 5)
    props = dl.analyze_patterns()
    assert props and props[0]["tools"] == ["read_file", "write_file"]
    assert props[0]["frequency"] == 5


# ── propose_directive ──────────────────────────────────────────────────────


def test_propose_directive_creates_entry():
    p = dl.propose_directive("always run tests after edit", "observed 5x")
    assert p["directive"] == "always run tests after edit"
    assert p["status"] == "pending"
    assert p["id"].startswith("dir_")


def test_proposals_summary_has_no_console_markup():
    # The summary is injected into the LLM system prompt — Rich markup
    # would leak as literal noise.
    dl.propose_directive("rule", "rationale")
    summary = dl.get_directive_proposals_summary()
    assert summary
    assert "[dim]" not in summary
    assert "[/dim]" not in summary


def test_propose_directive_id_is_utc():
    p = dl.propose_directive("rule", "rationale")
    parts = p["id"].split("_")
    assert parts[1].isdigit()


# ── list_proposed_directives ───────────────────────────────────────────────


def test_list_proposed_directives_filter_pending():
    dl.propose_directive("rule1", "r1")
    dl.propose_directive("rule2", "r2")
    pending = dl.list_proposed_directives("pending")
    assert len(pending) == 2


def test_list_proposed_directives_all():
    p = dl.propose_directive("rule", "r")
    dl.reject_directive(p["id"])
    dl.propose_directive("rule2", "r2")
    all_d = dl.list_proposed_directives("all")
    assert len(all_d) == 2


# ── reject_directive ──────────────────────────────────────────────────────


def test_reject_directive_success():
    p = dl.propose_directive("rule", "reason")
    result = dl.reject_directive(p["id"])
    assert result is True
    pending = dl.list_proposed_directives("pending")
    assert len(pending) == 0


def test_reject_directive_nonexistent():
    assert dl.reject_directive("dir_99999") is False


# ── accept_directive ──────────────────────────────────────────────────────


def test_accept_directive_updates_status():
    p = dl.propose_directive("test rule", "because")

    class FakeConfig:
        user_directives: list = []

        def save_workspace(self):
            pass

    cfg = FakeConfig()
    result = dl.accept_directive(p["id"], cfg)
    assert result is True
    assert "test rule" in cfg.user_directives
    accepted = [
        d
        for d in dl.list_proposed_directives("all")
        if d["status"] == "accepted"
    ]
    assert len(accepted) == 1


def test_accept_directive_nonexistent():
    class FakeConfig:
        user_directives: list = []

        def save_workspace(self):
            pass

    assert dl.accept_directive("dir_99999", FakeConfig()) is False
