# -*- coding: utf-8 -*-
"""Tests for P0 agent-loop correctness fixes.

Covers:
  * ``_classify_outcome`` — counter-based turn outcome classification.
  * Stale reflection hint — once failures stop, the hint must disappear
    from the system message instead of sticking for the rest of the turn.
  * The "工具连续失败" lesson is recorded at most once per turn even when
    the reflection hint persists across loop iterations.
  * Evidence anchoring — the hint quotes the failing tool's own output, and
    the stored lesson is the model's diagnosis rather than a template.
  * ``_current_turn_tools`` dedupes repeated calls of the same tool.
  * ``get_fallback_hints`` no longer contradicts system-prompt rule 15.
  * Futility refusal — a call that already failed identically, with no state
    change since, is refused before it reaches the executor or the permission
    hook, and the next request carries the replan demand.
  * Acceptance gate — in oneshot mode a failing verification check withholds
    the final answer and sends its raw output back, while a read-only or
    interactive turn never runs the check at all.
"""

from __future__ import annotations

import asyncio
import time
from unittest.mock import AsyncMock, MagicMock

import pytest

# Register builtin tools into the global registry so tool-dispatch tests
# work when this file runs standalone (registration is an import side
# effect of the tool modules).
import dashscope_agenticx.tools.filesystem  # noqa: E402,F401
import dashscope_agenticx.tools.shell  # noqa: E402,F401
from dashscope_agenticx.agent import SYSTEM_PROMPT, Agent, _classify_outcome
from dashscope_agenticx.executor import classify_tool_text
from dashscope_agenticx.memory.reflection import (
    ReflectionTracker,
    StagnationTracker,
    compression_hint,
    convergence_hint,
    time_budget_hint,
)
from dashscope_agenticx.memory.tool_chains import get_fallback_hints
from dashscope_agenticx.providers.base import LLMChunk, ToolCall
from dashscope_agenticx.providers.hardening import RetriesExhausted
from tests.ut.conftest import script_outcomes

# ---------------------------------------------------------------------------
# _classify_outcome
# ---------------------------------------------------------------------------


class TestClassifyOutcome:
    def test_all_success(self):
        assert _classify_outcome(3, 0) == "success"

    def test_all_failure(self):
        assert _classify_outcome(0, 2) == "failure"

    def test_mixed(self):
        assert _classify_outcome(1, 1) == "partial"

    def test_nothing_counted_is_neutral(self):
        # All executions cancelled — neither success nor failure.
        assert _classify_outcome(0, 0) == "partial"


# ---------------------------------------------------------------------------
# Agent loop: reflection hint lifecycle + experience recording
# ---------------------------------------------------------------------------


def _make_agent(
    script,
    executor_results,
    captured,
    requests=None,
    oneshot=False,
):
    """Build an Agent with a scripted streaming provider.

    ``script`` items: ("tool", name), ("same_tool", name), ("content", text),
    ("length", text) / ("stop", text) for a text response that ends with the
    given finish_reason, ("truncated_tool", name) for a cap-cut call whose
    arguments never completed, ("mixed_tool", (usable, cut)) for one response
    carrying both, or ("narrated_tool", (text, name)) for a response
    that explains itself and then acts — the shape the reflection hint asks
    for. Each provider call pops one item; the system-message content is
    recorded per call in ``captured``, and the full request is recorded in
    ``requests`` when given.

    ``oneshot`` selects autonomous mode, which is the only mode the acceptance
    gate arms in.

    ("tool", …) gives every call a distinct ``call_index`` argument, and
    ("same_tool", …) does not. The distinction matters: StagnationTracker
    refuses a call whose arguments *and* output are byte-identical to an
    earlier one, so a run of plain ("tool", …) items would stop reaching the
    executor at its third failure. Tests that are about repeated failures
    rather than repeated *calls* need the distinct form; tests that are about
    the refusal need ("same_tool", …).
    """
    provider = MagicMock()

    async def _stream(messages, *args, **kwargs):
        captured.append(messages[0]["content"])
        if requests is not None:
            requests.append(list(messages))
        kind, value = script.pop(0)
        if kind == "narrated_tool":
            value, tool_name = value
            yield LLMChunk(delta_content=value)
            arguments = {"call_index": len(captured)}
        elif kind in ("tool", "same_tool"):
            tool_name = value
            arguments = (
                {} if kind == "same_tool" else {"call_index": len(captured)}
            )
        elif kind in ("length", "stop"):
            yield LLMChunk(delta_content=value, finish_reason=kind)
            return
        elif kind == "truncated_tool":
            yield LLMChunk(
                tool_calls=[
                    ToolCall(
                        id=f"tc{len(captured)}",
                        name=value,
                        arguments={},
                        truncated=True,
                    ),
                ],
                finish_reason="length",
            )
            return
        elif kind == "mixed_tool":
            usable_name, cut_name = value
            yield LLMChunk(
                tool_calls=[
                    ToolCall(
                        id=f"tc{len(captured)}a",
                        name=usable_name,
                        arguments={"call_index": len(captured)},
                    ),
                    ToolCall(
                        id=f"tc{len(captured)}b",
                        name=cut_name,
                        arguments={},
                        truncated=True,
                    ),
                ],
            )
            return
        else:
            yield LLMChunk(delta_content=value)
            return
        yield LLMChunk(
            tool_calls=[
                ToolCall(
                    id=f"tc{len(captured)}",
                    name=tool_name,
                    arguments=arguments,
                ),
            ],
        )

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
        side_effect=script_outcomes(executor_results),
    )

    agent = Agent(
        provider=provider,
        executor=executor,
        memory_manager=memory_manager,
        system_prompt="",  # avoid reading workspace/global prompt files
        oneshot=oneshot,
    )
    agent._system_prompt_for_turn = lambda _txt: "base"
    return agent, memory_manager


@pytest.fixture
def isolated_env(tmp_path, monkeypatch):
    """Keep audit/history writes out of the real workspace."""
    monkeypatch.setattr(
        "dashscope_agenticx.audit.get_audit_logger",
        lambda: MagicMock(),
    )
    from dashscope_agenticx.platforms.local import history

    monkeypatch.setattr(history, "WORKSPACE_DIR", tmp_path / "ws")
    return tmp_path


class TestReflectionHintLifecycle:
    @pytest.mark.asyncio
    async def test_hint_injected_then_cleared_after_success(
        self,
        isolated_env,
    ):
        # 4 failures (threshold 3 → hint on iterations 4-5), then a success
        # resets the tracker → iteration 6 must run without the hint.
        script = [
            ("tool", "run_command"),
            ("tool", "run_command"),
            ("tool", "run_command"),
            ("tool", "run_command"),
            ("tool", "run_command"),
            ("content", "finished"),
        ]
        results = ["错误: boom"] * 4 + ["ok"]
        captured: list[str] = []
        requests: list[list[dict]] = []
        agent, mm = _make_agent(script, results, captured, requests)

        chunks = [c async for c in agent.run_stream("do something")]
        assert "finished" in "".join(chunks)

        # The system message never changes, on any iteration. Folding the
        # volatile hints into it used to rewrite the whole prompt prefix
        # byte-for-byte every loop, which defeated provider-side caching.
        assert captured == ["base"] * 6

        def _tail(i):
            return requests[i][-1]

        # Iterations 0-2 are below the failure threshold: no hint message, so
        # the request simply ends on the accumulated history.
        assert all(
            "Reflection: repeated tool failures" not in str(_tail(i))
            for i in (0, 1, 2)
        )

        # Iterations 3-4 carry the hint as a trailing user message.
        assert _tail(3)["role"] == "user"
        assert "Reflection: repeated tool failures" in _tail(3)["content"]
        assert _tail(4)["role"] == "user"
        assert "Reflection: repeated tool failures" in _tail(4)["content"]

        # Success on iteration 5 reset the tracker → the request ends on the
        # tool result again, with no hint appended.
        assert _tail(5)["role"] == "tool"
        assert "Reflection: repeated tool failures" not in str(_tail(5))

        # The hint never leaks into persisted history.
        assert all(
            "Reflection: repeated tool failures"
            not in str(m.get("content", ""))
            for m in agent.messages
        )

        # The repeated-failure lesson is recorded exactly once for the turn,
        # even though the hint was injected on two iterations.
        lesson_calls = [
            c
            for c in mm.record_experience.call_args_list
            if c.kwargs.get("task_summary") == "repeated tool failures"
        ]
        assert len(lesson_calls) == 1
        assert lesson_calls[0].kwargs["outcome"] == "failure"

        # Turn-level experience uses counter-based outcome: 1 success,
        # 4 failures → partial (not the old last-message substring heuristic).
        turn_calls = [
            c
            for c in mm.record_experience.call_args_list
            if c.kwargs.get("task_summary") == "do something"
        ]
        assert len(turn_calls) == 1
        assert turn_calls[0].kwargs["outcome"] == "partial"
        assert turn_calls[0].kwargs["tools_used"] == ["run_command"]

    @pytest.mark.asyncio
    async def test_nonzero_exit_code_classified_failure(self, isolated_env):
        # A failing test run used to be recorded as a success: the verdict was
        # sniffed off the result text's prefix, and pytest's failure output
        # does not start with one. Reflection, experience memory and directive
        # learning all therefore missed it.
        script = [("tool", "run_command"), ("content", "done")]
        captured: list[str] = []
        agent, mm = _make_agent(
            script,
            ["FAILED tests/test_x.py\n[exit code: 1]"],
            captured,
        )
        async for _ in agent.run_stream("run the tests"):
            pass

        turn_call = mm.record_experience.call_args_list[-1]
        assert turn_call.kwargs["outcome"] == "failure"

        # The verdict's provenance is inspectable after the fact.
        trace_call = mm.log_trace.call_args_list[0]
        assert trace_call.args[0] == "tool_execution"
        assert trace_call.args[1]["signal_kind"] == "exit_code"
        assert trace_call.args[1]["exit_code"] == 1
        assert trace_call.args[1]["success"] is False

    @pytest.mark.asyncio
    async def test_all_failures_classified_failure(self, isolated_env):
        script = [("tool", "run_command"), ("content", "gave up")]
        captured: list[str] = []
        agent, mm = _make_agent(script, ["错误: boom"], captured)
        async for _ in agent.run_stream("try it"):
            pass

        turn_call = mm.record_experience.call_args_list[-1]
        assert turn_call.kwargs["outcome"] == "failure"

    @pytest.mark.asyncio
    async def test_all_success_classified_success(self, isolated_env):
        script = [("tool", "run_command"), ("content", "done")]
        captured: list[str] = []
        agent, mm = _make_agent(script, ["ok"], captured)

        async for _ in agent.run_stream("try it"):
            pass

        turn_call = mm.record_experience.call_args_list[-1]
        assert turn_call.kwargs["outcome"] == "success"


def _reflection_lessons(mm):
    """The repeated-failure experience calls made during a run."""
    return [
        c
        for c in mm.record_experience.call_args_list
        if c.kwargs.get("task_summary") == "repeated tool failures"
    ]


class TestReflectionEvidenceAnchoring:
    """A7 — the hint quotes the tool's own output; the lesson is the model's
    diagnosis rather than a template string about a count."""

    @pytest.mark.asyncio
    async def test_raw_output_and_verdict_reach_the_tracker(
        self,
        isolated_env,
    ):
        script = [("tool", "run_command"), ("content", "done")]
        captured: list[str] = []
        agent, mm = _make_agent(
            script,
            ["FAILED tests/test_a.py - assert 1 == 2\n[exit code: 1]"],
            captured,
        )
        async for _ in agent.run_stream("run the tests"):
            pass

        tracker = mm.session.reflection
        assert tracker.evidence.startswith("FAILED tests/test_a.py")
        assert tracker.evidence_tool == "run_command"
        assert tracker.evidence_signal == "exit_code"
        assert tracker.evidence_exit_code == 1

    @pytest.mark.asyncio
    async def test_evidence_is_not_the_fallback_augmented_result(
        self,
        isolated_env,
    ):
        # The reflection hint quotes evidence back as the tool's own words, so
        # it must be the classified output, not `result` after our hint was
        # appended — otherwise the model reads our advice as tool output.
        script = [("tool", "run_command"), ("content", "done")]
        captured: list[str] = []
        agent, mm = _make_agent(script, ["boom\n[exit code: 1]"], captured)
        mm.session.tool_chains.get_fallback_hints.return_value = (
            "\nHint: try uv instead"
        )
        async for _ in agent.run_stream("install it"):
            pass

        assert mm.session.reflection.evidence == "boom\n[exit code: 1]"

    @pytest.mark.asyncio
    async def test_quoted_evidence_lands_in_the_hint(self, isolated_env):
        script = [("tool", "run_command")] * 4 + [("content", "done")]
        requests: list[list[dict]] = []
        agent, _mm = _make_agent(
            script,
            ["ModuleNotFoundError: No module named 'yaml'\n[exit code: 1]"]
            * 4,
            [],
            requests,
        )
        async for _ in agent.run_stream("fix imports"):
            pass

        hint = requests[3][-1]["content"]
        assert "ModuleNotFoundError" in hint
        assert "exit_code=1" in hint

    @pytest.mark.asyncio
    async def test_lesson_is_the_models_own_diagnosis(self, isolated_env):
        diagnosis = "the venv has no pyyaml; installing it before retrying"
        script = [("tool", "run_command")] * 3 + [
            ("narrated_tool", (diagnosis, "run_command")),
            ("content", "done"),
        ]
        captured: list[str] = []
        agent, mm = _make_agent(
            script,
            ["ModuleNotFoundError: No module named 'yaml'\n[exit code: 1]"]
            * 4,
            captured,
        )
        async for _ in agent.run_stream("fix imports"):
            pass

        lessons = _reflection_lessons(mm)
        assert len(lessons) == 1
        assert lessons[0].kwargs["lesson"] == diagnosis

    @pytest.mark.asyncio
    async def test_lesson_falls_back_to_evidence_without_text(
        self,
        isolated_env,
    ):
        # A tool-only response carries no diagnosis, but the stored lesson
        # must still be anchored on the failing output rather than the bare
        # "need a new strategy" template it replaces.
        script = [("tool", "run_command")] * 4 + [("content", "done")]
        captured: list[str] = []
        agent, mm = _make_agent(
            script,
            ["boom: No such file or directory\n[exit code: 2]"] * 4,
            captured,
        )
        async for _ in agent.run_stream("build it"):
            pass

        lessons = _reflection_lessons(mm)
        assert len(lessons) == 1
        lesson = lessons[0].kwargs["lesson"]
        assert "boom: No such file or directory" in lesson
        assert "exit_code=2" in lesson
        assert "need a new strategy" not in lesson

    @pytest.mark.asyncio
    async def test_no_lesson_below_the_threshold(self, isolated_env):
        script = [("tool", "run_command"), ("content", "done")]
        captured: list[str] = []
        agent, mm = _make_agent(script, ["boom\n[exit code: 1]"], captured)
        async for _ in agent.run_stream("try once"):
            pass

        assert _reflection_lessons(mm) == []

    @pytest.mark.asyncio
    async def test_a_stale_flag_cannot_license_a_lesson(self, isolated_env):
        # A turn that ends on an exception leaves the flag armed while the
        # tracker is reset for the next turn. Without clearing it, unrelated
        # content would be stored as a failure diagnosis.
        script = [("content", "all good here")]
        captured: list[str] = []
        agent, mm = _make_agent(script, [], captured)
        agent._reflection_hint_injected = True

        async for _ in agent.run_stream("unrelated task"):
            pass

        assert _reflection_lessons(mm) == []


class TestTurnToolsDedupe:
    @pytest.mark.asyncio
    async def test_same_tool_tracked_once(self, isolated_env):
        script = [
            ("tool", "run_command"),
            ("tool", "run_command"),
            ("content", "done"),
        ]
        captured: list[str] = []
        agent, mm = _make_agent(script, ["ok", "ok"], captured)

        async for _ in agent.run_stream("twice"):
            pass

        assert agent._current_turn_tools == ["run_command"]
        assert agent._turn_tool_successes == 2
        assert agent._turn_tool_failures == 0


# ---------------------------------------------------------------------------
# Fallback hints (tool_chains)
# ---------------------------------------------------------------------------


class TestFallbackHints:
    def test_write_file_has_no_shell_fallback(self):
        # Suggesting run_command echo/cat would contradict system rule 15.
        assert get_fallback_hints("write_file") == ""

    def test_read_file_suggests_path_verification(self):
        hint = get_fallback_hints("read_file")
        assert "list_directory" in hint

    def test_search_files_still_suggests_grep(self):
        assert "grep" in get_fallback_hints("search_files")

    def test_unknown_tool_has_no_hint(self):
        assert get_fallback_hints("nonexistent") == ""


# ---------------------------------------------------------------------------
# Oneshot read-only hard stop (stagnation gate)
# ---------------------------------------------------------------------------


class TestOneshotReadonlyHardStop:
    @pytest.mark.asyncio
    async def test_hard_stop_breaks_loop(self, isolated_env):
        # read_file is classified read-only; with cap=3 the loop must stop
        # right after the 3rd read-only call, before any further LLM call.
        script = [
            ("tool", "read_file"),
            ("tool", "read_file"),
            ("tool", "read_file"),
            ("content", "should never run"),
        ]
        captured: list[str] = []
        agent, mm = _make_agent(script, ["ok"] * 3, captured)
        agent.oneshot = True
        agent.readonly_hard_cap = 3

        chunks = [c async for c in agent.run_stream("investigate")]
        text = "".join(chunks)
        assert "Stopped" in text
        assert "should never run" not in text
        # 3 tool iterations; the 4th iteration hits the gate before the LLM.
        assert len(captured) == 3

    @pytest.mark.asyncio
    async def test_mutating_call_avoids_hard_stop(self, isolated_env):
        # A write resets the read-only streak, so the run completes normally.
        script = [
            ("tool", "read_file"),
            ("tool", "read_file"),
            ("tool", "write_file"),
            ("tool", "read_file"),
            ("content", "done"),
        ]
        captured: list[str] = []
        agent, mm = _make_agent(script, ["ok"] * 4, captured)
        agent.oneshot = True
        agent.readonly_hard_cap = 3

        chunks = [c async for c in agent.run_stream("do it")]
        text = "".join(chunks)
        assert "done" in text
        assert "Stopped" not in text

    @pytest.mark.asyncio
    async def test_interactive_mode_no_hard_stop(self, isolated_env):
        # oneshot=False (default): streak only yields a nudge, never a stop.
        script = [
            ("tool", "read_file"),
            ("tool", "read_file"),
            ("tool", "read_file"),
            ("tool", "read_file"),
            ("content", "finished"),
        ]
        captured: list[str] = []
        agent, mm = _make_agent(script, ["ok"] * 4, captured)
        agent.readonly_hard_cap = 3  # cap set but not oneshot

        chunks = [c async for c in agent.run_stream("look around")]
        text = "".join(chunks)
        assert "finished" in text
        assert "Stopped" not in text


# ---------------------------------------------------------------------------
# Structural interruption (A8): a futile call is refused, not nudged
# ---------------------------------------------------------------------------


def _tool_messages(agent):
    return [m for m in agent.messages if m["role"] == "tool"]


def _pairing_is_intact(agent) -> bool:
    """Every assistant tool_call id has exactly one matching tool response."""
    pending = [
        tc["id"]
        for m in agent.messages
        if m.get("role") == "assistant"
        for tc in m.get("tool_calls", [])
    ]
    answered = [m.get("tool_call_id") for m in _tool_messages(agent)]
    return sorted(pending) == sorted(answered)


class TestFutilityRefusal:
    """A nudge the model can ignore is what the loop already had. These check
    the interruption is structural: the call does not run."""

    @pytest.mark.asyncio
    async def test_identical_failure_stops_reaching_the_executor(
        self,
        isolated_env,
    ):
        script = [("same_tool", "run_command")] * 4 + [("content", "gave up")]
        agent, _mm = _make_agent(
            script,
            ["FAILED test_a.py\n[exit code: 1]"] * 4,
            [],
        )
        async for _ in agent.run_stream("fix it"):
            pass

        assert agent.executor.execute_detailed.await_count == 2

    @pytest.mark.asyncio
    async def test_refused_call_still_answers_its_tool_call(
        self,
        isolated_env,
    ):
        # Short-circuiting must not drop the tool response: an unanswered
        # tool_call id is a 400 from every provider on the next request.
        script = [("same_tool", "run_command")] * 4 + [("content", "gave up")]
        agent, _mm = _make_agent(
            script,
            ["FAILED test_a.py\n[exit code: 1]"] * 4,
            [],
        )
        async for _ in agent.run_stream("fix it"):
            pass

        assert len(_tool_messages(agent)) == 4
        assert _pairing_is_intact(agent)
        assert "Not executed" in _tool_messages(agent)[2]["content"]

    @pytest.mark.asyncio
    async def test_state_change_lets_the_same_command_run_again(
        self,
        isolated_env,
    ):
        # The false positive that would make this feature worse than the loop:
        # re-running the suite after an edit is the correct thing to do.
        script = [
            ("same_tool", "run_command"),
            ("tool", "write_file"),
            ("same_tool", "run_command"),
            ("content", "done"),
        ]
        agent, _mm = _make_agent(
            script,
            ["FAILED\n[exit code: 1]", "written", "passed"],
            [],
        )
        async for _ in agent.run_stream("fix it"):
            pass

        assert agent.executor.execute_detailed.await_count == 3

    @pytest.mark.asyncio
    async def test_refused_call_never_reaches_the_permission_hook(
        self,
        isolated_env,
    ):
        # The check sits before the hook on purpose: prompting the user to
        # approve a call that will not run wastes their attention.
        script = [("same_tool", "run_command")] * 3 + [("content", "stop")]
        agent, _mm = _make_agent(script, ["boom\n[exit code: 1]"] * 3, [])
        agent.hook_bus = MagicMock()
        agent.hook_bus.dispatch_async = AsyncMock(
            return_value=MagicMock(blocked=False, confirm=False),
        )
        async for _ in agent.run_stream("fix it"):
            pass

        events = [
            c.args[0].event
            for c in agent.hook_bus.dispatch_async.await_args_list
        ]
        assert events.count("before_tool_call") == 2

    @pytest.mark.asyncio
    async def test_refusal_is_audited_as_a_denial(
        self,
        isolated_env,
        monkeypatch,
    ):
        # A call the agent decided not to run is still a decision about a
        # tool, so it belongs in the audit log next to the hook denials.
        audit = MagicMock()
        monkeypatch.setattr(
            "dashscope_agenticx.audit.get_audit_logger",
            lambda: audit,
        )
        script = [("same_tool", "run_command")] * 3 + [("content", "stop")]
        agent, _mm = _make_agent(script, ["boom\n[exit code: 1]"] * 3, [])
        async for _ in agent.run_stream("fix it"):
            pass

        denied = [
            c
            for c in audit.log_tool_call.call_args_list
            if c.kwargs.get("decision") == "denied"
        ]
        assert len(denied) == 1
        assert "futility" in denied[0].kwargs["reason"]

    @pytest.mark.asyncio
    async def test_replan_demand_follows_the_refusal(self, isolated_env):
        # The refusal is a tool result, so it can be truncated or evicted
        # later in a long turn. The next request repeats the demand as a
        # trailing user message, where it cannot be lost.
        script = [("same_tool", "run_command")] * 4 + [
            ("content", "replanned"),
        ]
        requests: list[list[dict]] = []
        agent, _mm = _make_agent(
            script,
            ["boom\n[exit code: 1]"] * 4,
            [],
            requests,
        )
        async for _ in agent.run_stream("fix it"):
            pass

        assert requests[3][-1]["role"] == "user"
        assert (
            "Stopped: this call is being repeated"
            in requests[3][-1]["content"]
        )


# ---------------------------------------------------------------------------
# Oneshot autonomous-mode task policy (iterate against measurable criteria)
# ---------------------------------------------------------------------------


class TestOneshotTaskPolicy:
    def test_oneshot_prompt_contains_iteration_policy(self, isolated_env):
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=True,
        )
        assert "Task execution policy" in agent.system_prompt
        # Work-discipline clause: plan file, progress recorded per step,
        # remaining items re-checked before finishing, no all-thinking turns.
        assert "written plan" in agent.system_prompt
        assert "land something on disk" in agent.system_prompt
        # Requirement-enumeration clause: each stated requirement becomes
        # its own executable check; domain facts are stated explicitly.
        assert "every stated requirement" in agent.system_prompt
        assert "executable check" in agent.system_prompt
        assert "domain facts" in agent.system_prompt
        # Long-command budget clause: probe before betting minutes on an
        # install/compile/download; explicit timeouts fail fast.
        assert "long unverified command" in agent.system_prompt
        assert "fails fast" in agent.system_prompt

    def test_interactive_prompt_lacks_policy(self, isolated_env):
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="base prompt",
            oneshot=False,
        )
        assert "Task execution policy" not in agent.system_prompt


class TestBasePromptRules:
    def test_grader_tool_verification_clause(self):
        # dna-insert-fix (2026-10-12): the agent picked annealing lengths
        # with its own Tm implementation "calibrated" against Tm_NN and
        # missed the grader's oligotm ΔTm bound. Verify with the named
        # ground-truth tool, not a substitute.
        assert "ground-truth tool" in SYSTEM_PROMPT
        assert "calibrated substitute" in SYSTEM_PROMPT

    def test_forensic_snapshot_clause(self):
        # db-wal-recovery (2026-10-12__medium12): a read-only sqlite3 open
        # made SQLite delete the corrupt WAL on close; the update inside it
        # was lost and the agent fell back to guessing. Snapshot first.
        assert "Snapshot a fragile artifact" in SYSTEM_PROMPT
        assert "immutable" in SYSTEM_PROMPT

    def test_vision_perception_clause(self):
        # solve-sudoku / gcode-to-text (2026-10-12__fix-071): both agents
        # approximated image perception with terminal ASCII renders and
        # hand-rolled CV while the registered view_image tool (qwen-vl-max
        # fallback on the same key) went unused for the whole run.
        assert "Read images with the vision model" in SYSTEM_PROMPT
        assert "view_image" in SYSTEM_PROMPT
        assert "render it to a PNG first" in SYSTEM_PROMPT

    def test_view_image_description_covers_data_renders(self):
        # The old description steered the tool at screenshots/photos, so
        # the model never considered it for task images or self-rendered
        # artifacts.
        import inspect

        from dashscope_agenticx.tools.vision import (
            register_vision_tools,
        )  # noqa: F401

        src = inspect.getsource(register_vision_tools)
        assert "whenever a task" in src
        assert "rendered yourself" in src

    def test_oneshot_registers_vision_tools(self):
        # 071k postmortem: _run_oneshot wired session/evolution/skill
        # tools but never vision, so every bench run executed with no
        # view_image in the schema — the prompt named a tool the model
        # could not see — and auto_perceive had no perception context
        # (auto_perceive_count stayed 0, tips never suppressed).
        import inspect

        from dashscope_agenticx.cli import runners
        from dashscope_agenticx.runtime import wire_agent_runtime

        oneshot_src = inspect.getsource(runners._run_oneshot)
        wiring_src = inspect.getsource(wire_agent_runtime)
        assert "RuntimeBuilder" in oneshot_src
        assert "register_vision_tools" in wiring_src


# ---------------------------------------------------------------------------
# Turn-level retry: transient model-API failures must not abort a long run
# ---------------------------------------------------------------------------


def _flaky_agent(provider, monkeypatch, slept):
    async def fake_sleep(s):
        slept.append(s)

    monkeypatch.setattr(asyncio, "sleep", fake_sleep)
    # _auto_compress reads self.provider.chat even when it won't compress.
    provider.chat = AsyncMock()
    memory_manager = MagicMock()
    memory_manager.session.reflection = ReflectionTracker(threshold=3)
    memory_manager.session.stagnation = StagnationTracker()
    memory_manager.session.tool_chains.get_fallback_hints.return_value = ""
    executor = MagicMock()
    executor.execute_detailed = AsyncMock(
        return_value=classify_tool_text("ok"),
    )
    agent = Agent(
        provider=provider,
        executor=executor,
        memory_manager=memory_manager,
        system_prompt="base",
        oneshot=True,
    )
    agent._system_prompt_for_turn = lambda _t: "base"
    return agent


class TestAgentTurnRetry:
    @pytest.mark.asyncio
    async def test_transient_error_retries_turn_then_succeeds(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = {"n": 0}

        class FlakyProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RuntimeError(
                        "Cannot connect to API server; check network",
                    )
                yield LLMChunk(delta_content="recovered")

        slept: list[float] = []
        agent = _flaky_agent(FlakyProvider(), monkeypatch, slept)
        text = "".join([c async for c in agent.run_stream("do it")])
        assert "recovered" in text
        assert calls["n"] == 2  # retried the turn exactly once
        assert slept and slept[0] == agent.turn_retry_delay

    @pytest.mark.asyncio
    async def test_persistent_error_exhausts_retries_then_raises(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = {"n": 0}

        class DeadProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                raise RuntimeError("Cannot connect to API server")
                yield  # pragma: no cover - makes this an async generator

        slept: list[float] = []
        agent = _flaky_agent(DeadProvider(), monkeypatch, slept)
        agent.turn_retry_max = 2
        with pytest.raises(RuntimeError, match="Cannot connect"):
            async for _ in agent.run_stream("do it"):
                pass
        assert calls["n"] == 3  # 1 initial + 2 retries

    @pytest.mark.asyncio
    async def test_non_retryable_error_propagates_immediately(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = {"n": 0}

        class AuthFailProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                raise RuntimeError("invalid api key")
                yield  # pragma: no cover

        slept: list[float] = []
        agent = _flaky_agent(AuthFailProvider(), monkeypatch, slept)
        with pytest.raises(RuntimeError, match="invalid api key"):
            async for _ in agent.run_stream("do it"):
                pass
        assert calls["n"] == 1  # no retry for non-retryable

    @pytest.mark.asyncio
    async def test_slow_exhausted_budget_is_not_re_paid(
        self,
        isolated_env,
        monkeypatch,
    ):
        """A provider that stalled through its whole budget is not retried.

        Every production provider arrives wrapped in HardenedProvider, so
        re-running its budget here multiplied max_retries x request_timeout
        by turn_retry_max: measured 12 HTTP attempts and ~819s of silent
        wall clock in one turn against a 60s configured timeout.
        """
        calls = {"n": 0}

        class ExhaustedProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                raise RetriesExhausted(
                    RuntimeError(
                        "API request timed out; check network",
                    ),
                    268.0,
                )
                yield  # pragma: no cover - makes this an async generator

        slept: list[float] = []
        agent = _flaky_agent(ExhaustedProvider(), monkeypatch, slept)
        agent.turn_retry_max = 2
        with pytest.raises(RetriesExhausted):
            async for _ in agent.run_stream("do it"):
                pass
        assert calls["n"] == 1  # the turn-level retry stayed out of it
        assert not slept

    @pytest.mark.asyncio
    async def test_fast_exhausted_budget_is_still_retried(
        self,
        isolated_env,
        monkeypatch,
    ):
        """A budget spent failing fast leaves the turn retry in play.

        Skipping on the type alone was a regression: a refused connection
        costs only the ~28s of backoff, and re-attempting is what rides out
        the transient blip. A bench trial died this way 33s into a 1800s
        budget with zero API calls recorded.
        """
        calls = {"n": 0}

        class RefusedProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                if calls["n"] == 1:
                    raise RetriesExhausted(
                        RuntimeError(
                            "Cannot connect to API server; check network",
                        ),
                        28.0,
                    )
                yield LLMChunk(delta_content="recovered")

        slept: list[float] = []
        agent = _flaky_agent(RefusedProvider(), monkeypatch, slept)
        agent.turn_retry_max = 2
        text = "".join([c async for c in agent.run_stream("do it")])
        assert "recovered" in text
        assert calls["n"] == 2
        assert slept and slept[0] == agent.turn_retry_delay

    @pytest.mark.asyncio
    async def test_mid_stream_break_keeps_the_partial_turn(
        self,
        isolated_env,
        monkeypatch,
    ):
        """A retryable error after chunks were yielded degrades, not dies.

        No layer retries it — re-requesting would duplicate the output
        already written — so it used to escape run_stream and kill the
        process, taking the rest of the run's budget with it.
        """
        calls = {"n": 0}

        class BreakingProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                yield LLMChunk(delta_content="partial work")
                raise RuntimeError("peer closed connection; network")

        agent = _flaky_agent(BreakingProvider(), monkeypatch, [])
        text = "".join([c async for c in agent.run_stream("do it")])
        assert "partial work" in text
        assert "Stream interrupted" in text
        assert calls["n"] == 1  # never re-requested, so nothing duplicated

    @pytest.mark.asyncio
    async def test_mid_stream_non_retryable_break_still_propagates(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = {"n": 0}

        class AuthBreakProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                yield LLMChunk(delta_content="partial work")
                raise RuntimeError("invalid api key")

        agent = _flaky_agent(AuthBreakProvider(), monkeypatch, [])
        with pytest.raises(RuntimeError, match="invalid api key"):
            async for _ in agent.run_stream("do it"):
                pass
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_reasoning_only_stall_retries_the_turn(
        self,
        isolated_env,
        monkeypatch,
    ):
        """Reasoning chunks are not user-visible, so they must not block
        the turn retry.

        Thinking models stream reasoning first; a stall in that window
        used to set `emitted`, skip both retry layers, and land in the
        empty-turn exit — dna-insert died at 562/1800s with zero
        deliverable (2026-10-12__fix-071). Retrying duplicates nothing
        the user ever saw.
        """
        calls = {"n": 0}

        class ThinkingStallProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                if calls["n"] == 1:
                    yield LLMChunk(delta_reasoning_content="let me think")
                    raise RuntimeError("peer closed connection; network")
                yield LLMChunk(delta_content="recovered")

        slept: list[float] = []
        agent = _flaky_agent(ThinkingStallProvider(), monkeypatch, slept)
        text = "".join([c async for c in agent.run_stream("do it")])
        assert "recovered" in text
        assert "Stream interrupted" not in text
        assert calls["n"] == 2
        assert slept and slept[0] == agent.turn_retry_delay

    @pytest.mark.asyncio
    async def test_reasoning_then_content_stall_keeps_partial_no_retry(
        self,
        isolated_env,
        monkeypatch,
    ):
        """Reasoning followed by visible content still counts as emitted."""
        calls = {"n": 0}

        class ThinkingBreakProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                yield LLMChunk(delta_reasoning_content="let me think")
                yield LLMChunk(delta_content="partial work")
                raise RuntimeError("peer closed connection; network")

        agent = _flaky_agent(ThinkingBreakProvider(), monkeypatch, [])
        text = "".join([c async for c in agent.run_stream("do it")])
        assert "partial work" in text
        assert "Stream interrupted" in text
        assert calls["n"] == 1

    @pytest.mark.asyncio
    async def test_oneshot_empty_turn_nudges_and_continues(
        self,
        isolated_env,
        monkeypatch,
    ):
        """A clean-but-empty stop is a provider failure shape, not 'done'.

        The stall that slipped both retry layers ended the run with
        budget left; in oneshot the loop must nudge and continue instead
        of breaking on the first empty turn.
        """
        calls = {"n": 0}

        class EmptyThenDoneProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                if calls["n"] == 1:
                    yield LLMChunk(delta_reasoning_content="hmm")
                    yield LLMChunk(finish_reason="stop")
                else:
                    yield LLMChunk(delta_content="done")

        agent = _flaky_agent(EmptyThenDoneProvider(), monkeypatch, [])
        text = "".join([c async for c in agent.run_stream("do it")])
        assert "done" in text
        assert "Empty response" in text
        assert calls["n"] == 2

    @pytest.mark.asyncio
    async def test_empty_turn_backstop_is_bounded(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = {"n": 0}

        class AlwaysEmptyProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                yield LLMChunk(finish_reason="stop")

        agent = _flaky_agent(AlwaysEmptyProvider(), monkeypatch, [])
        text = "".join([c async for c in agent.run_stream("do it")])
        assert "Model returned no content" in text
        assert calls["n"] == 1 + agent._empty_turn_max

    @pytest.mark.asyncio
    async def test_interactive_empty_turn_breaks_without_nudge(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = {"n": 0}

        class EmptyProvider:
            async def chat_stream(
                self,
                messages,
                tools=None,
                response_format=None,
            ):
                calls["n"] += 1
                yield LLMChunk(finish_reason="stop")

        slept: list[float] = []
        agent = _flaky_agent(EmptyProvider(), monkeypatch, slept)
        agent.oneshot = False
        text = "".join([c async for c in agent.run_stream("do it")])
        assert "Model returned no content" in text
        assert "Empty response" not in text
        assert calls["n"] == 1


# ---------------------------------------------------------------------------
# Budget-aware convergence nudge: productive-but-plateaued iteration
# ---------------------------------------------------------------------------


class TestConvergenceHint:
    def test_no_hint_below_soft_ratio(self):
        assert convergence_hint(0, 100) == ""
        assert convergence_hint(58, 100) == ""  # used 59 → 0.59 < 0.60

    def test_soft_boundary(self):
        # used = loop_index + 1; soft fires at used/max >= 0.60.
        h = convergence_hint(59, 100)  # used 60 → 0.60
        assert "Budget check" in h
        assert "60/100" in h
        assert "converge now" not in h  # not yet the hard band

    def test_soft_band_switch_approach(self):
        h = convergence_hint(70, 100)  # used 71 → 0.71
        assert "structurally different strategy" in h
        assert "ALREADY met" in h  # lock-in-a-passing-result clause
        assert "converge now" not in h

    def test_hard_boundary(self):
        h = convergence_hint(84, 100)  # used 85 → 0.85
        assert "converge now" in h

    def test_hard_band_finalize(self):
        h = convergence_hint(95, 100)
        assert "converge now" in h
        assert "ONE final verification" in h
        assert "Do NOT start a new approach" in h
        assert "Budget check" not in h  # hard band, not soft

    def test_custom_ratios(self):
        soft = convergence_hint(30, 100, soft_ratio=0.25, hard_ratio=0.5)
        assert "Budget check" in soft  # 0.31 in [0.25, 0.5)
        hard = convergence_hint(50, 100, soft_ratio=0.25, hard_ratio=0.5)
        assert "converge now" in hard  # 0.51 >= 0.5

    def test_disabled_when_soft_ratio_ge_1(self):
        assert convergence_hint(99, 100, soft_ratio=1.0) == ""

    def test_max_turns_zero_is_safe_noop(self):
        assert convergence_hint(5, 0) == ""


class TestAgentConvergenceSection:
    def test_interactive_never_nudges(self, isolated_env):
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=False,
        )
        agent.max_turns = 10
        assert agent._convergence_section(9) == ""

    def test_oneshot_nudges_near_cap_and_silent_early(self, isolated_env):
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=True,
        )
        agent.max_turns = 10
        agent.converge_soft_ratio = 0.6
        agent.converge_hard_ratio = 0.85
        assert agent._convergence_section(0) == ""  # used 1/10
        late = agent._convergence_section(9)  # used 10/10 → hard
        assert "converge now" in late

    def test_oneshot_disabled_when_soft_ratio_ge_1(self, isolated_env):
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=True,
        )
        agent.max_turns = 10
        agent.converge_soft_ratio = 1.0
        assert agent._convergence_section(9) == ""


# ---------------------------------------------------------------------------
# Wall-clock budget nudge: secure the deliverable before the harness kills
# the run (the few-but-huge-generations shape the turn-count nudge misses)
# ---------------------------------------------------------------------------


class TestTimeBudgetHint:
    def test_no_hint_below_soft_ratio(self):
        assert time_budget_hint(100, 900) == ""
        assert time_budget_hint(449, 900) == ""  # 0.499 < 0.5

    def test_soft_boundary_is_deliverable_first(self):
        h = time_budget_hint(450, 900)  # 0.50
        assert "half spent" in h
        assert "450s of your ~900s budget" in h
        assert "(450s left)" in h
        assert "best-effort version NOW" in h
        # Explicitly not the turn nudge's "converge" wording: the failure
        # shape here is a missing artifact, not a plateaued loop.
        assert "converge" not in h
        assert "nearly exhausted" not in h

    def test_soft_band_existing_deliverable_clause(self):
        h = time_budget_hint(600, 900)
        assert "re-save after every improvement" in h

    def test_hard_boundary(self):
        h = time_budget_hint(720, 900)  # 0.80
        assert "nearly exhausted" in h
        assert "best version" in h
        assert "ONE final verification" in h
        assert "Do NOT start a new approach" in h
        assert "half spent" not in h

    def test_hard_band_forces_partial_write_on_empty_disk(self):
        # solve-sudoku in 2026-10-12__medium12 burned the whole budget with
        # the solution file never created: "best version on disk" meant
        # nothing to an agent that had written nothing. The hard band must
        # demand a partial/placeholder write, since an empty path scores 0.
        h = time_budget_hint(720, 900)
        assert "not on disk yet" in h
        assert "placeholder version can still score" in h
        assert "scores zero" in h

    def test_remaining_clamps_at_zero(self):
        h = time_budget_hint(1000, 900)
        assert "(0s left)" in h

    def test_custom_ratios(self):
        assert "half spent" in time_budget_hint(200, 900, soft_ratio=0.2)
        hard = time_budget_hint(230, 900, soft_ratio=0.2, hard_ratio=0.25)
        assert "nearly exhausted" in hard

    def test_disabled_without_budget(self):
        assert time_budget_hint(100, 0) == ""
        assert time_budget_hint(100, -5) == ""


class TestAgentTimeBudgetSection:
    def _agent(self, oneshot):
        return Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=oneshot,
        )

    def test_interactive_never_nudges(self, isolated_env):
        agent = self._agent(oneshot=False)
        agent.time_budget_sec = 100
        agent._run_started = time.monotonic() - 95  # 95% spent
        assert agent._time_budget_section() == ""

    def test_unset_budget_is_silent(self, isolated_env):
        agent = self._agent(oneshot=True)
        agent.time_budget_sec = 0
        agent._run_started = time.monotonic() - 9999
        assert agent._time_budget_section() == ""

    def test_run_not_started_is_silent(self, isolated_env):
        agent = self._agent(oneshot=True)
        agent.time_budget_sec = 100
        assert agent._time_budget_section() == ""

    def test_oneshot_soft_band_fires(self, isolated_env):
        agent = self._agent(oneshot=True)
        agent.time_budget_sec = 100
        agent.time_soft_ratio = 0.5
        agent.time_hard_ratio = 0.8
        agent._run_started = time.monotonic() - 60  # 60% spent
        assert "half spent" in agent._time_budget_section()

    def test_oneshot_hard_band_fires(self, isolated_env):
        agent = self._agent(oneshot=True)
        agent.time_budget_sec = 100
        agent.time_soft_ratio = 0.5
        agent.time_hard_ratio = 0.8
        agent._run_started = time.monotonic() - 85  # 85% spent
        assert "nearly exhausted" in agent._time_budget_section()


class TestCompressionHint:
    def test_no_event_below_warn_ratio_is_silent(self):
        assert compression_hint(None, 0.0) == ""
        assert compression_hint(None, 0.39) == ""

    def test_event_fires_notice_with_counts(self):
        h = compression_hint((120, 40), 0.0)
        assert "just compressed" in h
        assert "120 to 40 messages" in h
        # The notice exists because compressed-away state must be re-fetched
        # or persisted, not remembered.
        assert "Re-read any file" in h
        assert "write it to disk now" in h

    def test_event_wins_over_ratio(self):
        # A post-compression ratio still in the warn band must not stack a
        # pre-hint on top of the event notice.
        h = compression_hint((120, 40), 0.45)
        assert "just compressed" in h
        assert "filling up" not in h

    def test_pre_hint_at_warn_ratio(self):
        h = compression_hint(None, 0.40)
        assert "filling up" in h
        assert "40% of the context window" in h
        assert "compression is approaching" in h
        assert "disk NOW" in h

    def test_pre_hint_boundary_just_below_is_silent(self):
        assert compression_hint(None, 0.399) == ""

    def test_custom_warn_ratio(self):
        assert "filling up" in compression_hint(None, 0.2, warn_ratio=0.1)


class TestAgentCompressionSection:
    def _agent(self):
        return Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=True,
        )

    def test_event_is_spent_once(self, isolated_env):
        agent = self._agent()
        agent._compression_event = (120, 40)
        assert "just compressed" in agent._compression_section()
        # Second section build in the same turn must not repeat the notice.
        assert agent._compression_section() == ""

    def test_ratio_band_fires(self, isolated_env):
        agent = self._agent()
        agent._context_ratio = 0.5
        assert "filling up" in agent._compression_section()

    def test_cold_start_is_silent(self, isolated_env):
        agent = self._agent()
        assert agent._compression_section() == ""


class TestPrefixCharsTrace:
    """prefix_chars measures the cacheable request prefix: every prompt
    bucket except the volatile trailing hint."""

    _BUCKETS = {
        "system_chars": 4,
        "user_chars": 10,
        "assistant_chars": 6,
        "tool_result_chars": 20,
        "tool_call_args_chars": 5,
        "tools_schema_chars": 100,
    }

    def _last_prompt_stats(self, agent):
        return agent.trace_logger.log_llm_call.call_args.kwargs["prompt_stats"]

    @pytest.mark.asyncio
    async def test_prefix_chars_sum_buckets_when_no_hint(self, isolated_env):
        script = [("content", "done")]
        agent, _mm = _make_agent(script, [], [], [])
        agent.executor.record_prompt_composition.return_value = dict(
            self._BUCKETS,
        )
        _ = [c async for c in agent.run_stream("do something")]
        assert self._last_prompt_stats(agent)["prefix_chars"] == sum(
            self._BUCKETS.values(),
        )

    @pytest.mark.asyncio
    async def test_prefix_chars_subtracts_trailing_hint(self, isolated_env):
        script = [("content", "done")]
        agent, _mm = _make_agent(script, [], [], [])
        agent.executor.record_prompt_composition.return_value = dict(
            self._BUCKETS,
        )
        agent._compression_event = (120, 40)
        _ = [c async for c in agent.run_stream("do something")]
        stats = self._last_prompt_stats(agent)
        raw_hint = compression_hint((120, 40), 0.0)
        # The trailing hint message carries the stripped text; hint_chars
        # logs the raw section length.
        assert stats["prefix_chars"] == sum(self._BUCKETS.values()) - len(
            raw_hint.strip(),
        )
        assert stats["hint_chars"] == {"compression": len(raw_hint)}


class TestCommandTimeoutClamp:
    """One run_command may not burn more than the remaining wall-clock
    budget minus the finalize reserve (reqenum-0612 lost 720s of 1800s
    to two command timeouts)."""

    def _agent(self, oneshot=True, budget=1000.0, elapsed=699.0):
        # elapsed sits 1s below the round number: the clamp recomputes
        # remaining a few milliseconds later, and int() truncation must
        # not turn an exact 300s remainder into 299.
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=oneshot,
        )
        agent.time_budget_sec = budget
        agent._run_started = time.monotonic() - elapsed
        return agent

    @staticmethod
    def _call(timeout=None):
        args = {"command": "make"}
        if timeout is not None:
            args["timeout"] = timeout
        return ToolCall(id="1", name="run_command", arguments=args)

    def test_default_timeout_clamped_to_ceiling(self, isolated_env):
        agent = self._agent()  # remaining 300, reserve 180 -> ceiling 120
        call = self._call()
        note = agent._clamp_run_command_timeout(call)
        assert call.arguments["timeout"] == 120
        assert "clamped from 600s to 120s" in note
        assert "300s of wall-clock budget remain" in note

    def test_explicit_timeout_above_ceiling_clamped(self, isolated_env):
        agent = self._agent(elapsed=820.0)  # remaining 180 -> ceiling 30
        call = self._call(timeout=600)
        note = agent._clamp_run_command_timeout(call)
        assert call.arguments["timeout"] == 30
        assert "clamped from 600s to 30s" in note

    def test_below_ceiling_untouched(self, isolated_env):
        agent = self._agent()  # ceiling 120
        call = self._call(timeout=60)
        note = agent._clamp_run_command_timeout(call)
        assert call.arguments["timeout"] == 60
        assert note == ""

    def test_floor_is_min_even_when_reserve_exceeds_remaining(
        self,
        isolated_env,
    ):
        agent = self._agent(elapsed=900.0)  # remaining 100 < reserve
        call = self._call()
        note = agent._clamp_run_command_timeout(call)
        assert call.arguments["timeout"] == agent._CMD_TIMEOUT_MIN_SEC
        assert "clamped" in note

    def test_interactive_never_clamps(self, isolated_env):
        agent = self._agent(oneshot=False)
        call = self._call(timeout=600)
        assert agent._clamp_run_command_timeout(call) == ""
        assert call.arguments["timeout"] == 600

    def test_unset_budget_never_clamps(self, isolated_env):
        agent = self._agent(budget=0.0)
        call = self._call()
        assert agent._clamp_run_command_timeout(call) == ""
        assert "timeout" not in call.arguments

    def test_run_not_started_never_clamps(self, isolated_env):
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=True,
        )
        agent.time_budget_sec = 1000.0
        call = self._call()
        assert agent._clamp_run_command_timeout(call) == ""
        assert "timeout" not in call.arguments

    def test_other_tools_untouched(self, isolated_env):
        agent = self._agent(elapsed=900.0)
        call = ToolCall(id="1", name="write_file", arguments={"path": "x"})
        assert agent._clamp_run_command_timeout(call) == ""
        assert call.arguments == {"path": "x"}

    def test_junk_timeout_falls_back_to_default_then_clamps(
        self,
        isolated_env,
    ):
        agent = self._agent()  # ceiling 120
        call = self._call(timeout="auto")
        note = agent._clamp_run_command_timeout(call)
        assert call.arguments["timeout"] == 120
        assert "clamped" in note


# ---------------------------------------------------------------------------
# Output cap: finish_reason=length continues the turn instead of ending it
# ---------------------------------------------------------------------------


class TestOutputCapContinuation:
    """A response cut by the per-call output cap is not a final answer.

    The cap exists for the monolithic-generation failure shape (one 15-minute
    response that never lands a deliverable): the partial goes into history,
    a shorter-chunks nudge rides the next request, and the loop continues —
    which is also what makes every between-iteration mechanism (time budget,
    convergence, reflection) reachable again.
    """

    @pytest.mark.asyncio
    async def test_cut_response_continues_with_nudge(self, isolated_env):
        script = [("length", "partial draft"), ("stop", "final answer")]
        captured: list[str] = []
        requests: list[list[dict]] = []
        agent, _mm = _make_agent(script, [], captured, requests)

        chunks = [c async for c in agent.run_stream("do something")]
        out = "".join(chunks)

        assert "partial draft" in out
        assert "Output-length limit reached" in out
        assert "final answer" in out
        # Two request boundaries: the cut turn and the continued one.
        assert len(requests) == 2
        # The partial and the nudge both ride the continued request.
        assert requests[1][-2] == {
            "role": "assistant",
            "content": "partial draft",
        }
        assert requests[1][-1]["role"] == "user"
        assert "Output-length limit reached" in requests[1][-1]["content"]
        # Both also land in persistent history.
        roles = [m["role"] for m in agent.messages]
        assert roles == ["user", "assistant", "user", "assistant"]
        assert agent.messages[1]["content"] == "partial draft"
        assert agent.messages[3]["content"] == "final answer"

    @pytest.mark.asyncio
    async def test_empty_cut_gets_placeholder(self, isolated_env):
        # A cap hit during planning emits nothing visible; history still
        # needs an assistant message so the next request pairs correctly.
        script = [("length", ""), ("stop", "done")]
        captured: list[str] = []
        requests: list[list[dict]] = []
        agent, _mm = _make_agent(script, [], captured, requests)

        chunks = [c async for c in agent.run_stream("do something")]
        assert "done" in "".join(chunks)

        assert agent.messages[1]["role"] == "assistant"
        assert agent.messages[1]["content"].startswith(
            "[response cut off by the output-length limit",
        )

    @pytest.mark.asyncio
    async def test_repeated_cuts_stay_bounded(self, isolated_env):
        # Continuations consume turn budget: a model that keeps hitting the
        # cap must not loop forever.
        script = [("length", f"chunk {i}") for i in range(3)]
        captured: list[str] = []
        requests: list[list[dict]] = []
        agent, _mm = _make_agent(script, [], captured, requests)
        agent.max_turns = 3

        chunks = [c async for c in agent.run_stream("do something")]
        out = "".join(chunks)

        assert len(requests) == 3
        assert out.count("Output-length limit reached") == 3


class TestTruncatedToolCall:
    """A call whose arguments were cut off is not executed.

    Executing it only earns a "missing required arguments" error and an
    identical retry, so the loop must instead tell the model to retry in
    smaller pieces (circuit-fibsqrt 2026-11-03: 3 × ~190s on one write_file).
    """

    @pytest.mark.asyncio
    async def test_cut_call_is_not_executed(self, isolated_env):
        script = [("truncated_tool", "write_file"), ("stop", "done")]
        captured: list[str] = []
        requests: list[list[dict]] = []
        agent, _mm = _make_agent(script, [], captured, requests)

        out = "".join([c async for c in agent.run_stream("do something")])

        assert "was NOT executed" in out
        assert "done" in out
        assert agent.executor.execute_detailed.await_count == 0
        assert requests[1][-1]["role"] == "user"
        assert "write_file" in requests[1][-1]["content"]
        assert "was NOT executed" in requests[1][-1]["content"]

    @pytest.mark.asyncio
    async def test_usable_sibling_still_runs(self, isolated_env):
        # A response can carry several calls; only the cut one is dropped.
        script = [
            ("mixed_tool", ("read_file", "write_file")),
            ("stop", "done"),
        ]
        captured: list[str] = []
        requests: list[list[dict]] = []
        agent, _mm = _make_agent(
            script,
            ["file contents"],
            captured,
            requests,
        )

        out = "".join([c async for c in agent.run_stream("do something")])

        assert "file contents" in out
        assert "was NOT executed" in out
        assert agent.executor.execute_detailed.await_count == 1
        assert "was NOT executed" in requests[1][-1]["content"]


# ---------------------------------------------------------------------------
# Silence nudge: a structural backstop for unheard tool calls
# ---------------------------------------------------------------------------


def _nudged(requests) -> int:
    """Requests whose tail hint carries the silence section."""
    return sum(1 for r in requests if "gone quiet" in r[-1]["content"])


class TestSilenceNudge:
    """The turn shape none of the other three sections catch.

    A run of *distinct* run_command calls trips nothing: StagnationTracker's
    read-only classifier counts run_command as mutating, so every call resets
    its streak; its repeat counters need byte-identical arguments and output;
    ReflectionTracker needs failures; and _convergence_section is oneshot-only.
    An interactive turn can therefore call tools for minutes with no prose
    reaching the user, which is what the transcript that motivated this did.
    """

    @pytest.mark.asyncio
    async def test_fires_in_interactive_mode(self, isolated_env):
        requests: list = []
        script = [("tool", "run_command")] * 6 + [("content", "found it")]
        agent, _mm = _make_agent(
            script,
            ["ok"] * 6,
            [],
            requests=requests,
        )
        assert agent.oneshot is False
        async for _ in agent.run_stream("look into this"):
            pass

        assert _nudged(requests) == 1
        # Not before the count is reached: the first six requests are the six
        # silent iterations, and the nudge rides on the seventh.
        assert all("gone quiet" not in r[-1]["content"] for r in requests[:6])

    @pytest.mark.asyncio
    async def test_spent_once_even_if_the_silence_continues(
        self,
        isolated_env,
    ):
        requests: list = []
        script = [("tool", "run_command")] * 10 + [("content", "done")]
        agent, _mm = _make_agent(
            script,
            ["ok"] * 10,
            [],
            requests=requests,
        )
        async for _ in agent.run_stream("look into this"):
            pass

        assert _nudged(requests) == 1

    @pytest.mark.asyncio
    async def test_streamed_prose_resets_the_count(self, isolated_env):
        # Explaining itself is the behaviour the nudge asks for, so a turn
        # that narrates every few calls must never be told to start.
        requests: list = []
        script = [
            *[("tool", "run_command")] * 4,
            ("narrated_tool", ("still looking", "run_command")),
            *[("tool", "run_command")] * 4,
            ("content", "done"),
        ]
        agent, _mm = _make_agent(
            script,
            ["ok"] * 9,
            [],
            requests=requests,
        )
        async for _ in agent.run_stream("look into this"):
            pass

        assert _nudged(requests) == 0

    @pytest.mark.asyncio
    async def test_a_new_turn_rearms_it(self, isolated_env):
        requests: list = []
        script = (
            [("tool", "run_command")] * 6
            + [("content", "one")]
            + [("tool", "run_command")] * 6
            + [("content", "two")]
        )
        agent, _mm = _make_agent(
            script,
            ["ok"] * 12,
            [],
            requests=requests,
        )
        async for _ in agent.run_stream("first"):
            pass
        async for _ in agent.run_stream("second"):
            pass

        assert _nudged(requests) == 2

    @pytest.mark.asyncio
    async def test_threshold_is_env_tunable(self, isolated_env, monkeypatch):
        monkeypatch.setenv("AGENTICX_SILENT_TOOL_NUDGE", "2")
        requests: list = []
        script = [("tool", "run_command")] * 2 + [("content", "done")]
        agent, _mm = _make_agent(
            script,
            ["ok"] * 2,
            [],
            requests=requests,
        )
        assert agent.silent_tool_nudge == 2
        async for _ in agent.run_stream("look into this"):
            pass

        assert _nudged(requests) == 1

    @pytest.mark.asyncio
    async def test_zero_disables_it(self, isolated_env, monkeypatch):
        monkeypatch.setenv("AGENTICX_SILENT_TOOL_NUDGE", "0")
        requests: list = []
        script = [("tool", "run_command")] * 10 + [("content", "done")]
        agent, _mm = _make_agent(
            script,
            ["ok"] * 10,
            [],
            requests=requests,
        )
        assert agent.silent_tool_nudge == 0
        async for _ in agent.run_stream("look into this"):
            pass

        assert _nudged(requests) == 0

    @pytest.mark.asyncio
    async def test_unparsable_env_falls_back(self, isolated_env, monkeypatch):
        monkeypatch.setenv("AGENTICX_SILENT_TOOL_NUDGE", "lots")
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
        )
        assert agent.silent_tool_nudge == 6

    def test_not_gated_on_oneshot(self, isolated_env):
        for oneshot in (False, True):
            agent = Agent(
                provider=MagicMock(),
                executor=MagicMock(),
                system_prompt="",
                oneshot=oneshot,
            )
            agent.silent_tool_nudge = 3
            assert agent._silence_section() == ""
            agent._silent_tool_calls = 2
            assert agent._silence_section() == ""
            agent._silent_tool_calls = 3
            assert "gone quiet" in agent._silence_section()
            # Spent: the second call returns nothing however quiet it gets.
            agent._silent_tool_calls = 20
            assert agent._silence_section() == ""


# ---------------------------------------------------------------------------
# Acceptance gate: the loop, not the model, decides when the turn may end
# ---------------------------------------------------------------------------

_GATE_PASS = "3 passed in 0.42s\n[exit code: 0]"


def _gate_fail(detail="FAILED tests/test_x.py::test_a - assert 1 == 2"):
    return f"{detail}\n[exit code: 1]"


def _arm_gate(monkeypatch, *outputs):
    """Arm the gate with an explicit command and script its shell outputs.

    Only the subprocess is faked: the gate still calls the real
    ``run_command``, so it keeps the blocked-pattern checks and the output
    caps, and ``classify_tool_text`` still reads the verdict off the
    ``[exit code: N]`` trailer. An unscripted run raises IndexError rather
    than passing silently, so a gate that fires when it should not is a
    failure.
    """
    scripted = list(outputs)
    calls: list[tuple[str, int | None]] = []

    async def _run_command(command, timeout=None):
        calls.append((command, timeout))
        return scripted.pop(0)

    monkeypatch.setenv("AGENTICX_ACCEPTANCE", "1")
    monkeypatch.setenv("AGENTICX_ACCEPTANCE_CMD", "pytest -q")
    monkeypatch.setattr(
        "dashscope_agenticx.tools.shell.run_command",
        _run_command,
    )
    monkeypatch.setattr(
        "dashscope_agenticx.audit.get_audit_logger",
        lambda: MagicMock(),
    )
    return calls


class TestAcceptanceGate:
    """The system prompt already asks the model to verify its work. These
    check the answer is withheld when the check says otherwise."""

    @pytest.mark.asyncio
    async def test_a_failing_check_sends_the_answer_back(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = _arm_gate(
            monkeypatch,
            _GATE_PASS,  # baseline, before any change
            _gate_fail(),  # gate rejects the first answer
            _GATE_PASS,  # gate accepts the second
        )
        script = [
            ("tool", "write_file"),
            ("content", "fixed it"),
            ("tool", "write_file"),
            ("content", "actually fixed it"),
        ]
        agent, _mm = _make_agent(script, ["ok"] * 2, [], oneshot=True)
        text = "".join([c async for c in agent.run_stream("fix it")])

        assert "Answer withheld" in text
        assert len(calls) == 3
        # The rejected answer stays in history as an attempt, with the raw
        # check output as the next requirement — dropping it would leave the
        # rejection quoting an attempt the model never sees.
        contents = [m.get("content", "") for m in agent.messages]
        assert "fixed it" in contents
        rejection = contents[contents.index("fixed it") + 1]
        assert "Not accepted" in rejection
        assert "FAILED tests/test_x.py::test_a" in rejection
        assert agent.messages[-1]["content"] == "actually fixed it"

    @pytest.mark.asyncio
    async def test_a_passing_check_lets_the_answer_through(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = _arm_gate(monkeypatch, _GATE_PASS, _GATE_PASS)
        script = [("tool", "write_file"), ("content", "done")]
        agent, _mm = _make_agent(script, ["ok"], [], oneshot=True)
        text = "".join([c async for c in agent.run_stream("fix it")])

        assert "Answer withheld" not in text
        assert "done" in text
        assert agent.messages[-1]["content"] == "done"
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_the_baseline_is_taken_before_the_change_lands(
        self,
        isolated_env,
        monkeypatch,
    ):
        order: list[str] = []

        async def _run_command(command, timeout=None):
            order.append("check")
            return _GATE_PASS

        monkeypatch.setenv("AGENTICX_ACCEPTANCE", "1")
        monkeypatch.setenv("AGENTICX_ACCEPTANCE_CMD", "pytest -q")
        monkeypatch.setattr(
            "dashscope_agenticx.tools.shell.run_command",
            _run_command,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.audit.get_audit_logger",
            lambda: MagicMock(),
        )

        script = [("tool", "write_file"), ("content", "done")]
        agent, _mm = _make_agent(script, ["ok"], [], oneshot=True)
        inner = agent.executor.execute_detailed

        async def _spy(tool_def, arguments):
            order.append("mutate")
            return await inner(tool_def, arguments)

        agent.executor.execute_detailed = _spy
        async for _ in agent.run_stream("fix it"):
            pass

        # Anchor, then change, then verdict — any other order and the anchor
        # describes a workspace that no longer exists.
        assert order == ["check", "mutate", "check"]

    @pytest.mark.asyncio
    async def test_a_read_only_turn_never_pays_for_the_check(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = _arm_gate(monkeypatch)
        script = [("tool", "read_file"), ("content", "here it is")]
        agent, _mm = _make_agent(script, ["ok"], [], oneshot=True)
        text = "".join([c async for c in agent.run_stream("what does it say")])

        assert calls == []
        assert "here it is" in text

    @pytest.mark.asyncio
    async def test_the_answer_is_flagged_once_the_budget_is_spent(
        self,
        isolated_env,
        monkeypatch,
    ):
        monkeypatch.setenv("AGENTICX_ACCEPTANCE_RETRIES", "0")
        calls = _arm_gate(monkeypatch, _GATE_PASS, _gate_fail())
        script = [("tool", "write_file"), ("content", "done")]
        agent, _mm = _make_agent(script, ["ok"], [], oneshot=True)
        text = "".join([c async for c in agent.run_stream("fix it")])

        # Latency is bounded, so an uncertified answer goes out rather than
        # looping forever — but it goes out saying so.
        assert "Answer withheld" not in text
        assert "Unverified" in text
        assert agent.messages[-1]["role"] == "assistant"
        assert "Unverified" in agent.messages[-1]["content"]
        assert len(calls) == 2

    @pytest.mark.asyncio
    async def test_interactive_runs_are_not_gated(
        self,
        isolated_env,
        monkeypatch,
    ):
        calls = _arm_gate(monkeypatch)
        script = [("tool", "write_file"), ("content", "done")]
        agent, _mm = _make_agent(script, ["ok"], [], oneshot=False)
        text = "".join([c async for c in agent.run_stream("fix it")])

        assert calls == []
        assert "done" in text

    def test_the_armed_gate_is_named_in_the_policy_prompt(
        self,
        isolated_env,
        monkeypatch,
    ):
        _arm_gate(monkeypatch)
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=True,
        )
        # Naming the command lets the model run it first instead of meeting
        # the gate at the exit; naming the prohibition closes off the cheapest
        # way to make a gate pass, which is deleting it.
        assert "`pytest -q`" in agent.system_prompt
        assert "weakening the check itself" in agent.system_prompt

    def test_a_disarmed_gate_makes_no_promise_in_the_prompt(
        self,
        isolated_env,
    ):
        agent = Agent(
            provider=MagicMock(),
            executor=MagicMock(),
            system_prompt="",
            oneshot=True,
        )
        assert "Verification gate" not in agent.system_prompt


# ---------------------------------------------------------------------------
# run_stream entry: orphan-user dedup + failure/interrupt history trace
# ---------------------------------------------------------------------------


def _content_stream(text):
    async def _stream(messages, *args, **kwargs):
        yield LLMChunk(delta_content=text)

    return _stream


class TestRunStreamEntryAndFailureTrace:
    @pytest.mark.asyncio
    async def test_failed_turn_leaves_an_assistant_trace(self, isolated_env):
        # A provider failure with zero output used to leave only the user
        # message behind: N resubmits stacked as bare user prompts with no
        # trace of why each attempt failed.
        agent, _mm = _make_agent([], [], [])

        async def _fail(messages, *args, **kwargs):
            raise RuntimeError(
                "DashScope API error: 403 - Access denied, please make sure "
                "your account is in good standing",
            )
            yield  # pragma: no cover - async generator shape

        agent.provider.chat_stream = _fail

        with pytest.raises(RuntimeError, match="Access denied"):
            async for _ in agent.run_stream("why did this fail"):
                pass

        assert agent.messages[0] == {
            "role": "user",
            "content": "why did this fail",
        }
        assert agent.messages[1]["role"] == "assistant"
        assert agent.messages[1]["content"].startswith(
            "[Turn failed before any response: ",
        )
        assert "Access denied" in agent.messages[1]["content"]

    @pytest.mark.asyncio
    async def test_resubmit_reuses_orphan_user_message(self, isolated_env):
        agent, _mm = _make_agent([], [], [])

        async def _abort(messages, *args, **kwargs):
            raise KeyboardInterrupt
            yield  # pragma: no cover - async generator shape

        agent.provider.chat_stream = _abort
        with pytest.raises(KeyboardInterrupt):
            async for _ in agent.run_stream("same question"):
                pass
        # Interrupted before any output: no assistant trace, so the user
        # message is left as the last history entry.
        assert agent.messages == [{"role": "user", "content": "same question"}]

        agent.provider.chat_stream = _content_stream("answer")
        chunks = [c async for c in agent.run_stream("same question")]
        assert "answer" in "".join(chunks)
        # The orphan is reused as this turn's input, not duplicated.
        users = [m for m in agent.messages if m["role"] == "user"]
        assert users == [{"role": "user", "content": "same question"}]

    @pytest.mark.asyncio
    async def test_different_text_does_not_reuse_the_orphan(
        self,
        isolated_env,
    ):
        agent, _mm = _make_agent([], [], [])

        async def _abort(messages, *args, **kwargs):
            raise KeyboardInterrupt
            yield  # pragma: no cover - async generator shape

        agent.provider.chat_stream = _abort
        with pytest.raises(KeyboardInterrupt):
            async for _ in agent.run_stream("original question"):
                pass

        agent.provider.chat_stream = _content_stream("answer")
        async for _ in agent.run_stream("a different question"):
            pass
        users = [m["content"] for m in agent.messages if m["role"] == "user"]
        assert users == ["original question", "a different question"]

    @pytest.mark.asyncio
    async def test_markdown_only_partial_is_dropped(self, isolated_env):
        agent, _mm = _make_agent([], [], [])

        async def _fragment(messages, *args, **kwargs):
            yield LLMChunk(delta_content="**")
            raise KeyboardInterrupt

        agent.provider.chat_stream = _fragment
        chunks = []
        with pytest.raises(KeyboardInterrupt):
            async for c in agent.run_stream("q"):
                chunks.append(c)
        assert chunks == ["**"]
        # "**" is markdown scaffolding with zero information: nothing is
        # persisted, so the next session load shows no "** [interrupted]".
        assert agent.messages == [{"role": "user", "content": "q"}]

    @pytest.mark.asyncio
    async def test_real_partial_is_kept_with_interrupted_marker(
        self,
        isolated_env,
    ):
        agent, _mm = _make_agent([], [], [])

        async def _partial(messages, *args, **kwargs):
            yield LLMChunk(delta_content="**Hello")
            raise KeyboardInterrupt

        agent.provider.chat_stream = _partial
        with pytest.raises(KeyboardInterrupt):
            async for _ in agent.run_stream("q"):
                pass
        assert agent.messages[-1] == {
            "role": "assistant",
            "content": "**Hello [interrupted]",
        }
