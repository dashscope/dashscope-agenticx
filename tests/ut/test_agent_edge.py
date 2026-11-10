# -*- coding: utf-8 -*-
"""Edge-case tests for the agent loop and sub-agent management.

Covers:
  * UserAbortedTurn in the parallel tool path — must not be swallowed into a
    generic "错误:" tool result; the turn aborts only after every tool_call
    gets a tool response (providers reject unpaired tool_calls).
  * UserAbortedTurn in the sequential path — pending tool_calls are closed
    out before the abort propagates.
  * UserSupplement pairing (regression guard) in both paths.
  * Unknown-tool error still produces a paired tool response.
  * subagent_invoke children are built with allow_delegate=False.
  * delegate prompt building / max_turns precedence.
  * /subagents command surface (list/enable/disable/config/reload).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.providers.base import LLMChunk, ToolCall
from dashscope_agenticx.tools.registry import (
    PermissionLevel,
    ToolDefinition,
    registry,
)
from dashscope_agenticx.utils import UserAbortedTurn, UserSupplement
from tests.ut.conftest import script_outcomes


@pytest.fixture
def clean_registry():
    before = dict(registry._tools)
    yield registry
    registry._tools.clear()
    registry._tools.update(before)


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Keep audit/history/directives writes out of the real workspace."""
    monkeypatch.setattr(
        "dashscope_agenticx.audit.get_audit_logger",
        lambda: MagicMock(),
    )
    from dashscope_agenticx.platforms.local import history

    monkeypatch.setattr(history, "WORKSPACE_DIR", tmp_path / "ws")
    import dashscope_agenticx.memory.directives_learning as dl

    monkeypatch.setattr(dl, "WORKSPACE_DIR", tmp_path / "ws")
    return tmp_path


def _register_tool(
    name: str,
    permission: PermissionLevel = PermissionLevel.AUTO,
):
    registry._tools[name] = ToolDefinition(
        name=name,
        description="t",
        permission=permission,
        func=lambda **kw: "ok",
        parameters={"type": "object", "properties": {}},
    )


def _make_agent(script, executor_side_effects, captured):
    """Agent with a scripted streaming provider.

    script items: ("tools", [name, ...]) → one chunk with those tool_calls;
    ("content", text) → final content chunk. Provider calls pop in order;
    the system-message content is recorded per call.
    """
    from dashscope_agenticx.memory.reflection import (
        ReflectionTracker,
        StagnationTracker,
    )

    provider = MagicMock()

    async def _stream(messages, *args, **kwargs):
        captured.append(messages[0]["content"])
        kind, value = script.pop(0)
        if kind == "tools":
            yield LLMChunk(
                tool_calls=[
                    ToolCall(id=f"tc_{name}", name=name, arguments={})
                    for name in value
                ],
            )
        else:
            yield LLMChunk(delta_content=value)

    provider.chat_stream = _stream

    tracker = ReflectionTracker(threshold=3)
    memory_manager = MagicMock()
    memory_manager.session.reflection = tracker
    memory_manager.session.stagnation = StagnationTracker()
    memory_manager.record_tool_execution.side_effect = (
        tracker.record_tool_execution
    )
    memory_manager.session.tool_chains.get_fallback_hints.return_value = ""

    executor = MagicMock()
    executor.execute_detailed = AsyncMock(
        side_effect=script_outcomes(executor_side_effects),
    )

    agent = Agent(
        provider=provider,
        executor=executor,
        memory_manager=memory_manager,
        system_prompt="",
    )
    agent._system_prompt_for_turn = lambda _txt: "base"
    return agent, executor


def _assert_tool_pairing(agent: Agent):
    """Every assistant tool_call id has exactly one matching tool response."""
    pending: list[str] = []
    for msg in agent.messages:
        if msg.get("role") == "assistant":
            pending.extend(tc["id"] for tc in msg.get("tool_calls", []))
        elif msg.get("role") == "tool":
            assert (
                msg["tool_call_id"] in pending
            ), f"orphan tool response: {msg}"
            pending.remove(msg["tool_call_id"])
    assert pending == [], f"unanswered tool_calls: {pending}"


# ---------------------------------------------------------------------------
# Parallel tool path
# ---------------------------------------------------------------------------


class TestParallelToolPath:
    @pytest.mark.asyncio
    async def test_abort_not_swallowed_and_pairing_intact(
        self,
        clean_registry,
        isolated_env,
    ):
        """gather(return_exceptions=True) used to convert UserAbortedTurn into
        a '错误:' tool result — the stop signal was lost and the loop went on.
        Now: all calls get tool responses, then the abort re-raises."""
        _register_tool("alpha")
        _register_tool("beta")
        script = [("tools", ["alpha", "beta"]), ("content", "unreached")]
        captured: list[str] = []
        agent, _ = _make_agent(
            script,
            ["alpha-ok", UserAbortedTurn("用户中止本轮对话")],
            captured,
        )

        with pytest.raises(UserAbortedTurn):
            async for _ in agent.run_stream("go"):
                pass

        _assert_tool_pairing(agent)
        tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
        assert tool_msgs[0]["content"] == "alpha-ok"
        assert tool_msgs[1]["content"] == "Cancelled (user aborted this turn)"
        # The abort must end the turn — no second provider call.
        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_supplement_pauses_loop_and_replans(
        self,
        clean_registry,
        isolated_env,
    ):
        _register_tool("alpha")
        _register_tool("beta")
        script = [("tools", ["alpha", "beta"]), ("content", "replanned")]
        captured: list[str] = []
        agent, _ = _make_agent(
            script,
            [UserSupplement("更多细节"), "beta-ok"],
            captured,
        )

        out = ""
        async for chunk in agent.run_stream("go"):
            out += chunk

        assert "replanned" in out
        _assert_tool_pairing(agent)
        tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
        assert (
            tool_msgs[0]["content"] == "Cancelled (user added info to replan)"
        )
        assert tool_msgs[1]["content"] == "beta-ok"
        user_msgs = [m for m in agent.messages if m["role"] == "user"]
        assert any("更多细节" in m["content"] for m in user_msgs)
        assert len(captured) == 2

    @pytest.mark.asyncio
    async def test_parallel_success_results_in_order(
        self,
        clean_registry,
        isolated_env,
    ):
        _register_tool("alpha")
        _register_tool("beta")
        script = [("tools", ["alpha", "beta"]), ("content", "done")]
        captured: list[str] = []
        agent, _ = _make_agent(script, ["r1", "r2"], captured)

        out = ""
        async for chunk in agent.run_stream("go"):
            out += chunk

        assert "r1" in out and "r2" in out and "done" in out
        _assert_tool_pairing(agent)


# ---------------------------------------------------------------------------
# Sequential tool path
# ---------------------------------------------------------------------------


class TestSequentialToolPath:
    @pytest.mark.asyncio
    async def test_abort_closes_pending_calls_and_reraises(
        self,
        clean_registry,
        isolated_env,
    ):
        """Aborting tool #1 of 2 used to leave the assistant message with an
        unanswered tool_call — the next API request was then rejected. Now the
        remaining calls get synthetic cancel responses before re-raise."""
        _register_tool("write_file", PermissionLevel.CONFIRM)
        _register_tool("alpha")
        script = [("tools", ["write_file", "alpha"]), ("content", "unreached")]
        captured: list[str] = []
        agent, executor = _make_agent(
            script,
            [UserAbortedTurn("用户中止本轮对话")],
            captured,
        )

        with pytest.raises(UserAbortedTurn):
            async for _ in agent.run_stream("go"):
                pass

        _assert_tool_pairing(agent)
        tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
        assert len(tool_msgs) == 2
        assert all(
            m["content"] == "Cancelled (user aborted this turn)"
            for m in tool_msgs
        )
        # The second tool never executed.
        assert executor.execute_detailed.await_count == 1
        assert len(captured) == 1

    @pytest.mark.asyncio
    async def test_supplement_closes_pending_and_continues(
        self,
        clean_registry,
        isolated_env,
    ):
        _register_tool("write_file", PermissionLevel.CONFIRM)
        _register_tool("alpha")
        script = [("tools", ["write_file", "alpha"]), ("content", "replanned")]
        captured: list[str] = []
        agent, _ = _make_agent(script, [UserSupplement("补充说明")], captured)

        out = ""
        async for chunk in agent.run_stream("go"):
            out += chunk

        assert "replanned" in out
        _assert_tool_pairing(agent)
        tool_msgs = [m for m in agent.messages if m["role"] == "tool"]
        assert all(
            m["content"] == "Cancelled (user added info to replan)"
            for m in tool_msgs
        )
        user_msgs = [m for m in agent.messages if m["role"] == "user"]
        assert any("补充说明" in m["content"] for m in user_msgs)

    @pytest.mark.asyncio
    async def test_unknown_tool_yields_paired_error(
        self,
        clean_registry,
        isolated_env,
    ):
        script = [("tools", ["ghost_tool"]), ("content", "sorry")]
        captured: list[str] = []
        agent, executor = _make_agent(script, [], captured)

        out = ""
        async for chunk in agent.run_stream("go"):
            out += chunk

        assert "unknown tool" in out
        _assert_tool_pairing(agent)
        assert executor.execute_detailed.await_count == 0


# ---------------------------------------------------------------------------
# subagent: nested-delegation guard
# ---------------------------------------------------------------------------


def _fake_parent():
    from dashscope_agenticx.tools.registry import ToolRegistry

    class FakeAgent:
        provider = MagicMock()
        executor = MagicMock()
        tool_registry = ToolRegistry()
        user_name = "u"
        provider_name = "p"
        model_name = "m"
        disabled_caps_provider = None
        directives_provider = None
        hook_bus = None
        memory_manager = None
        system_prompt = "BASE"

    return FakeAgent()


class TestSubagentNestingGuard:
    @pytest.mark.asyncio
    async def test_child_built_with_allow_delegate_false(self):
        """delegate.py children already disable nested spawning; subagent.py
        children must too — otherwise a sub-agent can fan out further."""
        import dashscope_agenticx.agents.subagent as subagent_mod

        subagent_mod.set_parent_agent(_fake_parent())
        try:
            with patch("dashscope_agenticx.agent.Agent") as MockAgent:
                instance = MockAgent.return_value

                async def _stream(prompt):
                    yield "done"

                instance.run_stream = _stream
                await subagent_mod._subagent_invoke("task")

            assert MockAgent.call_args.kwargs["allow_delegate"] is False
            assert MockAgent.call_args.kwargs["session_path"] is None
        finally:
            subagent_mod.set_parent_agent(None)


# ---------------------------------------------------------------------------
# delegate: prompt building + max_turns precedence
# ---------------------------------------------------------------------------


class TestDelegateHelpers:
    def test_build_prompt_inlines_context_files(self, tmp_path):
        from dashscope_agenticx.agents.delegate import _build_prompt

        f = tmp_path / "ctx.txt"
        f.write_text("CONTEXT BODY", encoding="utf-8")
        out = _build_prompt("do it", [str(f), str(tmp_path / "missing.txt")])

        assert "CONTEXT BODY" in out
        assert "read failed" in out
        assert out.endswith("Task: do it")

    def test_child_system_prompt_variants(self):
        from dashscope_agenticx.agents.delegate import _child_system_prompt

        custom = _child_system_prompt(["read_file"], override="CUSTOM ROLE")
        assert custom.startswith("CUSTOM ROLE")
        assert "read_file" in custom
        assert "may not delegate" in custom

        default = _child_system_prompt(None)
        assert "may use any" in default
        assert "may not delegate" in default

    @pytest.mark.asyncio
    async def test_run_child_max_turns_precedence(self):
        """explicit param (hard-capped at 50) > subagents config > default 10."""
        import dashscope_agenticx.agents.delegate as delegate_mod

        fake_config = SimpleNamespace(
            subagents={"local.delegate": SimpleNamespace(max_turns=7)},
            delegation=SimpleNamespace(
                max_concurrent=5,
                default_timeout=120,
                allow_nested=False,
            ),
        )
        delegate_mod.set_config(fake_config)
        delegate_mod.set_parent_agent(_fake_parent())
        try:
            with patch("dashscope_agenticx.agent.Agent") as MockAgent:
                instance = MockAgent.return_value

                async def _stream(prompt):
                    yield "done"

                instance.run_stream = _stream

                await delegate_mod._run_child(
                    "t",
                    None,
                    None,
                    10,
                    max_turns=99,
                )
                assert MockAgent.call_args.kwargs["max_turns"] == 50

                await delegate_mod._run_child("t", None, None, 10)
                assert MockAgent.call_args.kwargs["max_turns"] == 7
        finally:
            delegate_mod.set_config(None)
            delegate_mod.set_parent_agent(None)


# ---------------------------------------------------------------------------
# /subagents command surface
# ---------------------------------------------------------------------------


class TestSubagentsCommand:
    def _config(self, monkeypatch):
        from dashscope_agenticx.config import Config

        monkeypatch.setattr(Config, "save_workspace", lambda self: None)
        monkeypatch.setattr(Config, "save_global", lambda self: None)
        return Config()

    def test_discover_lists_builtin_subagents(self, monkeypatch, tmp_path):
        config = self._config(monkeypatch)
        from dashscope_agenticx.agents.subagents import discover_subagents

        defs = discover_subagents(config)
        keys = {d.key for d in defs}
        assert {"local.subagent", "local.delegate"} <= keys
        assert all(d.source == "built-in" for d in defs)
        assert all(d.category == "local" for d in defs)

    def test_enable_disable_roundtrip(self, clean_registry, monkeypatch):
        import dashscope_agenticx.agents.subagent as subagent_mod
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        config = self._config(monkeypatch)
        config.enabled_capabilities = []
        # Tool wiring needs a live parent agent (cli.py normally sets one
        # after Agent construction); without it enable is config-only.
        subagent_mod.set_parent_agent(_fake_parent())
        try:
            handle_subagents_command(
                "/subagents enable local.subagent",
                config,
            )
            assert "local.subagent" in config.enabled_capabilities
            assert registry.get("subagent_invoke") is not None

            handle_subagents_command(
                "/subagents disable local.subagent",
                config,
            )
            assert "local.subagent" not in config.enabled_capabilities
            assert registry.get("subagent_invoke") is None
        finally:
            subagent_mod.set_parent_agent(None)

    def test_enable_unknown_key_rejected(self, monkeypatch):
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        config = self._config(monkeypatch)
        config.enabled_capabilities = []
        handle_subagents_command("/subagents enable bogus.key", config)
        assert config.enabled_capabilities == []

    def test_enable_when_all_enabled_is_noop(self, monkeypatch):
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        config = self._config(monkeypatch)
        config.enabled_capabilities = None  # everything enabled
        handle_subagents_command("/subagents enable local.subagent", config)
        assert config.enabled_capabilities is None

    def test_disable_from_all_enabled_builds_explicit_list(
        self,
        clean_registry,
        monkeypatch,
    ):
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        config = self._config(monkeypatch)
        config.enabled_capabilities = None

        handle_subagents_command("/subagents disable local.delegate", config)
        assert config.enabled_capabilities is not None
        assert "local.delegate" not in config.enabled_capabilities
        assert "local.subagent" in config.enabled_capabilities

    def test_config_set_get_and_validation(self, monkeypatch):
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        config = self._config(monkeypatch)

        handle_subagents_command(
            "/subagents config local.subagent model qwen3-max",
            config,
        )
        assert config.subagents["local.subagent"].model == "qwen3-max"

        handle_subagents_command(
            "/subagents config local.subagent temperature 0.5",
            config,
        )
        assert config.subagents["local.subagent"].temperature == 0.5

        handle_subagents_command(
            "/subagents config local.subagent max_turns 20",
            config,
        )
        assert config.subagents["local.subagent"].max_turns == 20

        # Invalid values are rejected without clobbering prior state.
        handle_subagents_command(
            "/subagents config local.subagent temperature abc",
            config,
        )
        assert config.subagents["local.subagent"].temperature == 0.5
        handle_subagents_command(
            "/subagents config local.subagent temperature 3.0",
            config,
        )
        assert config.subagents["local.subagent"].temperature == 0.5
        handle_subagents_command(
            "/subagents config local.subagent max_turns 0",
            config,
        )
        assert config.subagents["local.subagent"].max_turns == 20
        handle_subagents_command(
            "/subagents config local.subagent bogus_key v",
            config,
        )
        assert not hasattr(config.subagents["local.subagent"], "bogus_key")

    def test_config_unknown_subagent_rejected(self, monkeypatch):
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        config = self._config(monkeypatch)
        handle_subagents_command("/subagents config bogus.key model x", config)
        assert "bogus.key" not in config.subagents

    def test_inert_config_keys_say_so(self, monkeypatch):
        """model/temperature are stored but nothing reads them, so the UI
        must not report them as knobs that turned."""
        from rich.console import Console

        from dashscope_agenticx.agents import subagents as subagents_mod
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        recorded = Console(record=True, width=200)
        monkeypatch.setattr(subagents_mod, "console", recorded)
        config = self._config(monkeypatch)

        handle_subagents_command(
            "/subagents config local.subagent temperature 0.5",
            config,
        )
        handle_subagents_command(
            "/subagents config local.subagent max_turns 20",
            config,
        )
        handle_subagents_command("/subagents config local.subagent", config)

        out = recorded.export_text()
        updated = [ln for ln in out.splitlines() if "Updated" in ln]
        assert len(updated) == 2
        assert "not applied" in updated[0]  # temperature
        assert "not applied" not in updated[1]  # max_turns does take effect
        assert "max_turns: 20" in out
        assert "temperature: 0.5" in out

    def test_list_and_unknown_subcommand(self, monkeypatch):
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        config = self._config(monkeypatch)
        handle_subagents_command("/subagents", config)  # list
        handle_subagents_command("/subagents list", config)
        handle_subagents_command("/subagents frobnicate", config)  # usage

    def test_reload(self, monkeypatch):
        import dashscope_agenticx.cli as cli_mod
        import dashscope_agenticx.extensions as ext_mod
        from dashscope_agenticx.agents.subagents import (
            handle_subagents_command,
        )

        monkeypatch.setattr(
            ext_mod,
            "apply_extensions",
            lambda models: SimpleNamespace(capabilities=[]),
        )
        monkeypatch.setattr(
            cli_mod,
            "sync_extensions_into_catalog",
            lambda ext: None,
        )
        config = self._config(monkeypatch)
        handle_subagents_command("/subagents reload", config)
