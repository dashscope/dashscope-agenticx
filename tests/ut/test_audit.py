# -*- coding: utf-8 -*-
"""Tests for the audit logging module."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from dashscope_agenticx.audit import (
    AuditEvent,
    AuditLogger,
    _redact_args,
    get_audit_logger,
    set_audit_logger,
)

# ── AuditEvent.now ─────────────────────────────────────────────────────


class TestAuditEvent:
    def test_now_creates_event_with_required_fields(self):
        event = AuditEvent.now("user", "input", "hello world", "received")
        assert event.source == "user"
        assert event.action == "input"
        assert event.subject == "hello world"
        assert event.decision == "received"
        assert event.reason == ""
        assert event.details == {}

    def test_now_accepts_reason(self):
        event = AuditEvent.now(
            "agent",
            "tool_call",
            "bash",
            "denied",
            reason="user declined",
        )
        assert event.reason == "user declined"

    def test_now_accepts_details_kwargs(self):
        event = AuditEvent.now(
            "agent",
            "tool_call",
            "bash",
            "executed",
            command="ls -la",
        )
        assert event.details == {"command": "ls -la"}

    def test_now_timestamp_is_iso_string(self):
        event = AuditEvent.now("user", "input", "hi", "received")
        assert isinstance(event.timestamp, str)
        assert "T" in event.timestamp  # ISO format


# ── _redact_args ────────────────────────────────────────────────────────


class TestRedactArgs:
    def test_sensitive_key_long_string_is_redacted(self):
        args = {"content": "a" * 50}
        result = _redact_args(args)
        assert result["content"] == "a" * 20 + "…(redacted)"

    def test_sensitive_key_short_string_not_redacted(self):
        args = {"content": "short"}
        result = _redact_args(args)
        assert result["content"] == "short"

    def test_sensitive_key_non_string_not_redacted(self):
        args = {"path": 12345}
        result = _redact_args(args)
        assert result["path"] == 12345

    def test_non_sensitive_key_not_redacted(self):
        args = {"model": "qwen-" + "a" * 50}
        result = _redact_args(args)
        assert result["model"] == "qwen-" + "a" * 50

    def test_credential_key_always_redacted(self):
        # api_key is in _CREDENTIAL_ARG_KEYS: redacted in full regardless of
        # length (previously it passed through unredacted — a real leak).
        result = _redact_args({"api_key": "sk-" + "a" * 50})
        assert result["api_key"] == "…(redacted)"

    def test_all_sensitive_keys_are_redacted_when_long(self):
        long_val = "x" * 50
        for key in (
            "content",
            "command",
            "query",
            "text",
            "prompt",
            "url",
            "path",
            "file",
        ):
            result = _redact_args({key: long_val})
            assert (
                result[key] == "x" * 20 + "…(redacted)"
            ), f"key '{key}' not redacted"

    def test_case_insensitive_key_matching(self):
        args = {"Content": "a" * 50}
        result = _redact_args(args)
        assert "…(redacted)" in result["Content"]

    def test_empty_dict(self):
        assert _redact_args({}) == {}


# ── AuditLogger ─────────────────────────────────────────────────────────


class TestAuditLogger:
    def test_log_user_input(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_user_input("hello world")
        events = logger.recent()
        assert len(events) == 1
        assert events[0]["source"] == "user"
        assert events[0]["action"] == "input"
        assert events[0]["decision"] == "received"
        assert "hello world" in events[0]["subject"]

    def test_log_user_input_truncates_long_input(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        long_text = "x" * 200
        logger.log_user_input(long_text, preview_len=50)
        events = logger.recent()
        assert len(events) == 1
        assert events[0]["subject"].endswith("…")
        assert len(events[0]["subject"]) == 51  # 50 chars + ellipsis

    def test_log_user_input_short_input_no_ellipsis(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_user_input("hi")
        events = logger.recent()
        assert events[0]["subject"] == "hi"

    def test_log_tool_call_denied(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_tool_call(
            "bash",
            {"command": "rm -rf /"},
            "denied",
            "user declined",
        )
        events = logger.recent()
        assert len(events) == 1
        assert events[0]["source"] == "agent"
        assert events[0]["action"] == "tool_call"
        assert events[0]["subject"] == "bash"
        assert events[0]["decision"] == "denied"
        assert events[0]["reason"] == "user declined"
        assert events[0]["details"]["command"] == "rm -rf /"

    def test_log_tool_call_executed(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_tool_call("read_file", {"path": "test.py"}, "executed")
        events = logger.recent()
        assert events[0]["decision"] == "executed"
        assert events[0]["reason"] == ""
        assert events[0]["details"]["path"] == "test.py"

    def test_log_tool_call_failed(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_tool_call(
            "write_file",
            {"path": "/x"},
            "failed",
            "PermissionError",
        )
        events = logger.recent()
        assert events[0]["decision"] == "failed"
        assert events[0]["reason"] == "PermissionError"

    def test_log_cron(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_cron("job-123", "auto", "scheduled trigger")
        events = logger.recent()
        assert len(events) == 1
        assert events[0]["source"] == "scheduler"
        assert events[0]["action"] == "cron"
        assert events[0]["subject"] == "job-123"
        assert events[0]["decision"] == "auto"
        assert events[0]["reason"] == "scheduled trigger"

    def test_query_by_source(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_user_input("hello")
        logger.log_tool_call("bash", {}, "executed")
        logger.log_cron("job-1", "auto")
        user_events = logger.query(source="user")
        assert len(user_events) == 1
        assert user_events[0]["action"] == "input"
        agent_events = logger.query(source="agent")
        assert len(agent_events) == 1
        assert agent_events[0]["action"] == "tool_call"

    def test_query_by_action(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_user_input("hello")
        logger.log_tool_call("bash", {}, "executed")
        logger.log_tool_call("bash", {}, "denied")
        tool_events = logger.query(action="tool_call")
        assert len(tool_events) == 2
        cron_events = logger.query(action="cron")
        assert len(cron_events) == 0

    def test_query_by_decision(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_tool_call("bash", {}, "executed")
        logger.log_tool_call("bash", {}, "denied")
        executed = logger.query(decision="executed")
        assert len(executed) == 1
        denied = logger.query(decision="denied")
        assert len(denied) == 1

    def test_query_by_subject(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_tool_call("read_file", {}, "executed")
        logger.log_tool_call("write_file", {}, "executed")
        results = logger.query(subject="read_file")
        assert len(results) == 1
        assert results[0]["subject"] == "read_file"

    def test_query_with_limit(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        for i in range(10):
            logger.log_tool_call(f"tool_{i}", {}, "executed")
        results = logger.query(limit=3)
        assert len(results) == 3

    def test_query_on_nonexistent_file_returns_empty(self, tmp_path):
        logger = AuditLogger(tmp_path / "nonexistent.log")
        assert logger.query() == []

    def test_recent_returns_last_n(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        for i in range(5):
            logger.log_tool_call(f"tool_{i}", {}, "executed")
        events = logger.recent(limit=3)
        assert len(events) == 3
        # Should be the last 3
        assert events[0]["subject"] == "tool_2"
        assert events[2]["subject"] == "tool_4"

    def test_recent_on_nonexistent_file_returns_empty(self, tmp_path):
        logger = AuditLogger(tmp_path / "nonexistent.log")
        assert logger.recent() == []

    def test_clear_removes_file(self, tmp_path):
        log_path = tmp_path / "audit.log"
        logger = AuditLogger(log_path)
        logger.log_user_input("hello")
        assert log_path.exists()
        logger.clear()
        assert not log_path.exists()

    def test_clear_on_nonexistent_file_is_noop(self, tmp_path):
        logger = AuditLogger(tmp_path / "nonexistent.log")
        logger.clear()  # should not raise

    def test_multiple_events_appended(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.log_user_input("first")
        logger.log_user_input("second")
        logger.log_tool_call("bash", {}, "executed")
        events = logger.recent(limit=20)
        assert len(events) == 3
        assert events[0]["subject"] == "first"
        assert events[2]["subject"] == "bash"


# ── Privacy mode ────────────────────────────────────────────────────────


class TestPrivacyMode:
    def test_privacy_mode_redacts_sensitive_args(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.set_privacy_mode(True)
        long_content = "a" * 50
        logger.log_tool_call(
            "write_file",
            {"content": long_content},
            "executed",
        )
        events = logger.recent()
        assert len(events) == 1
        assert events[0]["details"]["content"] == "a" * 20 + "…(redacted)"

    def test_privacy_mode_redacts_multiple_sensitive_keys(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.set_privacy_mode(True)
        long_val = "x" * 50
        logger.log_tool_call(
            "multi",
            {"content": long_val, "command": long_val, "query": long_val},
            "executed",
        )
        events = logger.recent()
        for key in ("content", "command", "query"):
            assert events[0]["details"][key] == "x" * 20 + "…(redacted)"

    def test_privacy_mode_keeps_short_values(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.set_privacy_mode(True)
        logger.log_tool_call("read_file", {"path": "short"}, "executed")
        events = logger.recent()
        assert events[0]["details"]["path"] == "short"

    def test_privacy_mode_keeps_non_sensitive_keys(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.set_privacy_mode(True)
        logger.log_tool_call("api_tool", {"model": "qwen-max"}, "executed")
        events = logger.recent()
        # model is not a sensitive key, so it passes through
        assert events[0]["details"]["model"] == "qwen-max"

    def test_privacy_mode_redacts_credential_keys(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.set_privacy_mode(True)
        logger.log_tool_call(
            "api_tool",
            {"api_key": "sk-" + "a" * 50},
            "executed",
        )
        events = logger.recent()
        assert events[0]["details"]["api_key"] == "…(redacted)"

    def test_privacy_mode_off_does_not_redact(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.set_privacy_mode(False)
        long_content = "a" * 50
        logger.log_tool_call(
            "write_file",
            {"content": long_content},
            "executed",
        )
        events = logger.recent()
        assert events[0]["details"]["content"] == "a" * 50

    def test_privacy_mode_can_be_toggled(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        long_val = "x" * 50

        # First call with privacy on -> redacted
        logger.set_privacy_mode(True)
        logger.log_tool_call("tool1", {"content": long_val}, "executed")

        # Second call with privacy off -> not redacted
        logger.set_privacy_mode(False)
        logger.log_tool_call("tool2", {"content": long_val}, "executed")

        events = logger.recent(limit=20)
        assert events[0]["details"]["content"] == "x" * 20 + "…(redacted)"
        assert events[1]["details"]["content"] == "x" * 50

    def test_privacy_mode_always_keeps_tool_name_and_decision(self, tmp_path):
        logger = AuditLogger(tmp_path / "audit.log")
        logger.set_privacy_mode(True)
        logger.log_tool_call(
            "dangerous_tool",
            {"content": "x" * 50},
            "denied",
            "blocked",
        )
        events = logger.recent()
        assert events[0]["subject"] == "dangerous_tool"
        assert events[0]["decision"] == "denied"
        assert events[0]["reason"] == "blocked"


# ── Global logger ───────────────────────────────────────────────────────


class TestGlobalLogger:
    def test_get_audit_logger_returns_instance(self):
        logger = get_audit_logger()
        assert isinstance(logger, AuditLogger)

    def test_set_audit_logger_replaces_global(self):
        original = get_audit_logger()
        try:
            custom = AuditLogger(Path("/tmp/test_audit_global.log"))
            set_audit_logger(custom)
            assert get_audit_logger() is custom
        finally:
            set_audit_logger(original)

    def test_get_audit_logger_returns_same_instance(self):
        assert get_audit_logger() is get_audit_logger()


# ── M5a: startup privacy wiring ───────────────────────────────────────────


class TestConfigureAuditLogger:
    def test_wiring_passes_privacy_flag(self):
        from dashscope_agenticx.audit import configure_audit_logger

        logger = get_audit_logger()
        original = logger._privacy_mode
        try:
            returned = configure_audit_logger(
                SimpleNamespace(privacy_mode=True),
            )
            assert returned is logger
            assert logger._privacy_mode is True

            configure_audit_logger(SimpleNamespace(privacy_mode=False))
            assert logger._privacy_mode is False

            # Missing attribute defaults to off (defensive getattr).
            configure_audit_logger(SimpleNamespace())
            assert logger._privacy_mode is False
        finally:
            logger.set_privacy_mode(original)

    def test_wired_logger_redacts_after_restart(self, tmp_path):
        """Simulate a restart: fresh global logger armed via the wiring with
        privacy enabled must redact sensitive tool args."""
        from dashscope_agenticx.audit import configure_audit_logger

        original = get_audit_logger()
        logger = AuditLogger(tmp_path / "audit.log")
        set_audit_logger(logger)
        try:
            configure_audit_logger(SimpleNamespace(privacy_mode=True))
            get_audit_logger().log_tool_call(
                "write_file",
                {"content": "x" * 50},
                "executed",
            )
            events = logger.recent()
            assert events[0]["details"]["content"] == "x" * 20 + "…(redacted)"
        finally:
            set_audit_logger(original)


# ── M5b: /audit recent N parsing ──────────────────────────────────────────


class TestAuditSlashCommand:
    @pytest.fixture
    def tmp_logger(self, tmp_path):
        original = get_audit_logger()
        logger = AuditLogger(tmp_path / "audit.log")
        set_audit_logger(logger)
        yield logger
        set_audit_logger(original)

    def _run(self, cmd: str, monkeypatch):
        from unittest.mock import MagicMock

        from dashscope_agenticx.cli.dispatch import _handle_slash_command

        console = MagicMock()
        monkeypatch.setattr("dashscope_agenticx.cli.dispatch.console", console)
        result = _handle_slash_command(
            cmd,
            SimpleNamespace(),
            SimpleNamespace(),
        )
        assert result is True
        return console

    def test_audit_recent_with_count(self, tmp_logger, monkeypatch):
        for i in range(60):
            tmp_logger.log_tool_call(f"tool_{i}", {}, "executed")
        console = self._run("/audit recent 50", monkeypatch)
        header = console.print.call_args_list[0].args[0]
        assert "(50 entries)" in header

    def test_audit_recent_default_count(self, tmp_logger, monkeypatch):
        for i in range(60):
            tmp_logger.log_tool_call(f"tool_{i}", {}, "executed")
        console = self._run("/audit recent", monkeypatch)
        header = console.print.call_args_list[0].args[0]
        assert "(20 entries)" in header

    def test_audit_bare_defaults_to_recent(self, tmp_logger, monkeypatch):
        tmp_logger.log_tool_call("tool_0", {}, "executed")
        console = self._run("/audit", monkeypatch)
        header = console.print.call_args_list[0].args[0]
        assert "(1 entries)" in header

    def test_audit_recent_bad_count_graceful(self, tmp_logger, monkeypatch):
        console = self._run("/audit recent abc", monkeypatch)
        msg = console.print.call_args_list[0].args[0]
        assert "Usage" in msg

    def test_audit_recent_nonpositive_count_graceful(
        self,
        tmp_logger,
        monkeypatch,
    ):
        console = self._run("/audit recent 0", monkeypatch)
        msg = console.print.call_args_list[0].args[0]
        assert "Usage" in msg
