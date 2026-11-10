# -*- coding: utf-8 -*-
"""Tests for memory/manager.py — SessionMemory, MemoryManager."""

from __future__ import annotations

from dashscope_agenticx.memory.manager import (
    MemoryManager,
    PersistentMemory,
    SessionMemory,
)

# ── SessionMemory ──────────────────────────────────────────────────────────


def test_session_memory_plan_tracker(tmp_path):
    sm = SessionMemory(workspace_dir=tmp_path)
    sm.plan.create_plan("Build rocket", ["design", "build", "launch"])
    status = sm.plan.get_plan_section()
    assert "Build rocket" in status or "design" in status


def test_session_memory_reflection_tracker(tmp_path):
    sm = SessionMemory(workspace_dir=tmp_path)
    for _ in range(4):
        sm.reflection.record_tool_execution("run_command", success=False)
    hint = sm.reflection.get_reflection_hint()
    assert hint  # should have a reflection hint after multiple failures


def test_session_memory_reset(tmp_path):
    sm = SessionMemory(workspace_dir=tmp_path)
    sm.plan.create_plan("old plan", ["step1"])
    sm.reset()
    assert sm.plan.current_plan is None


# ── MemoryManager ──────────────────────────────────────────────────────────


def test_memory_manager_record_experience(tmp_path):
    mm = MemoryManager(workspace_dir=tmp_path)
    mm.record_experience(
        task_summary="fix bug in auth",
        tools_used=["read_file", "write_file"],
        outcome="success",
        lesson="always check None",
    )
    # Should not raise; experience recorded to JSONL
    experiences = mm.persistent.search_experiences("auth bug")
    # keyword match may or may not find — just confirm no exception
    assert isinstance(experiences, list)


def test_memory_manager_record_tool_execution(tmp_path):
    mm = MemoryManager(workspace_dir=tmp_path)
    mm.record_tool_execution("run_command", success=True)
    mm.record_tool_execution("run_command", success=False)
    # Reflection tracker should have recorded
    assert mm.session.reflection.consecutive_failures >= 1


def test_memory_manager_reset_session(tmp_path):
    mm = MemoryManager(workspace_dir=tmp_path)
    mm.session.plan.create_plan("test plan", ["s1"])
    mm.reset_session()
    assert mm.session.plan.current_plan is None


def test_memory_manager_log_trace_no_crash(tmp_path):
    mm = MemoryManager(workspace_dir=tmp_path)
    mm.log_trace("test_event", {"key": "value"})
    # Trace file should have been created
    trace_files = list((tmp_path / "traces").glob("*.jsonl"))
    assert len(trace_files) == 1


# ── derive_child (sub-agent isolation) ─────────────────────────────────────


def test_derive_child_has_independent_session_tier(tmp_path):
    parent = MemoryManager(workspace_dir=tmp_path)
    child = MemoryManager.derive_child(parent)

    assert child.session is not parent.session
    assert child.session.plan is not parent.session.plan
    assert child.session.reflection is not parent.session.reflection
    assert child.session.tool_chains is not parent.session.tool_chains


def test_derive_child_shares_persistent_and_trace(tmp_path):
    parent = MemoryManager(workspace_dir=tmp_path)
    child = MemoryManager.derive_child(parent)

    assert child.persistent is parent.persistent
    assert child.trace is parent.trace


def test_derive_child_failures_do_not_move_parent_reflection(tmp_path):
    parent = MemoryManager(workspace_dir=tmp_path)
    child = MemoryManager.derive_child(parent)

    for _ in range(4):
        child.record_tool_execution("run_command", success=False)

    assert child.session.reflection.consecutive_failures == 4
    assert parent.session.reflection.consecutive_failures == 0
    assert parent.session.reflection.get_reflection_hint() == ""


def test_derive_child_plan_does_not_leak_to_parent(tmp_path):
    parent = MemoryManager(workspace_dir=tmp_path)
    child = MemoryManager.derive_child(parent)

    child.session.plan.create_plan("child plan", ["s1"])
    assert parent.session.plan.current_plan is None
    assert parent.session.plan.get_plan_section() == ""
