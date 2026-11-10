# -*- coding: utf-8 -*-
"""M11: /session rename of the current topic must update agent.session_path."""

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
    return agent


def test_rename_current_topic_updates_session_path(sm):
    sm.create_topic("work")  # create_topic makes it current
    agent = _make_agent(sm.get_history_path("work"))

    _handle_session_command("/session rename work job", MagicMock(), agent)

    assert sm.get_current_topic() == "job"
    assert agent.session_path == sm.get_history_path("job")


def test_rename_non_current_topic_leaves_session_path(sm):
    sm.create_topic("work")
    sm.create_topic("other")  # now current
    agent = _make_agent(sm.get_history_path("other"))

    _handle_session_command("/session rename work job", MagicMock(), agent)

    assert sm.get_current_topic() == "other"
    assert agent.session_path == sm.get_history_path("other")


def test_rename_current_topic_without_session_persist(sm):
    """agent.session_path is None when persistence is off — nothing to update."""
    sm.create_topic("work")
    agent = _make_agent(None)

    _handle_session_command("/session rename work job", MagicMock(), agent)

    assert sm.get_current_topic() == "job"
    assert agent.session_path is None


def test_rename_failure_leaves_session_path(sm):
    sm.create_topic("work")
    sm.create_topic("taken")
    agent = _make_agent(sm.get_history_path("work"))
    sm.set_current_topic("work")

    # 'taken' already exists → rename fails
    _handle_session_command("/session rename work taken", MagicMock(), agent)

    assert sm.get_current_topic() == "work"
    assert agent.session_path == sm.get_history_path("work")
