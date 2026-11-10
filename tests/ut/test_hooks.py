# -*- coding: utf-8 -*-
"""Tests for the hooks system."""

from __future__ import annotations

import shlex
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from dashscope_agenticx.executor import classify_tool_text
from dashscope_agenticx.hooks import (
    Hook,
    HookBus,
    HookContext,
    _build_variables,
    _match_condition,
    _prepare_run_command,
)
from dashscope_agenticx.utils.template import render_brace_template


class TestTemplateVariables:
    def test_build_variables_for_write_file(self):
        ctx = HookContext(
            event="after_tool_call",
            tool_name="write_file",
            arguments={"path": "src/auth.py", "content": "print('hi')"},
        )
        variables = _build_variables(ctx)
        assert variables["path"] == "src/auth.py"
        assert variables["filename"] == "auth.py"
        assert variables["filename_stem"] == "auth"
        assert "print" in variables["content"]

    def test_build_variables_for_run_command(self):
        ctx = HookContext(
            event="after_tool_call",
            tool_name="run_command",
            arguments={"command": "ls"},
            result="exit code: 1\nerror",
            success=False,
        )
        variables = _build_variables(ctx)
        assert variables["exit_code"] == "1"

    def test_render_brace_template(self):
        variables = {"path": "src/auth.py", "filename": "auth.py"}
        assert (
            render_brace_template("format {path}", variables)
            == "format src/auth.py"
        )


class TestSafeRunCommands:
    @pytest.mark.parametrize("asynchronous", [False, True])
    @pytest.mark.parametrize("array_template", [False, True])
    @pytest.mark.parametrize(
        "path",
        [
            "safe.py; printf __HOOK_INJECTED__",
            'space and "quotes\'".py',
            "$(printf injected)`printf injected` & other.py",
            "line\nbreak.py",
            "{filename}.py",
            r"C:\folder with spaces\file.py",
        ],
    )
    async def test_arguments_are_literal(
        self,
        path,
        array_template,
        asynchronous,
    ):
        arguments = [
            sys.executable,
            "-c",
            "import sys; print(sys.argv[1], end='')",
            "{path}",
        ]
        template = (
            arguments
            if array_template
            else " ".join(shlex.quote(argument) for argument in arguments)
        )
        hook = Hook("after_tool_call", {"action": "run", "run": template})
        context = HookContext(
            event="after_tool_call",
            tool_name="write_file",
            arguments={"path": path},
        )
        result = (
            await hook.execute_async(context)
            if asynchronous
            else hook.execute(context)
        )
        assert not result.warning
        assert result.output == path

    @pytest.mark.parametrize(
        "template",
        [
            "echo {path} > output.txt",
            "echo {path} | another-command",
            "echo {path}; another-command",
            "echo {path} && another-command",
            "echo '{path}",
            ["{path}", "argument"],
            ["python", 1],
            ["cmd.exe", "/c", "echo", "{path}"],
            ["sh", "-c", "{path}"],
            ["powershell.exe", "-Command", "{path}"],
            ["python", "-c", "print('{path}')"],
            ["python", "-m", "{path}"],
            ["python", "-cprint('{path}')"],
            ["python", "-Icmodule({path})"],
            ["python", "-m{path}"],
            ["bash.exe", "-c", "{path}"],
        ],
    )
    @pytest.mark.parametrize("asynchronous", [False, True])
    async def test_unsafe_templates_fail_closed(
        self,
        template,
        asynchronous,
        monkeypatch,
    ):
        import dashscope_agenticx.hooks as hooks_mod

        def unexpected_process(*arguments, **keywords):
            pytest.fail("invalid templates must not start a process")

        monkeypatch.setattr(hooks_mod.subprocess, "run", unexpected_process)
        monkeypatch.setattr(
            hooks_mod.asyncio,
            "create_subprocess_exec",
            unexpected_process,
        )
        hook = Hook(
            "after_tool_call",
            {"action": "run", "run": template, "on_fail": "block"},
        )
        context = HookContext(
            event="after_tool_call",
            tool_name="write_file",
            arguments={"path": "safe.py"},
        )
        result = (
            await hook.execute_async(context)
            if asynchronous
            else hook.execute(context)
        )
        assert result.blocked
        assert not result.output

    def test_windows_batch_templates_are_rejected(self, monkeypatch):
        from types import SimpleNamespace

        import dashscope_agenticx.hooks as hooks_mod

        monkeypatch.setattr(
            hooks_mod,
            "os",
            SimpleNamespace(name="nt", path=hooks_mod.os.path),
        )
        for executable in ("hook.bat", "hook.CMD"):
            with pytest.raises(ValueError, match="native executable"):
                _prepare_run_command(
                    [executable, "{path}"],
                    {"path": "file & command.py"},
                )

    def test_static_shell_scripts_remain_explicit(self):
        assert _prepare_run_command("echo hello > output.txt", {}) == (
            "echo hello > output.txt"
        )

    def test_unknown_variables_are_literal(self):
        assert _prepare_run_command(
            ["python", "{unknown}", "{path}"],
            {"path": "{unknown}"},
        ) == ["python", "{unknown}", "{unknown}"]


class TestConditionMatching:
    def test_fnmatch_path(self):
        assert _match_condition("src/*.py", "src/auth.py")
        assert not _match_condition("src/*.py", "src/auth.md")

    def test_fnmatch_filename(self):
        assert _match_condition("*.py", "src/auth.py")

    def test_content_pattern_on_error(self):
        hook = Hook(
            "on_error",
            {
                "match": "*",
                "content_pattern": "*ModuleNotFoundError*",
                "action": "warn",
                "message": "missing module",
            },
        )
        ctx_match = HookContext(
            event="on_error",
            tool_name="run_command",
            arguments={},
            result="ModuleNotFoundError: No module named 'dashscope'",
        )
        ctx_no_match = HookContext(
            event="on_error",
            tool_name="run_command",
            arguments={},
            result="some other error",
        )
        assert hook.applies_to(ctx_match)
        assert not hook.applies_to(ctx_no_match)

    def test_content_pattern_before_tool_call(self):
        hook = Hook(
            "before_tool_call",
            {
                "match": "run_command",
                "content_pattern": "*pip*install*",
                "action": "confirm",
                "message": "confirm pip",
            },
        )
        ctx_match = HookContext(
            event="before_tool_call",
            tool_name="run_command",
            arguments={"command": "pip install dashscope"},
        )
        ctx_no_match = HookContext(
            event="before_tool_call",
            tool_name="run_command",
            arguments={"command": "ls"},
        )
        assert hook.applies_to(ctx_match)
        assert not hook.applies_to(ctx_no_match)


class TestHookExecution:
    def test_block_action(self):
        hook = Hook(
            "before_tool_call",
            {
                "match": "delete_file",
                "action": "block",
                "message": "no delete",
            },
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="delete_file",
            arguments={"path": "x"},
        )
        result = hook.execute(ctx)
        assert result.blocked
        assert "no delete" in result.warning

    def test_warn_action(self):
        hook = Hook(
            "before_tool_call",
            {"match": "*", "action": "warn", "message": "careful"},
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = hook.execute(ctx)
        assert not result.blocked
        assert result.warning == "careful"

    def test_run_action_success(self):
        hook = Hook(
            "after_tool_call",
            {"match": "write_file", "action": "run", "run": "echo hello"},
        )
        ctx = HookContext(
            event="after_tool_call",
            tool_name="write_file",
            arguments={"path": "x"},
        )
        result = hook.execute(ctx)
        assert result.output == "hello"

    def test_run_action_failure_warn(self):
        hook = Hook(
            "after_tool_call",
            {
                "match": "write_file",
                "action": "run",
                "run": "exit 1",
                "on_fail": "warn",
            },
        )
        ctx = HookContext(
            event="after_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = hook.execute(ctx)
        assert not result.blocked
        assert "failed" in result.warning

    def test_run_action_failure_block(self):
        hook = Hook(
            "after_tool_call",
            {
                "match": "write_file",
                "action": "run",
                "run": "exit 1",
                "on_fail": "block",
            },
        )
        ctx = HookContext(
            event="after_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = hook.execute(ctx)
        assert result.blocked


class TestHookBus:
    def test_load_from_toml(self, tmp_path):
        hooks_file = tmp_path / "hooks.toml"
        hooks_file.write_text(
            "[[hooks.before_tool_call]]\n"
            'match = "delete_file"\n'
            'action = "block"\n'
            'message = "blocked"\n',
            encoding="utf-8",
        )
        bus = HookBus(hooks_file)
        assert len(bus.hooks) == 1
        assert bus.hooks[0].event == "before_tool_call"

    def test_missing_toml_is_noop(self, tmp_path):
        bus = HookBus(tmp_path / "hooks.toml")
        assert bus.hooks == []

    def test_dispatch_stops_at_block(self):
        bus = HookBus()
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "block", "message": "first"},
            ),
        )
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "warn", "message": "second"},
            ),
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = bus.dispatch(ctx)
        assert result.blocked
        assert len(result.warnings) == 1
        assert result.warnings[0] == "first"


class TestHookCategory:
    """Tests for the new ``category`` field on Hook."""

    def test_block_action_is_governance(self):
        hook = Hook("before_tool_call", {"match": "*", "action": "block"})
        assert hook.category == "governance"

    def test_confirm_action_is_governance(self):
        hook = Hook("before_tool_call", {"match": "*", "action": "confirm"})
        assert hook.category == "governance"

    def test_alert_action_is_governance(self):
        hook = Hook("after_tool_call", {"match": "*", "action": "alert"})
        assert hook.category == "governance"

    def test_all_governance_actions(self):
        for action in ("block", "confirm", "alert"):
            hook = Hook("before_tool_call", {"match": "*", "action": action})
            assert (
                hook.category == "governance"
            ), f"{action} should be governance"

    def test_run_action_is_plugin(self):
        hook = Hook(
            "after_tool_call",
            {"match": "*", "action": "run", "run": "echo hi"},
        )
        assert hook.category == "plugin"

    def test_warn_action_is_plugin(self):
        hook = Hook("before_tool_call", {"match": "*", "action": "warn"})
        assert hook.category == "plugin"

    def test_log_action_is_plugin(self):
        hook = Hook("after_tool_call", {"match": "*", "action": "log"})
        assert hook.category == "plugin"

    def test_all_plugin_actions(self):
        for action in ("run", "warn", "log"):
            hook = Hook("before_tool_call", {"match": "*", "action": action})
            assert hook.category == "plugin", f"{action} should be plugin"

    def test_unknown_action_is_plugin(self):
        """Unknown actions default to the plugin category."""
        hook = Hook("before_tool_call", {"match": "*", "action": "foobar"})
        assert hook.category == "plugin"

    def test_default_action_is_run_which_is_plugin(self):
        """When no action is specified, it defaults to 'run' (plugin)."""
        hook = Hook("before_tool_call", {"match": "*"})
        assert hook.action == "run"
        assert hook.category == "plugin"


class TestGovernanceFirstDispatch:
    """Tests for governance-first dispatch ordering in HookBus.dispatch."""

    def test_governance_block_runs_before_plugin_warn(self):
        """A governance block hook registered AFTER a plugin warn hook should
        still run first and prevent the warn from executing."""
        bus = HookBus()
        # Plugin hook registered first
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "warn", "message": "plugin warn"},
            ),
        )
        # Governance hook registered second
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "block", "message": "gov block"},
            ),
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = bus.dispatch(ctx)
        assert result.blocked
        # Only the governance block warning; plugin warn was never reached
        assert len(result.warnings) == 1
        assert result.warnings[0] == "gov block"

    def test_governance_alert_runs_before_plugin_warn_order(self):
        """When neither hook blocks, governance (alert) should execute before
        plugin (warn) even though the plugin was registered first."""
        bus = HookBus()
        execution_order = []

        gov_hook = Hook(
            "before_tool_call",
            {"match": "*", "action": "alert", "message": "gov"},
        )
        plug_hook = Hook(
            "before_tool_call",
            {"match": "*", "action": "warn", "message": "plug"},
        )

        # Wrap execute to track call order
        _gov_execute = gov_hook.execute
        _plug_execute = plug_hook.execute

        def _tracked_gov(ctx):
            execution_order.append("governance")
            return _gov_execute(ctx)

        def _tracked_plug(ctx):
            execution_order.append("plugin")
            return _plug_execute(ctx)

        gov_hook.execute = _tracked_gov
        plug_hook.execute = _tracked_plug

        # Register plugin first, governance second
        bus.register(plug_hook)
        bus.register(gov_hook)

        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = bus.dispatch(ctx)

        assert execution_order == ["governance", "plugin"]
        assert len(result.alerts) == 1
        assert len(result.warnings) == 1

    def test_governance_confirm_runs_before_plugin_run(self):
        """Governance confirm should run before plugin run."""
        bus = HookBus()
        execution_order = []

        gov_hook = Hook(
            "before_tool_call",
            {"match": "*", "action": "confirm", "message": "confirm please"},
        )
        plug_hook = Hook(
            "before_tool_call",
            {"match": "*", "action": "run", "run": "echo plugin_ran"},
        )

        _gov_execute = gov_hook.execute
        _plug_execute = plug_hook.execute

        gov_hook.execute = lambda ctx: (
            execution_order.append("gov"),
            _gov_execute(ctx),
        )[1]
        plug_hook.execute = lambda ctx: (
            execution_order.append("plug"),
            _plug_execute(ctx),
        )[1]

        bus.register(plug_hook)
        bus.register(gov_hook)

        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = bus.dispatch(ctx)

        assert execution_order == ["gov", "plug"]
        assert result.confirm is True
        assert any("plugin_ran" in o for o in result.outputs)

    def test_multiple_governance_hooks_all_run_before_plugin(self):
        """Multiple governance hooks should all execute before any plugin hook."""
        bus = HookBus()
        execution_order = []

        gov1 = Hook(
            "before_tool_call",
            {"match": "*", "action": "alert", "message": "g1"},
        )
        gov2 = Hook(
            "before_tool_call",
            {"match": "*", "action": "confirm", "message": "g2"},
        )
        plug1 = Hook(
            "before_tool_call",
            {"match": "*", "action": "warn", "message": "p1"},
        )
        plug2 = Hook(
            "before_tool_call",
            {"match": "*", "action": "log", "message": "p2"},
        )

        for hook, label in [
            (gov1, "g1"),
            (gov2, "g2"),
            (plug1, "p1"),
            (plug2, "p2"),
        ]:
            _exec = hook.execute
            hook.execute = lambda ctx, _e=_exec, _l=label: (
                execution_order.append(_l),
                _e(ctx),
            )[1]

        # Register in mixed order
        bus.register(plug1)
        bus.register(gov1)
        bus.register(plug2)
        bus.register(gov2)

        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        bus.dispatch(ctx)

        # Governance hooks should appear before plugin hooks in execution order
        gov_indices = [
            i for i, x in enumerate(execution_order) if x.startswith("g")
        ]
        plug_indices = [
            i for i, x in enumerate(execution_order) if x.startswith("p")
        ]
        assert all(gi < pi for gi in gov_indices for pi in plug_indices)

    def test_governance_block_stops_all_plugin_hooks(self):
        """A governance block should prevent ALL subsequent plugin hooks."""
        bus = HookBus()
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "warn", "message": "warn1"},
            ),
        )
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "log", "message": "log1"},
            ),
        )
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "block", "message": "blocked"},
            ),
        )
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "warn", "message": "warn2"},
            ),
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = bus.dispatch(ctx)
        assert result.blocked
        # Only the block warning; no plugin warnings or logs
        assert len(result.warnings) == 1
        assert result.warnings[0] == "blocked"
        assert len(result.logs) == 0

    def test_plugin_only_dispatch_unchanged(self):
        """When only plugin hooks are present, dispatch works normally."""
        bus = HookBus()
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "warn", "message": "w1"},
            ),
        )
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "log", "message": "l1"},
            ),
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = bus.dispatch(ctx)
        assert not result.blocked
        assert len(result.warnings) == 1
        assert len(result.logs) == 1

    def test_governance_only_dispatch_unchanged(self):
        """When only governance hooks are present, dispatch works normally."""
        bus = HookBus()
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "alert", "message": "a1"},
            ),
        )
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "confirm", "message": "c1"},
            ),
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = bus.dispatch(ctx)
        assert result.confirm is True
        assert len(result.alerts) == 1


class TestAgentHookIntegration:
    @pytest.fixture(autouse=True)
    def register_fake_tools(self):
        from dashscope_agenticx.tools.registry import (
            PermissionLevel,
            ToolDefinition,
            registry,
        )

        def _fake_delete(path: str) -> str:
            return "deleted"

        def _fake_write(path: str, content: str) -> str:
            return "written"

        # Snapshot the real entries: teardown must restore them, not delete
        # them — the registry is a process-wide singleton shared with later
        # test files (e2e relies on the real write_file).
        originals = {
            name: registry.get(name) for name in ("delete_file", "write_file")
        }

        registry.register(
            ToolDefinition(
                name="delete_file",
                description="delete",
                permission=PermissionLevel.AUTO,
                func=_fake_delete,
                parameters={
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            ),
        )
        registry.register(
            ToolDefinition(
                name="write_file",
                description="write",
                permission=PermissionLevel.AUTO,
                func=_fake_write,
                parameters={
                    "type": "object",
                    "properties": {
                        "path": {"type": "string"},
                        "content": {"type": "string"},
                    },
                    "required": ["path", "content"],
                },
            ),
        )
        yield
        for name, original in originals.items():
            if original is None:
                registry.unregister(name)
            else:
                registry.register(original)

    def _make_agent(self, hook_bus):
        from dashscope_agenticx.agent import Agent

        provider = MagicMock()
        executor = MagicMock()
        executor._async_check_permission = MagicMock(return_value=True)

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

    @pytest.mark.asyncio
    async def test_before_tool_call_block(self):
        bus = HookBus()
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "delete_file", "action": "block", "message": "no"},
            ),
        )
        agent = self._make_agent(bus)
        from dashscope_agenticx.providers.base import ToolCall

        result = await agent._execute_tool(
            ToolCall(id="1", name="delete_file", arguments={"path": "x"}),
        )
        assert "Blocked" in result
        agent.executor.execute_detailed.assert_not_called()

    @pytest.mark.asyncio
    async def test_after_tool_call_runs_command(self):
        bus = HookBus()
        bus.register(
            Hook(
                "after_tool_call",
                {"match": "write_file", "action": "run", "run": "echo done"},
            ),
        )
        agent = self._make_agent(bus)
        from dashscope_agenticx.providers.base import ToolCall

        with patch("dashscope_agenticx.agent.console") as mock_console:
            result = await agent._execute_tool(
                ToolCall(
                    id="1",
                    name="write_file",
                    arguments={"path": "x", "content": "y"},
                ),
            )
        assert result == "ok"
        mock_console.print.assert_called()


# ── M6a: content_pattern on message/response events ───────────────────────


class TestContentPatternMessageEvents:
    def test_on_response_matches_content(self):
        hook = Hook(
            "on_response",
            {
                "match": "*",
                "content_pattern": "*违约金*",
                "action": "warn",
                "message": "legal",
            },
        )
        ctx = HookContext(
            event="on_response",
            tool_name="",
            arguments={
                "input": "看看这份合同",
                "content": "合同包含违约金条款",
            },
        )
        assert hook.applies_to(ctx)

    def test_on_response_ignores_other_fields(self):
        """Pattern matching only the user input must NOT fire on_response."""
        hook = Hook(
            "on_response",
            {"match": "*", "content_pattern": "*secret*", "action": "warn"},
        )
        ctx = HookContext(
            event="on_response",
            tool_name="",
            arguments={"input": "tell me the secret", "content": "普通的回答"},
        )
        assert not hook.applies_to(ctx)

    def test_on_response_result_fallback(self):
        """Direct dispatchers that set result still match (back-compat)."""
        hook = Hook(
            "on_response",
            {"match": "*", "content_pattern": "*oops*", "action": "warn"},
        )
        ctx = HookContext(
            event="on_response",
            tool_name="",
            arguments={},
            result="oops failed",
        )
        assert hook.applies_to(ctx)

    def test_on_message_matches_input(self):
        hook = Hook(
            "on_message",
            {"match": "*", "content_pattern": "*密码*", "action": "warn"},
        )
        ctx = HookContext(
            event="on_message",
            tool_name="",
            arguments={"input": "我的密码是123"},
        )
        assert hook.applies_to(ctx)

    def test_on_message_no_match(self):
        hook = Hook(
            "on_message",
            {"match": "*", "content_pattern": "*密码*", "action": "warn"},
        )
        ctx = HookContext(
            event="on_message",
            tool_name="",
            arguments={"input": "你好"},
        )
        assert not hook.applies_to(ctx)


# ── M6b: async command hooks ──────────────────────────────────────────────


class TestAsyncShellRunner:
    async def test_uses_create_subprocess_exec(self, monkeypatch):
        import dashscope_agenticx.hooks as hooks_mod

        calls = {}

        class _FakeProc:
            returncode = 0

            async def communicate(self):
                return b"hello\n", b""

        async def _fake_exec(*args, **kwargs):
            calls["args"] = args
            calls["kwargs"] = kwargs
            return _FakeProc()

        monkeypatch.setattr(
            hooks_mod.asyncio,
            "create_subprocess_exec",
            _fake_exec,
        )
        code, stdout, stderr = await hooks_mod._run_shell_async("echo hello")
        assert code == 0
        assert stdout == "hello"
        assert stderr == ""
        # Shell wrapper + the original command string as separate argv items
        assert calls["args"][-1] == "echo hello"
        assert calls["kwargs"]["stdout"] == hooks_mod.asyncio.subprocess.PIPE

    async def test_timeout_kills_process(self, monkeypatch):
        import dashscope_agenticx.hooks as hooks_mod

        class _FakeProc:
            def __init__(self):
                self.killed = False
                self.returncode = None

            async def communicate(self):
                await hooks_mod.asyncio.sleep(30)
                return b"", b""

            def kill(self):
                self.killed = True

            async def wait(self):
                return -9

        proc = _FakeProc()

        async def _fake_exec(*args, **kwargs):
            return proc

        monkeypatch.setattr(
            hooks_mod.asyncio,
            "create_subprocess_exec",
            _fake_exec,
        )
        monkeypatch.setattr(hooks_mod, "_HOOK_TIMEOUT", 0.05)
        code, stdout, stderr = await hooks_mod._run_shell_async("sleep 999")
        assert code == 124
        assert stdout == ""
        assert stderr == "hook command timed out"
        assert proc.killed

    async def test_spawn_failure_is_graceful(self, monkeypatch):
        import dashscope_agenticx.hooks as hooks_mod

        async def _boom(*args, **kwargs):
            raise OSError("no shell")

        monkeypatch.setattr(hooks_mod.asyncio, "create_subprocess_exec", _boom)
        code, stdout, stderr = await hooks_mod._run_shell_async("whatever")
        assert code == 1
        assert "hook command failed" in stderr

    async def test_does_not_block_event_loop(self):
        import dashscope_agenticx.hooks as hooks_mod

        ticks = 0
        stop = False

        async def _ticker():
            nonlocal ticks, stop
            while not stop:
                await hooks_mod.asyncio.sleep(0.02)
                ticks += 1

        ticker = hooks_mod.asyncio.create_task(_ticker())
        try:
            code, stdout, _ = await hooks_mod._run_shell_async(
                "sleep 0.2; echo done",
            )
        finally:
            stop = True
            await ticker
        assert code == 0
        assert stdout == "done"
        # The loop kept running other tasks during the 0.2s subprocess.
        assert ticks >= 3


class TestAsyncHookExecution:
    async def test_execute_async_run_action(self):
        hook = Hook(
            "after_tool_call",
            {"match": "*", "action": "run", "run": "echo hi"},
        )
        ctx = HookContext(
            event="after_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = await hook.execute_async(ctx)
        assert result.output == "hi"

    async def test_execute_async_non_run_matches_sync(self):
        hook = Hook(
            "before_tool_call",
            {"match": "*", "action": "warn", "message": "careful"},
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        sync_result = hook.execute(ctx)
        async_result = await hook.execute_async(ctx)
        assert async_result.warning == sync_result.warning == "careful"

    async def test_dispatch_async_merges_outputs(self):
        bus = HookBus()
        bus.register(
            Hook(
                "after_tool_call",
                {"match": "*", "action": "run", "run": "echo out"},
            ),
        )
        ctx = HookContext(
            event="after_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = await bus.dispatch_async(ctx)
        assert result.outputs == ["out"]

    async def test_dispatch_async_governance_block_first(self):
        bus = HookBus()
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "warn", "message": "plugin"},
            ),
        )
        bus.register(
            Hook(
                "before_tool_call",
                {"match": "*", "action": "block", "message": "gov"},
            ),
        )
        ctx = HookContext(
            event="before_tool_call",
            tool_name="write_file",
            arguments={},
        )
        result = await bus.dispatch_async(ctx)
        assert result.blocked
        assert result.warnings == ["gov"]
