# -*- coding: utf-8 -*-
"""Phase 4 session projection, memory lifecycle, and ID regressions."""
from __future__ import annotations

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.memory.experience import ExperienceTracker
from dashscope_agenticx.memory.manager import MemoryManager
from dashscope_agenticx.session import SessionManager


def test_plan_and_goal_restore_from_session_projection(tmp_path):
    manager = SessionManager(tmp_path)
    memory = MemoryManager(tmp_path)
    memory.session.bind_projection(manager, manager.get_current_topic)
    memory.session.plan.create_plan("ship 1.0", ["code", "test"])
    memory.session.plan.mark_step_complete(0, "write_file")

    restored = MemoryManager(tmp_path)
    restored.session.bind_projection(manager, manager.get_current_topic)
    state = restored.session.restore_projection("default")

    plan = restored.session.plan.current_plan
    assert plan is not None
    assert plan.goal == "ship 1.0"
    assert plan.steps[0].completed is True
    assert plan.steps[0].tool_calls == ["write_file"]
    assert state["goal"] == {
        "description": "ship 1.0",
        "status": "active",
    }


def test_projection_fork_replays_messages_artifacts_and_ids(tmp_path):
    manager = SessionManager(tmp_path)
    ids = {
        "runtime_id": "runtime-1",
        "agent_id": "agent-1",
        "session_id": "default",
        "run_id": "run-1",
        "turn_id": "turn-1",
    }
    messages = [{"role": "user", "content": "hello"}]
    manager.record_messages_snapshot(messages, identifiers=ids)
    manager.record_turn_event(
        "hello",
        "world",
        ["read_file"],
        "success",
        identifiers=ids,
    )
    manager.record_artifact(
        {"path": "/tmp/report.txt", "name": "report.txt", "size": 7},
        identifiers=ids,
    )

    state = manager.project_state("default")
    assert state["messages"] == messages
    assert state["artifacts"] == [
        {
            "topic": "default",
            "path": "/tmp/report.txt",
            "name": "report.txt",
            "size": 7,
            "ids": ids,
        },
    ]
    assert state["last_turn"]["ids"] == ids

    assert manager.fork_topic("default", "forked") is True
    forked = manager.project_state("forked")
    assert forked["messages"] == messages
    assert forked["artifacts"] == state["artifacts"]


def test_experience_requires_verification_and_tracks_ttl_stats(tmp_path):
    tracker = ExperienceTracker(tmp_path)
    tracker.record_experience(
        "verified build",
        ["run_command"],
        "success",
        verified=True,
    )
    tracker.record_experience(
        "unverified guess",
        ["read_file"],
        "partial",
        verified=False,
    )
    tracker.record_experience(
        "expired build",
        ["run_command"],
        "success",
        verified=True,
        ttl_seconds=-1,
    )

    results = tracker.search_experiences("build")
    assert [item["task"] for item in results] == ["verified build"]
    assert tracker.search_experiences("missing") == []
    assert tracker.stats() == {
        "entries": 3,
        "active": 1,
        "hits": 1,
        "misses": 1,
    }
    assert tracker.delete(task="verified build") == 1
    assert tracker.stats()["entries"] == 2
    assert tracker.delete() == 2
    assert tracker.stats()["entries"] == 0


def test_child_memory_close_removes_ephemeral_workspace(tmp_path):
    parent = MemoryManager(tmp_path)
    child = MemoryManager.derive_child(parent)
    child_workspace = child._child_workspace
    assert child_workspace.exists()
    child.close()
    assert not child_workspace.exists()


def test_agent_load_restores_plan_and_artifacts(tmp_path):
    manager = SessionManager(tmp_path)
    memory = MemoryManager(tmp_path)
    memory.session.bind_projection(manager, manager.get_current_topic)
    memory.session.plan.create_plan("restore", ["one"])
    manager.record_messages_snapshot([{"role": "user", "content": "hi"}])
    manager.record_artifact(
        {"path": "/tmp/result.txt", "name": "result.txt", "size": 3},
    )

    agent = Agent.__new__(Agent)
    agent.session_path = manager.get_history_path("default")
    agent.messages = []
    agent.artifacts = []
    agent.memory_manager = MemoryManager(tmp_path)
    assert agent.load_session() == 1
    assert agent.memory_manager.session.plan.current_plan.goal == "restore"
    assert agent.artifacts[0]["name"] == "result.txt"
