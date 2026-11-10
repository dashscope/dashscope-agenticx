# -*- coding: utf-8 -*-
"""Tests for session.py — SessionMeta, SessionManager."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashscope_agenticx.session import (
    DEFAULT_TOPIC,
    SessionManager,
    SessionMeta,
)


@pytest.fixture()
def sm(tmp_path):
    """Create a fresh SessionManager backed by tmp_path."""
    return SessionManager(workspace_dir=tmp_path)


# ── SessionMeta ────────────────────────────────────────────────────────────


def test_session_meta_roundtrip():
    meta = SessionMeta(
        topic="work",
        created="2026-01-01T00:00:00",
        last_accessed="2026-01-02T00:00:00",
        message_count=5,
    )
    d = meta.to_dict()
    restored = SessionMeta.from_dict(d)
    assert restored.topic == "work"
    assert restored.message_count == 5
    assert restored.created == "2026-01-01T00:00:00"


def test_session_meta_from_dict_defaults():
    meta = SessionMeta.from_dict({})
    assert meta.topic == DEFAULT_TOPIC
    assert meta.message_count == 0


# ── _ensure_session_dir / init ─────────────────────────────────────────────


def test_session_manager_creates_default_dir(tmp_path):
    sm = SessionManager(workspace_dir=tmp_path)
    assert (tmp_path / "session" / DEFAULT_TOPIC).is_dir()
    assert (tmp_path / "session" / DEFAULT_TOPIC / "meta.json").exists()


# ── list_topics ────────────────────────────────────────────────────────────


def test_list_topics_initial_has_only_default(sm):
    topics = sm.list_topics()
    assert len(topics) == 1
    assert topics[0].topic == DEFAULT_TOPIC


def test_list_topics_default_is_first(sm):
    sm.create_topic("alpha")
    sm.create_topic("beta")
    topics = sm.list_topics()
    assert topics[0].topic == DEFAULT_TOPIC


def test_list_topics_shows_all_created(sm):
    sm.create_topic("a")
    sm.create_topic("b")
    names = {t.topic for t in sm.list_topics()}
    assert {"default", "a", "b"} == names


# ── create_topic ───────────────────────────────────────────────────────────


def test_create_topic_success(sm, tmp_path):
    result = sm.create_topic("new-topic")
    assert result is True
    assert (tmp_path / "session" / "new-topic").is_dir()
    assert sm.current_topic == "new-topic"


def test_create_topic_duplicate_returns_false(sm):
    sm.create_topic("dup")
    assert sm.create_topic("dup") is False


# ── set_current_topic ──────────────────────────────────────────────────────


def test_set_current_topic_existing(sm):
    sm.create_topic("t1")
    sm.set_current_topic(DEFAULT_TOPIC)
    assert sm.set_current_topic("t1") is True
    assert sm.current_topic == "t1"


def test_set_current_topic_nonexistent(sm):
    assert sm.set_current_topic("ghost") is False
    assert sm.current_topic == DEFAULT_TOPIC


# ── rename_topic ───────────────────────────────────────────────────────────


def test_rename_topic_success(sm, tmp_path):
    sm.create_topic("old")
    result = sm.rename_topic("old", "new")
    assert result is True
    assert (tmp_path / "session" / "new").is_dir()
    assert not (tmp_path / "session" / "old").exists()


def test_rename_topic_updates_meta(sm, tmp_path):
    sm.create_topic("old")
    sm.rename_topic("old", "renamed")
    meta_file = tmp_path / "session" / "renamed" / "meta.json"
    data = json.loads(meta_file.read_text())
    assert data["topic"] == "renamed"


def test_rename_topic_updates_current(sm):
    sm.create_topic("cur")
    sm.set_current_topic("cur")
    sm.rename_topic("cur", "renamed")
    assert sm.current_topic == "renamed"


def test_rename_topic_old_not_found(sm):
    assert sm.rename_topic("ghost", "new") is False


def test_rename_topic_new_exists(sm):
    sm.create_topic("a")
    sm.create_topic("b")
    assert sm.rename_topic("a", "b") is False


# ── delete_topic ───────────────────────────────────────────────────────────


def test_delete_topic_success(sm, tmp_path):
    sm.create_topic("temp")
    result = sm.delete_topic("temp")
    assert result is True
    assert not (tmp_path / "session" / "temp").exists()


def test_delete_default_topic_refused(sm):
    assert sm.delete_topic(DEFAULT_TOPIC) is False


def test_delete_topic_resets_current(sm):
    sm.create_topic("x")
    sm.set_current_topic("x")
    sm.delete_topic("x")
    assert sm.current_topic == DEFAULT_TOPIC


def test_delete_topic_nonexistent(sm):
    assert sm.delete_topic("ghost") is False


# ── path helpers ───────────────────────────────────────────────────────────


def test_get_history_path(sm, tmp_path):
    p = sm.get_history_path()
    assert p == tmp_path / "session" / DEFAULT_TOPIC / "history.json"


def test_get_input_history_path(sm, tmp_path):
    p = sm.get_input_history_path()
    assert p == tmp_path / "session" / DEFAULT_TOPIC / "input-history.txt"


def test_get_history_path_explicit_topic(sm, tmp_path):
    sm.create_topic("mywork")
    p = sm.get_history_path("mywork")
    assert p == tmp_path / "session" / "mywork" / "history.json"


# ── update_message_count ───────────────────────────────────────────────────


def test_update_message_count(sm, tmp_path):
    sm.update_message_count(42)
    meta_file = tmp_path / "session" / DEFAULT_TOPIC / "meta.json"
    data = json.loads(meta_file.read_text())
    assert data["message_count"] == 42


# ── should_prompt_archive ──────────────────────────────────────────────────


def test_should_prompt_archive_below_threshold(sm):
    sm.update_message_count(50)
    assert sm.should_prompt_archive(threshold=100) is False


def test_should_prompt_archive_at_threshold(sm):
    sm.update_message_count(100)
    assert sm.should_prompt_archive(threshold=100) is True


def test_should_prompt_archive_non_default_topic(sm):
    sm.create_topic("work")
    sm.update_message_count(200, topic="work")
    assert sm.should_prompt_archive(topic="work", threshold=100) is False


# ── topic name validation (path traversal) ────────────────────────────────


@pytest.mark.parametrize("bad", ["../evil", "..", "a/b", "a\\b", "/abs", ""])
def test_create_topic_rejects_unsafe_names(sm, tmp_path, bad):
    assert sm.create_topic(bad) is False
    assert not (tmp_path / "evil").exists()


def test_create_topic_valid_name_still_works(sm, tmp_path):
    assert sm.create_topic("my-topic.v2") is True
    assert (tmp_path / "session" / "my-topic.v2").is_dir()


def test_delete_topic_rejects_traversal(sm, tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "keep.txt").write_text("keep", encoding="utf-8")
    assert sm.delete_topic("../outside") is False
    assert (outside / "keep.txt").exists()


@pytest.mark.parametrize("bad", ["../x", "..", "a/b", "a\\b", "/abs", ""])
def test_delete_topic_rejects_unsafe_names(sm, bad):
    assert sm.delete_topic(bad) is False


@pytest.mark.parametrize("bad", ["../evil", "..", "a/b", "a\\b", "/abs", ""])
def test_rename_topic_rejects_unsafe_new_name(sm, tmp_path, bad):
    sm.create_topic("work")
    assert sm.rename_topic("work", bad) is False
    assert (tmp_path / "session" / "work").is_dir()
    assert not (tmp_path / "evil").exists()


@pytest.mark.parametrize(
    "bad",
    ["../default", "..", "a/b", "a\\b", "/abs", ""],
)
def test_rename_topic_rejects_unsafe_old_name(sm, bad):
    assert sm.rename_topic(bad, "new-name") is False


@pytest.mark.parametrize(
    "bad",
    ["../default", "..", "a/b", "a\\b", "/abs", ""],
)
def test_set_current_topic_rejects_unsafe_names(sm, bad):
    assert sm.set_current_topic(bad) is False
    assert sm.get_current_topic() == DEFAULT_TOPIC


# ── scene memory ───────────────────────────────────────────────────────────


def test_scene_default_empty(sm):
    assert sm.get_scene() == ""


def test_scene_set_and_get(sm):
    assert sm.set_scene("project X uses pytest")
    assert sm.get_scene() == "project X uses pytest"
    assert (sm.session_dir / DEFAULT_TOPIC / "scene.md").exists()


def test_scene_set_empty_clears(sm):
    sm.set_scene("note")
    assert sm.set_scene("   ")
    assert sm.get_scene() == ""
    assert not (sm.session_dir / DEFAULT_TOPIC / "scene.md").exists()


def test_scene_append(sm):
    sm.append_scene("line one")
    sm.append_scene("line two")
    assert sm.get_scene() == "line one\nline two"


def test_scene_append_empty_is_noop(sm):
    assert not sm.append_scene("  ")
    assert sm.get_scene() == ""


def test_scene_isolated_per_topic(sm):
    sm.create_topic("work")  # switches current to "work"
    sm.set_scene("work context")
    assert sm.get_scene() == "work context"
    assert sm.get_scene(DEFAULT_TOPIC) == ""


def test_scene_follows_rename(sm):
    sm.set_scene("keep me")
    assert sm.rename_topic(DEFAULT_TOPIC, "renamed")
    assert sm.get_scene("renamed") == "keep me"


def test_scene_removed_with_topic(sm):
    sm.create_topic("tmp")
    sm.set_scene("bye", "tmp")
    assert sm.delete_topic("tmp")
    assert sm.get_scene("tmp") == ""


def test_scene_update_records_event(sm):
    sm.set_scene("hello")
    events = sm.event_log().read()
    assert any(e["type"] == "scene/updated" for e in events)


def test_scene_unsafe_topic_rejected(sm):
    assert not sm.set_scene("x", "../escape")
    assert not sm.append_scene("x", "../escape")


def test_scene_section_renders():
    from dashscope_agenticx.prompt_pipeline import PromptContext, SceneSection

    ctx = PromptContext(user_input="hi", scene_provider=lambda: "note A")
    out = SceneSection().render(ctx)
    assert "Scene memory" in out and "note A" in out


def test_scene_section_empty_or_failing_provider():
    from dashscope_agenticx.prompt_pipeline import PromptContext, SceneSection

    assert SceneSection().render(PromptContext(user_input="hi")) == ""
    empty = PromptContext(user_input="hi", scene_provider=lambda: "  ")
    assert SceneSection().render(empty) == ""

    def boom():
        raise RuntimeError("no session")

    failing = PromptContext(user_input="hi", scene_provider=boom)
    assert SceneSection().render(failing) == ""


def test_environment_section_states_os_and_shell():
    """run_command's syntax is platform-specific, so the prompt must say which.

    Without this the model wrote `ls -la ... 2>/dev/null || find /` on a
    Windows host and burned a turn on the PowerShell ParserError.
    """
    import platform

    from dashscope_agenticx.prompt_pipeline import (
        EnvironmentSection,
        PromptContext,
    )
    from dashscope_agenticx.tools.shell import shell_label

    ctx = PromptContext(
        user_input="hi",
        user_name="dashscope",
        provider_name="tongyi",
        model_name="qwen3.8-max",
    )
    out = EnvironmentSection().render(ctx)

    assert f"Current OS: {platform.system()} {platform.machine()}" in out
    assert f"run_command executes via: {shell_label()}" in out
    assert "Current user: dashscope" in out
    assert "Current model: tongyi/qwen3.8-max" in out


def test_environment_section_is_never_empty():
    """The platform lines do not depend on any context being available."""
    from dashscope_agenticx.prompt_pipeline import (
        EnvironmentSection,
        PromptContext,
    )

    out = EnvironmentSection().render(PromptContext(user_input="hi"))

    assert "Current OS:" in out
    assert "run_command executes via:" in out
