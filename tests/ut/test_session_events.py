# -*- coding: utf-8 -*-
"""Tests for session_events.py and the SessionManager event sidecar.

Covers the append-only SessionEventLog (schema, seq, filtering, tail,
malformed-line tolerance, best-effort writes) and the lifecycle events
wired into SessionManager (create/switch/rename).
"""

from __future__ import annotations

import json

import pytest

from dashscope_agenticx.session import SessionManager
from dashscope_agenticx.session_events import SCHEMA_VERSION, SessionEventLog


@pytest.fixture()
def log(tmp_path):
    """A SessionEventLog writing into a temp events.jsonl."""
    return SessionEventLog(tmp_path / "events.jsonl")


@pytest.fixture()
def sm(tmp_path):
    """A fresh SessionManager backed by tmp_path."""
    return SessionManager(workspace_dir=tmp_path)


# ── SessionEventLog core ───────────────────────────────────────────────


def test_append_writes_schema_fields(log):
    log.append("topic/created", {"topic": "work"})
    events = log.read()
    assert len(events) == 1
    e = events[0]
    assert e["v"] == SCHEMA_VERSION
    assert e["seq"] == 1
    assert e["type"] == "topic/created"
    assert e["data"] == {"topic": "work"}
    assert e["ts"]  # non-empty ISO timestamp


def test_event_log_redacts_secrets_before_persisting(log):
    secret = "sk-abcdefghijklmnopqrstuvwxyz123456"
    log.append("messages/snapshot", {"messages": [{"content": secret}]})
    raw = log.path.read_text(encoding="utf-8")
    assert secret not in raw
    assert "[REDACTED]" in raw


def test_event_log_rejects_unsafe_topic_without_writing(sm):
    escaped = sm.workspace_dir / "escaped" / "events.jsonl"
    with pytest.raises(ValueError, match="unsafe session topic"):
        sm.event_log("../escaped")
    sm.record_messages_snapshot(
        [{"role": "user", "content": "x"}],
        "../escaped",
    )
    assert not escaped.exists()


def test_seq_increments_monotonically(log):
    for i in range(3):
        log.append("turn/end", {"i": i})
    seqs = [e["seq"] for e in log.read()]
    assert seqs == [1, 2, 3]


def test_append_default_data_is_empty_dict(log):
    log.append("ping")
    assert log.read()[0]["data"] == {}


def test_read_filters_by_type(log):
    log.append("a", {"n": 1})
    log.append("b", {"n": 2})
    log.append("a", {"n": 3})
    only_a = log.read("a")
    assert [e["data"]["n"] for e in only_a] == [1, 3]
    assert all(e["type"] == "a" for e in only_a)


def test_len_counts_well_formed_events(log):
    log.append("a")
    log.append("b")
    assert len(log) == 2


def test_malformed_lines_are_skipped(log):
    log.append("good", {"n": 1})
    # Corrupt the file with a non-JSON line and a wrong-schema entry.
    with open(log.path, "a", encoding="utf-8") as f:
        f.write("not json\n")
        f.write(json.dumps({"v": 999, "type": "future"}) + "\n")
    log.append("good", {"n": 2})
    events = log.read()
    assert [e["data"]["n"] for e in events] == [1, 2]


def test_read_missing_file_returns_empty(tmp_path):
    empty = SessionEventLog(tmp_path / "nope" / "events.jsonl")
    assert empty.read() == []
    assert len(empty) == 0


def test_append_never_raises(tmp_path):
    # Make the would-be parent directory a regular file so mkdir fails.
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file", encoding="utf-8")
    bad = SessionEventLog(blocker / "events.jsonl")
    bad.append("topic/created")  # must not raise
    assert bad.read() == []


# ── SessionManager lifecycle wiring ────────────────────────────────────


def test_create_topic_emits_created_event(sm):
    assert sm.create_topic("work")
    events = sm.event_log("work").read("topic/created")
    assert len(events) == 1
    assert events[0]["data"] == {"topic": "work"}


def test_set_current_topic_emits_switched_event(sm):
    sm.create_topic("work")
    sm.set_current_topic("default")
    events = sm.event_log("default").read("topic/switched")
    assert events and events[-1]["data"] == {"topic": "default"}


def test_rename_topic_emits_renamed_event_and_travels(sm):
    sm.create_topic("old")
    # Seed an event that should travel with the directory rename.
    sm.event_log("old").append("turn/end", {"n": 1})
    assert sm.rename_topic("old", "new")
    events = sm.event_log("new").read()
    types = [e["type"] for e in events]
    assert "turn/end" in types  # survived the rename
    assert types[-1] == "topic/renamed"
    assert events[-1]["data"] == {"from": "old", "to": "new"}


def test_event_log_defaults_to_current_topic(sm):
    sm.create_topic("work")  # becomes current
    assert sm.event_log().path == sm._events_file("work")


def test_sidecar_does_not_touch_history(sm):
    sm.create_topic("work")
    sm.event_log("work").append("turn/end", {"n": 1})
    # history.json must remain absent/untouched by the event sidecar.
    assert not sm.get_history_path("work").exists()


# ── record_turn_event ──────────────────────────────────────────────────


def test_record_turn_event_appends_turn_end(sm):
    sm.create_topic("work")
    sm.record_turn_event(
        user_text="fix the bug",
        assistant_text="done",
        tools_used=["read_file", "write_file"],
        outcome="success",
        topic="work",
    )
    events = sm.event_log("work").read("turn/end")
    assert len(events) == 1
    data = events[0]["data"]
    assert data["user"] == "fix the bug"
    assert data["assistant"] == "done"
    assert data["tools"] == ["read_file", "write_file"]
    assert data["outcome"] == "success"


def test_record_turn_event_truncates_long_text(sm):
    sm.create_topic("work")
    sm.record_turn_event(
        user_text="x" * 500,
        assistant_text="y" * 500,
        topic="work",
    )
    data = sm.event_log("work").read("turn/end")[0]["data"]
    assert len(data["user"]) == 200
    assert len(data["assistant"]) == 200


def test_record_turn_event_never_raises(sm):
    # Odd / empty inputs must not raise (best-effort sidecar).
    sm.record_turn_event(user_text="", assistant_text="", topic="default")
    sm.record_turn_event(
        user_text=None,
        assistant_text=None,
        tools_used=None,
        topic="default",
    )


# ── messages/snapshot + resume_from_events ─────────────────────────────


def test_snapshot_round_trip(sm):
    sm.create_topic("work")
    msgs = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    sm.record_messages_snapshot(msgs, topic="work")
    events = sm.event_log("work").read("messages/snapshot")
    assert len(events) == 1
    assert events[0]["data"]["topic"] == "work"
    assert events[0]["data"]["messages"] == msgs
    assert sm.resume_from_events("work") == msgs


def test_resume_picks_latest_snapshot(sm):
    sm.create_topic("work")
    sm.record_messages_snapshot(
        [{"role": "user", "content": "v1"}],
        topic="work",
    )
    sm.record_messages_snapshot(
        [
            {"role": "user", "content": "v1"},
            {"role": "assistant", "content": "v2"},
        ],
        topic="work",
    )
    resumed = sm.resume_from_events("work")
    assert resumed[-1]["content"] == "v2"
    assert len(resumed) == 2


def test_resume_without_snapshot_returns_empty(sm):
    sm.create_topic("work")
    sm.event_log("work").append("turn/end", {"n": 1})
    assert sm.resume_from_events("work") == []


def test_record_messages_snapshot_never_raises(sm):
    sm.record_messages_snapshot(None, topic="default")
    sm.record_messages_snapshot([], topic="../evil")
    assert sm.resume_from_events("../evil") == []


# ── fork_topic ─────────────────────────────────────────────────────────


def test_fork_topic_copies_events_and_marks_fork(sm):
    sm.create_topic("src")
    msgs = [{"role": "user", "content": "context"}]
    sm.record_messages_snapshot(msgs, topic="src")
    assert sm.fork_topic("src", "dst")
    # The fork inherits the full event history …
    assert sm.resume_from_events("dst") == msgs
    # … plus a fork marker as the last event.
    events = sm.event_log("dst").read()
    assert events[-1]["type"] == "topic/forked"
    assert events[-1]["data"] == {"from": "src", "to": "dst"}
    # Source is untouched by the marker.
    assert sm.event_log("src").read("topic/forked") == []


def test_fork_topic_rejects_bad_src_or_dst(sm):
    sm.create_topic("src")
    assert not sm.fork_topic("missing", "dst")  # src must exist
    sm.create_topic("dst")
    assert not sm.fork_topic("src", "dst")  # dst must not exist
    assert not sm.fork_topic("../evil", "dst2")  # unsafe names


# ── read_raw ───────────────────────────────────────────────────────────


def test_read_raw_includes_other_schema_versions(log):
    log.append("ours", {"n": 1})
    with open(log.path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"v": 999, "type": "future"}) + "\n")
        f.write("not json\n")
    raw = log.read_raw()
    assert [e.get("type") for e in raw] == ["ours", "future"]
    # read() still filters to the current schema.
    assert [e["type"] for e in log.read()] == ["ours"]


# ── compact_snapshots ──────────────────────────────────────────────────


def _snap(log, tag):
    log.append(
        "messages/snapshot",
        {"messages": [{"role": "user", "content": tag}]},
    )


def test_compact_keeps_newest_snapshots_and_audit_trail(log):
    log.append("topic/created", {"topic": "work"})
    for i in range(5):
        log.append("turn/end", {"n": i})
        _snap(log, f"v{i}")
    assert log.compact_snapshots(keep=2)
    events = log.read()
    # 1 lifecycle + 5 turn/end + 2 snapshots, seq renumbered 1..N.
    assert [e["seq"] for e in events] == list(range(1, len(events) + 1))
    snaps = log.read("messages/snapshot")
    assert [s["data"]["messages"][0]["content"] for s in snaps] == [
        "v3",
        "v4",
    ]
    # Audit trail intact.
    assert len(log.read("turn/end")) == 5
    assert log.read("topic/created")


def test_compact_noop_when_at_or_under_keep(log):
    _snap(log, "v0")
    _snap(log, "v1")
    before = log.path.read_text(encoding="utf-8")
    assert not log.compact_snapshots(keep=2)
    assert log.path.read_text(encoding="utf-8") == before


def test_compact_preserves_foreign_schema_entries(log):
    _snap(log, "v0")
    with open(log.path, "a", encoding="utf-8") as f:
        f.write(json.dumps({"v": 999, "type": "future"}) + "\n")
    _snap(log, "v1")
    _snap(log, "v2")
    assert log.compact_snapshots(keep=1)
    types = [e.get("type") for e in log.read_raw()]
    assert "future" in types
    assert types.count("messages/snapshot") == 1


def test_compact_never_raises(tmp_path):
    blocker = tmp_path / "blocked"
    blocker.write_text("i am a file", encoding="utf-8")
    bad = SessionEventLog(blocker / "events.jsonl")
    assert not bad.compact_snapshots()  # must not raise


def test_auto_compaction_bounds_snapshot_count(sm):
    from dashscope_agenticx.session import _SNAPSHOT_COMPACT_AT, _SNAPSHOT_KEEP

    sm.create_topic("work")
    total = _SNAPSHOT_COMPACT_AT + 3
    for i in range(total):
        sm.record_messages_snapshot(
            [{"role": "user", "content": f"v{i}"}],
            topic="work",
        )
    snaps = sm.event_log("work").read("messages/snapshot")
    assert len(snaps) <= _SNAPSHOT_COMPACT_AT
    # The newest snapshot survived, so resume still works.
    resumed = sm.resume_from_events("work")
    assert resumed == [{"role": "user", "content": f"v{total - 1}"}]
    # topic/created survived the compaction.
    assert sm.event_log("work").read("topic/created")
    assert _SNAPSHOT_KEEP >= 1


# ── load_session: events as source of truth ────────────────────────────


def _bare_agent(session_path):
    """An Agent instance without __init__ (load_session needs only
    session_path and messages)."""
    from dashscope_agenticx.agent import Agent

    agent = Agent.__new__(Agent)
    agent.session_path = session_path
    agent.messages = []
    return agent


def test_load_session_prefers_event_snapshot(sm):
    sm.create_topic("work")
    msgs = [{"role": "user", "content": "from-events"}]
    sm.record_messages_snapshot(msgs, topic="work")
    sm.get_history_path("work").write_text(
        json.dumps([{"role": "user", "content": "from-history"}]),
        encoding="utf-8",
    )
    agent = _bare_agent(sm.get_history_path("work"))
    assert agent.load_session() == 1
    assert agent.messages == msgs


def test_load_session_falls_back_to_history(sm):
    sm.create_topic("work")  # no snapshot events yet
    sm.get_history_path("work").write_text(
        json.dumps([{"role": "user", "content": "h"}]),
        encoding="utf-8",
    )
    agent = _bare_agent(sm.get_history_path("work"))
    assert agent.load_session() == 1
    assert agent.messages[0]["content"] == "h"


def test_load_session_recovers_from_corrupt_history(sm):
    sm.create_topic("work")
    msgs = [{"role": "user", "content": "safe"}]
    sm.record_messages_snapshot(msgs, topic="work")
    sm.get_history_path("work").write_text("{corrupt", encoding="utf-8")
    agent = _bare_agent(sm.get_history_path("work"))
    assert agent.load_session() == 1
    assert agent.messages == msgs


def test_load_session_tolerates_torn_final_event(sm):
    sm.create_topic("work")
    sm.record_messages_snapshot(
        [{"role": "user", "content": "v1"}],
        topic="work",
    )
    # Simulate a crash mid-append: partial JSON as the last line.
    with open(sm._events_file("work"), "a", encoding="utf-8") as f:
        f.write('{"v": 1, "seq": 99, "type": "messages/snap')
    agent = _bare_agent(sm.get_history_path("work"))
    assert agent.load_session() == 1
    assert agent.messages[0]["content"] == "v1"


def test_load_session_without_path_returns_zero():
    assert _bare_agent(None).load_session() == 0
