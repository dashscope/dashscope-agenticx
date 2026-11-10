# -*- coding: utf-8 -*-
"""Tests for /history search — full-text search across stored sessions.

Sessions are isolated by installing a SessionManager backed by tmp_path
as the global instance (same monkeypatch style as other ut tests).
"""

from __future__ import annotations

import json

import pytest

from dashscope_agenticx import session as session_mod
from dashscope_agenticx.cli import handlers_misc as hm
from dashscope_agenticx.cli.constants import _SUBCOMMANDS
from dashscope_agenticx.session import SessionManager

MSGS_DEFAULT = [
    {"role": "user", "content": "Please write a Python script for me"},
    {"role": "assistant", "content": "Sure, here is the Python code."},
]

MSGS_WORK = [
    {"role": "user", "content": "Fix the login bug in production"},
    {"role": "assistant", "content": "The python tests now pass."},
]


class _Out:
    """Console mock capturing print() args as joined text."""

    def __init__(self) -> None:
        self.lines: list[str] = []

    def print(self, *args, **kwargs) -> None:
        self.lines.append(" ".join(str(a) for a in args))

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def mgr(tmp_path, monkeypatch):
    """Isolated SessionManager installed as the global instance."""
    manager = SessionManager(workspace_dir=tmp_path)
    monkeypatch.setattr(session_mod, "_manager", manager)
    return manager


@pytest.fixture
def out():
    return _Out()


def _seed(mgr: SessionManager, topic: str, messages: list) -> None:
    """Write message history for a topic into isolated storage."""
    if topic != "default":
        mgr.create_topic(topic)
    path = mgr.get_history_path(topic)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(messages, ensure_ascii=False),
        encoding="utf-8",
    )


# ---------------------------------------------------------------------------
# _history_search_matches
# ---------------------------------------------------------------------------


class TestSearchMatches:
    def test_match_found_with_snippet(self, mgr):
        _seed(mgr, "default", MSGS_DEFAULT)
        matches = hm._history_search_matches("Python")
        assert len(matches) == 2
        first = matches[0]
        assert first["session"] == "default"
        assert first["role"] == "user"
        assert "Python" in first["snippet"]
        assert first["timestamp"]

    def test_case_insensitive(self, mgr):
        _seed(mgr, "default", MSGS_DEFAULT)
        assert len(hm._history_search_matches("pYtHoN")) == 2
        assert len(hm._history_search_matches("SCRIPT")) == 1

    def test_no_match_returns_empty(self, mgr):
        _seed(mgr, "default", MSGS_DEFAULT)
        assert hm._history_search_matches("zzz-not-there") == []

    def test_empty_storage_returns_empty(self, mgr):
        assert hm._history_search_matches("anything") == []

    def test_limit_respected(self, mgr):
        msgs = [
            {"role": "user", "content": f"needle message number {i}"}
            for i in range(5)
        ]
        _seed(mgr, "default", msgs)
        matches = hm._history_search_matches("needle", limit=2)
        assert len(matches) == 2

    def test_search_across_sessions(self, mgr):
        _seed(mgr, "default", MSGS_DEFAULT)
        _seed(mgr, "work", MSGS_WORK)
        matches = hm._history_search_matches("python")
        sessions = {m["session"] for m in matches}
        assert sessions == {"default", "work"}
        assert len(matches) == 3

    def test_multimodal_content_is_searched(self, mgr):
        msgs = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "Analyze this PYTHON file"},
                    {"type": "image_url", "image_url": {"url": "x"}},
                ],
            },
        ]
        _seed(mgr, "default", msgs)
        matches = hm._history_search_matches("python")
        assert len(matches) == 1
        assert "PYTHON" in matches[0]["snippet"]

    def test_message_without_content_is_skipped(self, mgr):
        msgs = [
            {"role": "assistant", "tool_calls": []},
            {"role": "user", "content": "find the needle here"},
        ]
        _seed(mgr, "default", msgs)
        matches = hm._history_search_matches("needle")
        assert len(matches) == 1
        assert matches[0]["role"] == "user"

    def test_long_content_snippet_is_truncated(self, mgr):
        content = "x" * 100 + "needle" + "y" * 100
        _seed(mgr, "default", [{"role": "user", "content": content}])
        matches = hm._history_search_matches("needle")
        snippet = matches[0]["snippet"]
        assert "needle" in snippet
        assert snippet.startswith("...")
        assert snippet.endswith("...")
        assert len(snippet) <= 72


# ---------------------------------------------------------------------------
# _handle_history_command — search branch
# ---------------------------------------------------------------------------


class TestHistorySearchCommand:
    def test_missing_keyword_prints_usage(self, mgr, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        hm._handle_history_command("/history search")
        assert "Usage: /history search <keyword>" in out.text

    def test_no_match_message(self, mgr, out, monkeypatch):
        _seed(mgr, "default", MSGS_DEFAULT)
        monkeypatch.setattr(hm, "console", out)
        hm._handle_history_command("/history search zzz")
        assert "No matches for 'zzz'" in out.text

    def test_match_output_shows_session_role_snippet(
        self,
        mgr,
        out,
        monkeypatch,
    ):
        _seed(mgr, "default", MSGS_DEFAULT)
        monkeypatch.setattr(hm, "console", out)
        hm._handle_history_command("/history search python")
        assert "match(es) for 'python'" in out.text
        assert "default" in out.text
        assert "user" in out.text
        assert "Python" in out.text

    def test_limit_argument_respected(self, mgr, out, monkeypatch):
        msgs = [
            {"role": "user", "content": f"needle message number {i}"}
            for i in range(5)
        ]
        _seed(mgr, "default", msgs)
        monkeypatch.setattr(hm, "console", out)
        hm._handle_history_command("/history search needle 2")
        assert "2 match(es) for 'needle'" in out.text

    def test_invalid_limit_rejected(self, mgr, out, monkeypatch):
        _seed(mgr, "default", MSGS_DEFAULT)
        monkeypatch.setattr(hm, "console", out)
        hm._handle_history_command("/history search python abc")
        assert "positive integer" in out.text

    def test_search_listed_in_completion(self):
        assert "search" in _SUBCOMMANDS["/history"]
