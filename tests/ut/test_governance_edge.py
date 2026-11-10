# -*- coding: utf-8 -*-
"""Edge-case tests for the governance module:
permission policy specificity/loading, audit redaction depth, executor
policy gaps, and agent-side hook-denial audit trail."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashscope_agenticx.executor import classify_tool_text
from dashscope_agenticx.permission import (
    PermissionPolicy,
    configure_permission_policy,
    get_permission_policy,
    load_permissions_file,
    set_permission_policy,
)


@pytest.fixture
def restore_policy():
    original = get_permission_policy()
    yield
    set_permission_policy(original)


# ---------------------------------------------------------------------------
# permission.py — command-rule specificity and normalization
# ---------------------------------------------------------------------------


class TestCommandRuleSpecificity:
    def test_longest_prefix_wins_regardless_of_insertion_order(self):
        p = PermissionPolicy()
        p.add_command_rule("git", "allow")
        p.add_command_rule("git push", "deny")
        assert p.check_command("git push origin main") == "deny"
        assert p.check_command("git status") == "allow"

        p2 = PermissionPolicy()
        p2.add_command_rule("git push", "deny")
        p2.add_command_rule("git", "allow")
        assert p2.check_command("git push origin main") == "deny"
        assert p2.check_command("git status") == "allow"

    def test_three_levels_of_specificity(self):
        p = PermissionPolicy()
        p.add_command_rule("git", "allow")
        p.add_command_rule("git push", "confirm")
        p.add_command_rule("git push --force", "deny")
        assert p.check_command("git log") == "allow"
        assert p.check_command("git push origin main") == "confirm"
        assert p.check_command("git push --force origin main") == "deny"

    def test_whitespace_runs_normalized(self):
        p = PermissionPolicy()
        p.add_command_rule("git push", "deny")
        assert p.check_command("git\tpush origin main") == "deny"
        assert p.check_command("git   push origin main") == "deny"
        assert p.check_command("  git push origin main") == "deny"

    def test_prefix_stored_normalized(self):
        p = PermissionPolicy()
        p.add_command_rule("git\tpush  --force", "deny")
        assert p.check_command("git push --force") == "deny"

    def test_empty_prefix_rejected(self):
        p = PermissionPolicy()
        with pytest.raises(ValueError):
            p.add_command_rule("   ", "allow")

    def test_no_match_returns_none(self):
        p = PermissionPolicy()
        p.add_command_rule("git", "allow")
        assert p.check_command("curl example.com") is None


# ---------------------------------------------------------------------------
# permission.py — permissions.toml loading
# ---------------------------------------------------------------------------


class TestPermissionsFileLoad:
    def test_missing_file_is_noop(self, tmp_path):
        p = PermissionPolicy()
        assert load_permissions_file(p, tmp_path / "none.toml") == []
        assert p.tool_rules == {} and p.command_rules == {}

    def test_malformed_toml_is_noop(self, tmp_path):
        bad = tmp_path / "permissions.toml"
        bad.write_text("[tools\nbroken", encoding="utf-8")
        p = PermissionPolicy()
        assert load_permissions_file(p, bad) == []
        assert p.tool_rules == {}

    def test_valid_file_loads_both_sections(self, tmp_path):
        f = tmp_path / "permissions.toml"
        f.write_text(
            '[tools]\nwrite_file = "confirm"\ndelete_file = "deny"\n'
            '[commands]\n"git status" = "allow"\n"git push" = "deny"\n',
            encoding="utf-8",
        )
        p = PermissionPolicy()
        assert load_permissions_file(p, f) == []
        assert p.check_tool("write_file") == "confirm"
        assert p.check_tool("delete_file") == "deny"
        assert p.check_command("git status --short") == "allow"
        assert p.check_command("git push origin main") == "deny"

    def test_invalid_decision_collected_and_dropped(self, tmp_path):
        f = tmp_path / "permissions.toml"
        f.write_text(
            '[tools]\nwrite_file = "maybe"\nread_file = "allow"\n',
            encoding="utf-8",
        )
        p = PermissionPolicy()
        errors = load_permissions_file(p, f)
        assert len(errors) == 1 and "maybe" in errors[0]
        # A bad rule must fail closed (dropped), never default to allow.
        assert p.check_tool("write_file") is None
        assert p.check_tool("read_file") == "allow"

    def test_non_table_section_collected(self, tmp_path):
        f = tmp_path / "permissions.toml"
        f.write_text('tools = ["not", "a", "table"]\n', encoding="utf-8")
        p = PermissionPolicy()
        errors = load_permissions_file(p, f)
        assert len(errors) == 1

    def test_configure_merges_global_then_workspace(
        self,
        tmp_path,
        monkeypatch,
        restore_policy,
    ):
        global_dir = tmp_path / "global"
        workspace_dir = tmp_path / "ws"
        global_dir.mkdir()
        workspace_dir.mkdir()
        (global_dir / "permissions.toml").write_text(
            '[tools]\nwrite_file = "confirm"\ndelete_file = "deny"\n'
            '[commands]\n"git push" = "deny"\n',
            encoding="utf-8",
        )
        (workspace_dir / "permissions.toml").write_text(
            '[tools]\nwrite_file = "allow"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_DIR", global_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            workspace_dir,
        )

        policy = configure_permission_policy()
        assert get_permission_policy() is policy
        # Workspace wins on the shared key; global-only keys survive.
        assert policy.check_tool("write_file") == "allow"
        assert policy.check_tool("delete_file") == "deny"
        assert policy.check_command("git push origin main") == "deny"


# ---------------------------------------------------------------------------
# audit.py — redaction depth
# ---------------------------------------------------------------------------


class TestAuditCredentialRedaction:
    def test_short_credential_fully_redacted(self):
        from dashscope_agenticx.audit import _redact_args

        out = _redact_args({"api_key": "abc"})
        assert out["api_key"] == "…(redacted)"
        assert "abc" not in out["api_key"]

    @pytest.mark.parametrize(
        "key",
        [
            "api_key",
            "apikey",
            "key",
            "token",
            "secret",
            "password",
            "passwd",
            "authorization",
            "auth",
            "credential",
            "cookie",
        ],
    )
    def test_all_credential_keys_redacted(self, key):
        from dashscope_agenticx.audit import _redact_args

        assert _redact_args({key: "x"})[key] == "…(redacted)"

    def test_credential_keys_case_insensitive(self):
        from dashscope_agenticx.audit import _redact_args

        out = _redact_args({"API_KEY": "k", "Token": "t"})
        assert out["API_KEY"] == "…(redacted)"
        assert out["Token"] == "…(redacted)"

    def test_nested_dict_recursed(self):
        from dashscope_agenticx.audit import _redact_args

        out = _redact_args(
            {"headers": {"Authorization": "Bearer xyz", "x": 1}},
        )
        assert out["headers"]["Authorization"] == "…(redacted)"
        assert out["headers"]["x"] == 1

    def test_nested_content_key_truncated(self):
        from dashscope_agenticx.audit import _redact_args

        out = _redact_args({"opts": {"content": "c" * 30}})
        assert out["opts"]["content"] == "c" * 20 + "…(redacted)"

    def test_non_sensitive_sibling_untouched(self):
        from dashscope_agenticx.audit import _redact_args

        out = _redact_args({"model": "qwen-max", "n": 3})
        assert out == {"model": "qwen-max", "n": 3}

    def test_logger_end_to_end_privacy_redacts_credentials(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        logger = AuditLogger(path=tmp_path / "audit.log")
        logger.set_privacy_mode(True)
        logger.log_tool_call(
            "http_request",
            {"url": "https://example.com/this-is-a-long-url", "token": "t"},
            decision="executed",
        )
        record = json.loads((tmp_path / "audit.log").read_text().strip())
        assert record["details"]["token"] == "…(redacted)"
        assert record["details"]["url"].endswith("…(redacted)")

    def test_logger_privacy_off_keeps_values(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        logger = AuditLogger(path=tmp_path / "audit.log")
        logger.log_tool_call(
            "t",
            {"token": "t", "command": "ls"},
            decision="executed",
        )
        record = json.loads((tmp_path / "audit.log").read_text().strip())
        assert record["details"]["token"] == "t"
        assert record["details"]["command"] == "ls"

    def test_configure_audit_logger_rearms_privacy(self):
        from dashscope_agenticx.audit import (
            configure_audit_logger,
            get_audit_logger,
        )

        original = get_audit_logger()._privacy_mode
        try:
            configure_audit_logger(SimpleNamespace(privacy_mode=True))
            assert get_audit_logger()._privacy_mode is True
            configure_audit_logger(SimpleNamespace(privacy_mode=False))
            assert get_audit_logger()._privacy_mode is False
            configure_audit_logger(SimpleNamespace())  # missing attr → False
            assert get_audit_logger()._privacy_mode is False
        finally:
            get_audit_logger().set_privacy_mode(original)


# ---------------------------------------------------------------------------
# executor.py — policy allow/confirm paths and specificity integration
# ---------------------------------------------------------------------------


class TestExecutorPolicyPaths:
    @pytest.fixture
    def policy(self, restore_policy):
        p = PermissionPolicy()
        set_permission_policy(p)
        return p

    async def test_command_allow_autopasses_non_readonly(
        self,
        policy,
        make_tool,
        make_executor,
    ):
        policy.add_command_rule("make deploy", "allow")
        executor = make_executor()
        executor._confirm_callback = AsyncMock()
        tool = make_tool(name="run_command")
        ok = await executor._async_check_permission(
            tool,
            {"command": "make deploy prod"},
        )
        assert ok is True
        executor._confirm_callback.assert_not_called()

    async def test_tool_confirm_rule_reaches_callback(
        self,
        policy,
        make_tool,
        make_executor,
    ):
        from dashscope_agenticx.tools.registry import PermissionLevel

        policy.add_tool_rule("write_file", "confirm")
        executor = make_executor()
        executor._confirm_callback = AsyncMock(return_value="y")
        tool = make_tool(name="write_file", permission=PermissionLevel.CONFIRM)
        ok = await executor._async_check_permission(tool, {"path": "x"})
        assert ok is True
        executor._confirm_callback.assert_called_once()

    async def test_command_confirm_rule_reaches_callback(
        self,
        policy,
        make_tool,
        make_executor,
    ):
        policy.add_command_rule("make", "confirm")
        executor = make_executor()
        executor._confirm_callback = AsyncMock(return_value="n")
        tool = make_tool(name="run_command")
        ok = await executor._async_check_permission(
            tool,
            {"command": "make all"},
        )
        assert ok is False
        executor._confirm_callback.assert_called_once()

    async def test_specific_deny_overrides_broad_allow_on_executor(
        self,
        policy,
        make_tool,
        make_executor,
    ):
        policy.add_command_rule("git", "allow")
        policy.add_command_rule("git push", "deny")
        executor = make_executor()
        executor._confirm_callback = AsyncMock(return_value="y")
        tool = make_tool(name="run_command")
        assert (
            await executor._async_check_permission(
                tool,
                {"command": "git push origin main"},
            )
            is False
        )
        executor._confirm_callback.assert_not_called()
        assert (
            await executor._async_check_permission(
                tool,
                {"command": "git status"},
            )
            is True
        )

    async def test_callback_update_without_supplement_aborts(
        self,
        policy,
        make_tool,
        make_executor,
    ):
        from dashscope_agenticx.tools.registry import PermissionLevel
        from dashscope_agenticx.utils.exceptions import UserAbortedTurn

        executor = make_executor()
        executor._confirm_callback = AsyncMock(return_value="u")
        tool = make_tool(name="write_file", permission=PermissionLevel.CONFIRM)
        with pytest.raises(UserAbortedTurn):
            await executor._async_check_permission(tool, {"path": "x"})


# ---------------------------------------------------------------------------
# executor.py — sync interactive prompt path (y/n/u/a/s protocol)
# ---------------------------------------------------------------------------


class TestSyncCheckPermission:
    def _tool(self, make_tool, name="write_file", dangerous=False):
        from dashscope_agenticx.tools.registry import PermissionLevel

        return make_tool(
            name=name,
            permission=(
                PermissionLevel.DANGEROUS
                if dangerous
                else PermissionLevel.CONFIRM
            ),
        )

    def test_dangerous_confirm_yes(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Confirm.ask",
            lambda *a, **kw: True,
        )
        executor = make_executor()
        assert (
            executor._check_permission(
                self._tool(make_tool, dangerous=True),
                {"path": "x"},
            )
            is True
        )

    def test_dangerous_confirm_no(self, make_tool, make_executor, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Confirm.ask",
            lambda *a, **kw: False,
        )
        executor = make_executor()
        assert (
            executor._check_permission(
                self._tool(make_tool, dangerous=True),
                {"path": "x"},
            )
            is False
        )

    def test_dangerous_keyboard_interrupt_aborts(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        from dashscope_agenticx.utils.exceptions import UserAbortedTurn

        def _raise(*a, **kw):
            raise KeyboardInterrupt

        monkeypatch.setattr("dashscope_agenticx.executor.Confirm.ask", _raise)
        executor = make_executor()
        with pytest.raises(UserAbortedTurn):
            executor._check_permission(
                self._tool(make_tool, dangerous=True),
                {},
            )

    def test_confirm_always_adds_trust_cache(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Prompt.ask",
            lambda *a, **kw: "a",
        )
        executor = make_executor()
        tool = self._tool(make_tool)
        assert executor._check_permission(tool, {"path": "x"}) is True
        assert "write_file" in executor._always_allow
        # Second call short-circuits on the cache (no prompt needed).
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Prompt.ask",
            lambda *a, **kw: pytest.fail("should not prompt again"),
        )
        assert executor._check_permission(tool, {"path": "y"}) is True

    def test_confirm_stop_aborts_turn(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        from dashscope_agenticx.utils.exceptions import UserAbortedTurn

        monkeypatch.setattr(
            "dashscope_agenticx.executor.Prompt.ask",
            lambda *a, **kw: "s",
        )
        executor = make_executor()
        with pytest.raises(UserAbortedTurn):
            executor._check_permission(self._tool(make_tool), {})

    def test_confirm_update_with_text_raises_supplement(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        from dashscope_agenticx.utils.exceptions import UserSupplement

        answers = iter(["u", "请改用 ripgrep"])
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Prompt.ask",
            lambda *a, **kw: next(answers),
        )
        executor = make_executor()
        with pytest.raises(UserSupplement, match="ripgrep"):
            executor._check_permission(self._tool(make_tool), {})

    def test_confirm_update_empty_proceeds(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        answers = iter(["u", "  "])
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Prompt.ask",
            lambda *a, **kw: next(answers),
        )
        executor = make_executor()
        assert executor._check_permission(self._tool(make_tool), {}) is True

    def test_confirm_no_returns_false(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Prompt.ask",
            lambda *a, **kw: "n",
        )
        executor = make_executor()
        assert executor._check_permission(self._tool(make_tool), {}) is False

    def test_confirm_keyboard_interrupt_aborts(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        from dashscope_agenticx.utils.exceptions import UserAbortedTurn

        def _raise(*a, **kw):
            raise EOFError

        monkeypatch.setattr("dashscope_agenticx.executor.Prompt.ask", _raise)
        executor = make_executor()
        with pytest.raises(UserAbortedTurn):
            executor._check_permission(self._tool(make_tool), {})

    def test_sync_policy_command_allow_skips_prompt(
        self,
        make_tool,
        make_executor,
        monkeypatch,
        restore_policy,
    ):
        policy = PermissionPolicy()
        policy.add_command_rule("make deploy", "allow")
        set_permission_policy(policy)
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Prompt.ask",
            lambda *a, **kw: pytest.fail("policy allow must not prompt"),
        )
        executor = make_executor()
        tool = make_tool(name="run_command")
        assert (
            executor._check_permission(tool, {"command": "make deploy prod"})
            is True
        )

    def test_sync_readonly_autopass(
        self,
        make_tool,
        make_executor,
        monkeypatch,
    ):
        monkeypatch.setattr(
            "dashscope_agenticx.executor.Prompt.ask",
            lambda *a, **kw: pytest.fail("read-only must not prompt"),
        )
        executor = make_executor()
        tool = make_tool(name="run_command")
        assert (
            executor._check_permission(tool, {"command": "git status"}) is True
        )

    def test_get_stats_and_record_api_call(self, make_executor):
        executor = make_executor()
        executor.record_api_call({"input_tokens": 3, "output_tokens": 4})
        executor.record_api_call({"input_tokens": 1, "output_tokens": 1})
        executor.record_error()
        stats = executor.get_stats()
        assert stats["api_calls"] == 2
        assert stats["token_usage"]["input_tokens"] == 4
        assert stats["token_usage"]["output_tokens"] == 5
        assert stats["errors"] == 1

    def test_record_skills(self, make_executor):
        executor = make_executor()
        executor.record_skills(["text-generation", "cli"])
        executor.record_skills(["text-generation"])
        executor.record_skills([])
        stats = executor.get_stats()
        assert stats["skill_calls"] == 3
        assert stats["skill_counts"] == {"text-generation": 2, "cli": 1}

    def test_record_prompt_composition(self, make_executor):
        executor = make_executor()
        executor.record_prompt_composition(
            [
                {"role": "system", "content": "abcd"},
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "ok",
                    "tool_calls": [
                        {"function": {"name": "f", "arguments": '{"x": 1}'}},
                    ],
                },
                {"role": "tool", "content": "result!"},
                {"role": "user", "content": [{"type": "text", "text": "img"}]},
            ],
            tools_schema=[{"name": "f"}],
        )
        executor.record_prompt_composition([{"role": "user", "content": "zz"}])
        comp = executor.get_stats()["prompt_composition"]
        assert comp["system"] == 4
        assert comp["user"] == 2 + len('[{"type": "text", "text": "img"}]') + 2
        assert comp["assistant"] == 2
        assert comp["tools"] == (len('{"x": 1}') + 7 + len('[{"name": "f"}]'))


# ---------------------------------------------------------------------------
# audit.py — remaining logger branches
# ---------------------------------------------------------------------------


class TestAuditLoggerBranches:
    def test_privacy_mode_redacts_user_subject(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        logger = AuditLogger(path=tmp_path / "audit.log")
        logger.set_privacy_mode(True)
        logger.log_user_input("x" * 100)
        record = json.loads((tmp_path / "audit.log").read_text().strip())
        assert record["source"] == "user"
        assert record["subject"] == "x" * 20 + "…(redacted)"

    def test_log_cron_event(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        logger = AuditLogger(path=tmp_path / "audit.log")
        logger.log_cron("job-1", "skipped", "隐私模式")
        record = json.loads((tmp_path / "audit.log").read_text().strip())
        assert record["source"] == "scheduler"
        assert record["action"] == "cron"
        assert record["subject"] == "job-1"
        assert record["decision"] == "skipped"

    def test_log_io_error_never_raises(self, tmp_path):
        from dashscope_agenticx.audit import AuditEvent, AuditLogger

        blocker = tmp_path / "blocker"
        blocker.write_text("file, not a dir")
        logger = AuditLogger(path=blocker / "audit.log")  # mkdir must fail
        logger.log(AuditEvent.now("system", "test", "s", "auto"))  # no raise

    def test_recent_skips_malformed_lines(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        path = tmp_path / "audit.log"
        path.write_text(
            '{"source": "user", "action": "input", "subject": "a", "decision": "received"}\n'
            "not json\n\n"
            '{"source": "agent", "action": "tool_call", "subject": "t", "decision": "executed"}\n',
            encoding="utf-8",
        )
        logger = AuditLogger(path=path)
        records = logger.recent(limit=10)
        assert len(records) == 2
        assert records[0]["subject"] == "a"

    def test_recent_missing_file_returns_empty(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        assert AuditLogger(path=tmp_path / "none.log").recent() == []

    def test_query_subject_and_limit_filters(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        logger = AuditLogger(path=tmp_path / "audit.log")
        for i in range(5):
            logger.log_tool_call(f"tool_{i % 2}", {}, decision="executed")
        assert len(logger.query(subject="tool_0")) == 3
        assert len(logger.query(limit=2)) == 2
        assert len(logger.query(source="agent", decision="executed")) == 5
        assert logger.query(source="user") == []

    def test_query_missing_file_returns_empty(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        assert AuditLogger(path=tmp_path / "none.log").query() == []

    def test_clear_removes_file_and_tolerates_missing(self, tmp_path):
        from dashscope_agenticx.audit import AuditLogger

        path = tmp_path / "audit.log"
        logger = AuditLogger(path=path)
        logger.log_tool_call("t", {}, decision="executed")
        assert path.exists()
        logger.clear()
        assert not path.exists()
        logger.clear()  # missing again — no raise

    def test_set_and_get_global_logger(self):
        from dashscope_agenticx.audit import (
            AuditLogger,
            get_audit_logger,
            set_audit_logger,
        )

        original = get_audit_logger()
        try:
            custom = AuditLogger(path=Path("/tmp/x/audit.log"))
            set_audit_logger(custom)
            assert get_audit_logger() is custom
        finally:
            set_audit_logger(original)


# ---------------------------------------------------------------------------
# agent.py — hook denials must land in the audit trail
# ---------------------------------------------------------------------------


class TestAgentHookAudit:
    @pytest.fixture
    def audit_logger(self, tmp_path):
        from dashscope_agenticx.audit import (
            AuditLogger,
            get_audit_logger,
            set_audit_logger,
        )

        original = get_audit_logger()
        logger = AuditLogger(path=tmp_path / "audit.log")
        set_audit_logger(logger)
        yield logger
        set_audit_logger(original)

    def _make_agent(self, hook_bus, confirm_result=True):
        from dashscope_agenticx.agent import Agent

        provider = MagicMock()
        executor = MagicMock()
        executor._async_check_permission = AsyncMock(
            return_value=confirm_result,
        )

        async def _fake_execute(*args, **kwargs):
            return classify_tool_text("ok")

        executor.execute_detailed = MagicMock(side_effect=_fake_execute)
        executor.clear_session_trust = MagicMock()
        return Agent(
            provider=provider,
            executor=executor,
            hook_bus=hook_bus,
            session_path=Path(
                "/tmp/nonexistent/.agenticx/session/history.json",
            ),
        )

    async def test_hook_block_is_audited(self, audit_logger):
        from dashscope_agenticx.hooks import Hook, HookBus
        from dashscope_agenticx.providers.base import ToolCall
        from dashscope_agenticx.tools.registry import (
            PermissionLevel,
            ToolDefinition,
            registry,
        )

        original = registry.get("delete_file")
        registry.register(
            ToolDefinition(
                name="delete_file",
                description="delete",
                permission=PermissionLevel.AUTO,
                func=lambda path: "deleted",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
        )
        try:
            bus = HookBus()
            bus.register(
                Hook(
                    "before_tool_call",
                    {
                        "match": "delete_file",
                        "action": "block",
                        "message": "no",
                    },
                ),
            )
            agent = self._make_agent(bus)
            result = await agent._execute_tool(
                ToolCall(id="1", name="delete_file", arguments={"path": "x"}),
            )
            assert "Blocked" in result
            agent.executor.execute_detailed.assert_not_called()
        finally:
            if original is None:
                registry.unregister("delete_file")
            else:
                registry.register(original)

        events = audit_logger.query(action="tool_call", decision="denied")
        assert len(events) == 1
        assert events[0]["subject"] == "delete_file"
        assert "hook" in events[0]["reason"]

    async def test_hook_confirm_declined_is_audited(self, audit_logger):
        from dashscope_agenticx.hooks import Hook, HookBus
        from dashscope_agenticx.providers.base import ToolCall
        from dashscope_agenticx.tools.registry import (
            PermissionLevel,
            ToolDefinition,
            registry,
        )

        original = registry.get("read_file")
        registry.register(
            ToolDefinition(
                name="read_file",
                description="read",
                permission=PermissionLevel.AUTO,
                func=lambda path: "data",
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
        )
        try:
            bus = HookBus()
            bus.register(
                Hook(
                    "before_tool_call",
                    {"match": "read_file", "action": "confirm"},
                ),
            )
            agent = self._make_agent(bus, confirm_result=False)
            result = await agent._execute_tool(
                ToolCall(id="1", name="read_file", arguments={"path": "x"}),
            )
            assert result == "Cancelled"
            agent.executor.execute_detailed.assert_not_called()
        finally:
            if original is None:
                registry.unregister("read_file")
            else:
                registry.register(original)

        events = audit_logger.query(action="tool_call", decision="denied")
        assert len(events) == 1
        assert events[0]["subject"] == "read_file"
        assert events[0]["reason"] == "hook confirm declined"


# ---------------------------------------------------------------------------
# hooks.py — remaining action/loading gaps
# ---------------------------------------------------------------------------


class TestHookRemainingGaps:
    def test_run_failure_alert_escalates(self):
        from dashscope_agenticx.hooks import Hook, HookContext

        hook = Hook(
            "after_tool_call",
            {
                "match": "*",
                "action": "run",
                "run": "exit 3",
                "on_fail": "alert",
            },
        )
        result = hook.execute(
            HookContext(event="after_tool_call", tool_name="t"),
        )
        assert result.alert and "exit 3" in result.alert
        assert not result.blocked

    def test_silent_run_produces_no_log(self):
        from dashscope_agenticx.hooks import Hook, HookContext

        hook = Hook(
            "after_tool_call",
            {"match": "*", "action": "run", "run": "echo hi", "silent": True},
        )
        result = hook.execute(
            HookContext(event="after_tool_call", tool_name="t"),
        )
        assert result.output == "hi"
        assert result.log == ""

    def test_unknown_action_is_noop(self):
        from dashscope_agenticx.hooks import Hook, HookContext

        hook = Hook("before_tool_call", {"match": "*", "action": "explode"})
        result = hook.execute(
            HookContext(event="before_tool_call", tool_name="t"),
        )
        assert not result.blocked and not result.confirm and not result.output

    def test_malformed_toml_entries_skipped(self, tmp_path):
        from dashscope_agenticx.hooks import HookBus

        f = tmp_path / "hooks.toml"
        f.write_text(
            '[hooks]\nbefore_tool_call = "not-a-list"\n'
            '[[hooks.after_tool_call]]\naction = "log"\n'
            '[[hooks.after_tool_call]]\n"just a string" = true\n',
            encoding="utf-8",
        )
        # Second after_tool_call entry is a dict here (TOML table array), so
        # use a non-dict via inline list instead:
        f.write_text(
            '[hooks]\nbefore_tool_call = "not-a-list"\n'
            "after_tool_call = [1, 2]\n"
            '[[hooks.on_error]]\naction = "log"\nmessage = "m"\n',
            encoding="utf-8",
        )
        bus = HookBus(hooks_file=f)
        assert len(bus.hooks) == 1
        assert bus.hooks[0].event == "on_error"

    def test_exit_code_extracted_from_run_command_result(self):
        from dashscope_agenticx.hooks import HookContext, _build_variables

        ctx = HookContext(
            event="after_tool_call",
            tool_name="run_command",
            arguments={"command": "false"},
            result="exit code: 1\nsome output",
            success=False,
        )
        assert _build_variables(ctx)["exit_code"] == "1"

    def test_dispatch_confirm_does_not_block(self):
        from dashscope_agenticx.hooks import Hook, HookBus, HookContext

        bus = HookBus(hooks_file=Path("/nonexistent/hooks.toml"))
        bus.register(
            Hook("before_tool_call", {"match": "*", "action": "confirm"}),
        )
        result = bus.dispatch(
            HookContext(event="before_tool_call", tool_name="t"),
        )
        assert result.confirm is True
        assert result.blocked is False
