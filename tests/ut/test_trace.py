# -*- coding: utf-8 -*-
"""Tests for memory/trace.py — TraceLogger."""

from __future__ import annotations

import json

import pytest

from dashscope_agenticx.memory.trace import TraceLogger


@pytest.fixture()
def logger(tmp_path):
    return TraceLogger(workspace_dir=tmp_path)


# ── basic logging ──────────────────────────────────────────────────────────


def test_log_creates_trace_file(logger, tmp_path):
    logger.log("test_event", {"foo": "bar"})
    files = list((tmp_path / "traces").glob("*.jsonl"))
    assert len(files) == 1


def test_log_writes_valid_jsonl(logger, tmp_path):
    logger.log("my_event", {"x": 1})
    content = logger.trace_file.read_text(encoding="utf-8")
    entry = json.loads(content.strip())
    assert entry["event"] == "my_event"
    assert entry["x"] == 1


def test_log_multiple_entries(logger):
    logger.log("e1", {"a": 1})
    logger.log("e2", {"b": 2})
    lines = logger.trace_file.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["event"] == "e1"
    assert json.loads(lines[1])["event"] == "e2"


def test_log_includes_timestamp(logger):
    logger.log("ts_test", {})
    entry = json.loads(logger.trace_file.read_text().strip())
    assert "timestamp" in entry
    assert "elapsed_ms" in entry


def test_log_sanitizes_secrets(logger):
    logger.log("secret_event", {"api_key": "sk-secret123456789"})
    entry = json.loads(logger.trace_file.read_text().strip())
    assert "sk-secret" not in str(entry)


# ── log_llm_call ───────────────────────────────────────────────────────────


def test_log_llm_call(logger):
    logger.log_llm_call(
        messages=[{"role": "user", "content": "hello"}],
        tools=[{"name": "read_file"}],
        response={"content": "world", "tool_calls": []},
    )
    entry = json.loads(logger.trace_file.read_text().strip())
    assert entry["event"] == "llm_call"
    assert entry["message_count"] == 1
    assert entry["tool_count"] == 1


# ── log_tool_execution ─────────────────────────────────────────────────────


def test_log_tool_execution(logger):
    logger.log_tool_execution(
        tool_name="read_file",
        arguments={"path": "README.md"},
        result="file content here",
        duration_ms=42,
        success=True,
    )
    entry = json.loads(logger.trace_file.read_text().strip())
    assert entry["event"] == "tool_execution"
    assert entry["tool"] == "read_file"
    assert entry["success"] is True
    assert entry["duration_ms"] == 42


# ── log_decision ───────────────────────────────────────────────────────────


def test_log_decision(logger):
    logger.log_decision("plan_created", {"plan_name": "deploy", "steps": 3})
    entry = json.loads(logger.trace_file.read_text().strip())
    assert entry["event"] == "decision"
    assert entry["type"] == "plan_created"
    assert entry["plan_name"] == "deploy"


# ── graceful failure ───────────────────────────────────────────────────────


def test_log_does_not_raise_on_unwritable_dir(tmp_path, monkeypatch):
    """Trace errors must never propagate to the main agent loop."""
    logger = TraceLogger(workspace_dir=tmp_path)
    # Make trace file unwritable
    logger.trace_file.touch()
    logger.trace_file.chmod(0o000)
    try:
        logger.log("fail_silently", {"ok": True})  # should not raise
    finally:
        logger.trace_file.chmod(0o644)


# ── enriched llm_call (duration/model, debug-gated prompt) ─────────────────


def test_log_llm_call_includes_duration_and_model(logger):
    logger.log_llm_call(
        messages=[{"role": "user", "content": "hi"}],
        tools=[],
        response={"content": "ok", "tool_calls": []},
        duration_ms=1234,
        model="qwen3-max",
    )
    entry = json.loads(logger.trace_file.read_text().strip())
    assert entry["duration_ms"] == 1234
    assert entry["model"] == "qwen3-max"


def test_log_llm_call_omits_messages_by_default(logger):
    logger.log_llm_call(
        messages=[{"role": "user", "content": "secret prompt"}],
        tools=[{"name": "f"}],
        response={"content": "", "tool_calls": []},
    )
    entry = json.loads(logger.trace_file.read_text().strip())
    assert "messages" not in entry
    assert "tools" not in entry or "messages" not in entry


def test_log_llm_call_includes_messages_when_debug_on(logger):
    from dashscope_agenticx import debuglog

    debuglog.set_debug_enabled(True)
    try:
        logger.log_llm_call(
            messages=[{"role": "user", "content": "full prompt body"}],
            tools=[{"name": "read_file"}],
            response={"content": "", "tool_calls": []},
        )
    finally:
        debuglog.set_debug_enabled(False)
    entry = json.loads(logger.trace_file.read_text().strip())
    assert entry["messages"][0]["content"] == "full prompt body"
    assert entry["tools"] == ["read_file"]


# ── readers ────────────────────────────────────────────────────────────────


def test_iter_events(logger):
    logger.log("e1", {"a": 1})
    logger.log("e2", {"b": 2})
    events = list(logger.iter_events())
    assert [e["event"] for e in events] == ["e1", "e2"]


def test_tail_events(logger):
    for i in range(8):
        logger.log("ev", {"i": i})
    tail = logger.tail_events(3)
    assert [e["i"] for e in tail] == [5, 6, 7]


def test_search_events(logger):
    logger.log("ev", {"text": "hello world"})
    logger.log("ev", {"text": "goodbye"})
    matches = logger.search_events("hello")
    assert len(matches) == 1
    assert matches[0]["text"] == "hello world"


def test_clear(logger):
    logger.log("ev", {"a": 1})
    assert logger.trace_file.exists()
    logger.clear()
    assert not logger.trace_file.exists()
    logger.clear()  # idempotent


# ── /trace rendering ───────────────────────────────────────────────────────


def test_trace_event_text_llm_call():
    from dashscope_agenticx.cli.handlers_config import _trace_event_text

    ev = {
        "event": "llm_call",
        "elapsed_ms": 12300,
        "model": "qwen3-max",
        "message_count": 5,
        "duration_ms": 8200,
        "usage": {"prompt_tokens": 100, "completion_tokens": 20},
        "tool_calls": [{"name": "read_file"}],
    }
    text = _trace_event_text(ev, 1, full=False)
    assert "llm_call" in text
    assert "qwen3-max" in text
    assert "msgs=5" in text
    assert "8.2s" in text
    assert "read_file" in text
    assert "[+12.3s]" in text


def test_trace_event_text_tool_execution():
    from dashscope_agenticx.cli.handlers_config import _trace_event_text

    ev = {
        "event": "tool_execution",
        "elapsed_ms": 100,
        "tool": "run_command",
        "arguments": {"cmd": "ls"},
        "result_preview": "ok",
        "duration_ms": 230,
        "success": True,
    }
    text = _trace_event_text(ev, 2, full=False)
    assert "tool run_command" in text
    assert "230ms" in text
    assert '"cmd": "ls"' in text


def test_trace_event_text_usage_input_tokens_keys():
    """Providers record usage as input_tokens/output_tokens; the renderer
    must show them (prompt_tokens/completion_tokens are legacy names)."""
    from dashscope_agenticx.cli.handlers_config import _trace_event_text

    ev = {
        "event": "llm_call",
        "elapsed_ms": 0,
        "usage": {
            "input_tokens": 1234,
            "output_tokens": 56,
            "cached_tokens": 1000,
        },
    }
    text = _trace_event_text(ev, 1, full=False)
    assert "↑1234 (1000 cached)" in text
    assert "↓56" in text


def test_trace_event_text_llm_call_with_messages():
    from dashscope_agenticx.cli.handlers_config import _trace_event_text

    ev = {
        "event": "llm_call",
        "elapsed_ms": 0,
        "messages": [{"role": "user", "content": "prompt body"}],
    }
    text = _trace_event_text(ev, 1, full=True)
    assert "[user]" in text
    assert "prompt body" in text
