# -*- coding: utf-8 -*-
"""/session fork: expose fork_topic as a user command."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

import dashscope_agenticx.session as session_module
from dashscope_agenticx.cli.handlers_session import _handle_session_command
from dashscope_agenticx.session import SessionManager


@pytest.fixture()
def sm(tmp_path):
    """Fresh SessionManager installed as the global instance."""
    mgr = SessionManager(workspace_dir=tmp_path)
    old = session_module._manager
    session_module.set_session_manager(mgr)
    yield mgr
    session_module._manager = old


def _make_agent(session_path):
    agent = MagicMock()
    agent.session_path = session_path
    agent.messages = [{"role": "user", "content": "hi"}]
    return agent


def test_fork_current_topic_flushes_and_copies(sm):
    sm.create_topic("work")
    sm.event_log("work").append("turn/end", {"n": 1})
    agent = _make_agent(sm.get_history_path("work"))

    _handle_session_command("/session fork backup", MagicMock(), agent)

    agent.save_session.assert_called_once()
    assert "backup" in [m.topic for m in sm.list_topics()]
    dst_events = sm.event_log("backup").read()
    assert any(e["type"] == "turn/end" for e in dst_events)
    assert dst_events[-1]["type"] == "topic/forked"


def test_fork_with_explicit_source(sm):
    sm.create_topic("work")
    sm.create_topic("other")  # now current
    agent = _make_agent(sm.get_history_path("other"))

    _handle_session_command(
        "/session fork backup work",
        MagicMock(),
        agent,
    )

    # Forking a non-current topic must not flush the live agent.
    agent.save_session.assert_not_called()
    dst_events = sm.event_log("backup").read()
    assert dst_events[-1]["data"] == {"from": "work", "to": "backup"}


def test_fork_to_existing_topic_fails(sm):
    sm.create_topic("work")
    sm.create_topic("taken")
    agent = _make_agent(sm.get_history_path("taken"))
    sm.set_current_topic("work")

    _handle_session_command("/session fork taken", MagicMock(), agent)

    # No fork marker recorded in the pre-existing topic.
    assert not any(
        e["type"] == "topic/forked" for e in sm.event_log("taken").read()
    )


def test_fork_copies_history_json(sm):
    """Pre-snapshot sessions keep messages only in history.json."""
    sm.create_topic("work")
    history = sm.get_history_path("work")
    history.write_text('[{"role": "user", "content": "old"}]')
    agent = _make_agent(None)

    _handle_session_command("/session fork backup work", MagicMock(), agent)

    forked = sm.get_history_path("backup")
    assert forked.is_file()
    assert forked.read_text() == history.read_text()
