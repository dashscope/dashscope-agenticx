# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.memory.reflection — trackers, thresholds, classifier."""

from dashscope_agenticx.memory.reflection import (
    FUTILITY_THRESHOLD,
    REPEAT_OBSERVATION_THRESHOLD,
    ReflectionTracker,
)


class TestReflectionTracker:
    def test_initial_state(self):
        tracker = ReflectionTracker(threshold=3)
        assert tracker.consecutive_failures == 0
        assert tracker.last_failed_tools == []
        assert tracker.needs_reflection() is False

    def test_record_failure(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("read_file")
        assert tracker.consecutive_failures == 1
        assert "read_file" in tracker.last_failed_tools
        assert tracker.needs_reflection() is False

    def test_record_success_resets(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("a")
        tracker.record_failure("b")
        tracker.record_success()
        assert tracker.consecutive_failures == 0
        assert tracker.last_failed_tools == []

    def test_needs_reflection_at_threshold(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("a")
        tracker.record_failure("b")
        tracker.record_failure("c")
        assert tracker.needs_reflection() is True

    def test_get_reflection_hint_below_threshold(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("a")
        assert tracker.get_reflection_hint() == ""

    def test_get_reflection_hint_at_threshold(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("read_file")
        tracker.record_failure("write_file")
        tracker.record_failure("read_file")
        hint = tracker.get_reflection_hint()
        assert "3" in hint
        assert "read_file" in hint
        assert "write_file" in hint
        assert "Reflection: repeated tool failures" in hint

    def test_get_failure_lesson_below_threshold(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("a")
        assert tracker.get_failure_lesson() == ""

    def test_get_failure_lesson_at_threshold(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("read_file")
        tracker.record_failure("read_file")
        tracker.record_failure("read_file")
        lesson = tracker.get_failure_lesson()
        assert "3" in lesson
        assert "read_file" in lesson

    def test_reset(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("a")
        tracker.record_failure("b")
        tracker.reset()
        assert tracker.consecutive_failures == 0
        assert tracker.last_failed_tools == []

    def test_custom_threshold(self):
        tracker = ReflectionTracker(threshold=1)
        tracker.record_failure("a")
        assert tracker.needs_reflection() is True


def _failed_thrice(**kwargs) -> ReflectionTracker:
    tracker = ReflectionTracker(threshold=3)
    for _ in range(3):
        tracker.record_failure("run_command", **kwargs)
    return tracker


class TestEvidenceAnchoring:
    """The hint must quote the tool's own output, not just count failures."""

    def test_evidence_quoted_verbatim(self):
        tracker = _failed_thrice(evidence="KeyError: 'api_key'")
        hint = tracker.get_reflection_hint()
        assert "KeyError: 'api_key'" in hint
        assert "Verbatim output" in hint

    def test_only_latest_evidence_kept(self):
        # Accumulating failure history is what AlphaCodium measured as "no
        # improvement" — the model gets conditioned on its own past mistakes.
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("run_command", evidence="first error")
        tracker.record_failure("run_command", evidence="second error")
        tracker.record_failure("run_command", evidence="third error")
        assert tracker.evidence == "third error"
        hint = tracker.get_reflection_hint()
        assert "third error" in hint
        assert "first error" not in hint
        assert "second error" not in hint

    def test_verdict_line_carries_signal_and_exit_code(self):
        tracker = _failed_thrice(
            evidence="boom",
            signal_kind="exit_code",
            exit_code=2,
        )
        hint = tracker.get_reflection_hint()
        assert "signal=exit_code" in hint
        assert "exit_code=2" in hint

    def test_long_evidence_keeps_the_tail(self):
        # Tracebacks and "FAILED tests/..." lines sit at the end.
        noise = "x" * 5000
        tracker = _failed_thrice(evidence=noise + "\nFAILED tests/test_a.py")
        hint = tracker.get_reflection_hint()
        assert "FAILED tests/test_a.py" in hint
        assert len(hint) < len(noise)

    def test_success_clears_evidence(self):
        tracker = _failed_thrice(evidence="stale error")
        tracker.record_success()
        assert tracker.evidence == ""
        assert tracker.evidence_tool == ""
        assert tracker.evidence_exit_code is None

    def test_reset_clears_evidence(self):
        tracker = _failed_thrice(evidence="stale error", exit_code=1)
        tracker.reset()
        assert tracker.evidence == ""
        assert tracker.evidence_exit_code is None

    def test_record_tool_execution_threads_evidence(self):
        tracker = ReflectionTracker(threshold=1)
        tracker.record_tool_execution(
            "run_command",
            success=False,
            evidence="permission denied",
            signal_kind="tool_reported",
        )
        assert tracker.evidence == "permission denied"
        assert tracker.evidence_signal == "tool_reported"
        assert "permission denied" in tracker.get_reflection_hint()


class TestNoEvidenceDegradesGracefully:
    """Callers with no structured outcome must not get a dangling prompt."""

    def test_hint_says_nothing_was_captured(self):
        hint = _failed_thrice().get_reflection_hint()
        assert "No output was captured" in hint

    def test_hint_does_not_point_at_absent_output(self):
        # "Work from that output" with no output quoted would send the model
        # reasoning about text it was never shown.
        hint = _failed_thrice().get_reflection_hint()
        assert "Work from that output" not in hint

    def test_verdict_line_names_the_tool(self):
        hint = _failed_thrice().get_reflection_hint()
        assert "Last failure: run_command" in hint

    def test_hint_never_restores_signal_free_introspection(self):
        # Valmeekam et al. measured an 84.45% false-positive rate when a
        # model was asked to grade its own answer, and Huang et al. oppose
        # unanchored introspection specifically.
        hint = _failed_thrice(evidence="boom").get_reflection_hint()
        assert "whether the previous approach is flawed" not in hint
        assert "is your previous answer correct" not in hint.lower()

    def test_hint_demands_the_fix_in_the_same_response(self):
        # Anchoring must not cost an extra model call.
        hint = _failed_thrice(evidence="boom").get_reflection_hint()
        assert "in this same response" in hint


class TestEvidenceFence:
    def test_fence_outlasts_backticks_in_output(self):
        # Catted markdown would otherwise close the fence early and swallow
        # the rest of the hint into the code block.
        tracker = _failed_thrice(evidence="see ``` this block ```")
        hint = tracker.get_reflection_hint()
        before, quoted, after = hint.split("````")
        assert "this block" in quoted
        assert "```\n" not in before
        assert "Work from that output" in after

    def test_plain_output_uses_a_three_backtick_fence(self):
        hint = _failed_thrice(evidence="plain error").get_reflection_hint()
        assert "```\nplain error\n```" in hint


class TestFailureLesson:
    def test_prefers_the_model_diagnosis(self):
        tracker = _failed_thrice(evidence="raw stderr")
        lesson = tracker.get_failure_lesson(diagnosis="the path is relative")
        assert lesson == "the path is relative"

    def test_blank_diagnosis_falls_back_to_evidence(self):
        tracker = _failed_thrice(evidence="raw stderr", exit_code=3)
        lesson = tracker.get_failure_lesson(diagnosis="   \n ")
        assert "raw stderr" in lesson
        assert "exit_code=3" in lesson
        assert "3 consecutive failures" in lesson

    def test_diagnosis_is_capped(self):
        tracker = _failed_thrice(evidence="boom")
        lesson = tracker.get_failure_lesson(diagnosis="y" * 5000)
        assert len(lesson) < 1000
        assert "omitted" in lesson

    def test_evidence_tail_is_bounded(self):
        tracker = _failed_thrice(evidence="z" * 5000)
        assert len(tracker.get_failure_lesson()) < 600

    def test_below_threshold_even_with_diagnosis(self):
        tracker = ReflectionTracker(threshold=3)
        tracker.record_failure("a", evidence="boom")
        assert tracker.get_failure_lesson(diagnosis="my diagnosis") == ""


class TestStagnationTracker:
    def test_readonly_streak_counts(self):
        from dashscope_agenticx.memory.reflection import StagnationTracker

        tracker = StagnationTracker(threshold=3)
        tracker.record(True)
        tracker.record(True)
        assert not tracker.needs_nudge()
        tracker.record(True)
        assert tracker.needs_nudge()

    def test_mutating_call_resets(self):
        from dashscope_agenticx.memory.reflection import StagnationTracker

        tracker = StagnationTracker(threshold=2)
        tracker.record(True)
        tracker.record(True)
        tracker.record(False)
        assert tracker.readonly_streak == 0
        assert not tracker.needs_nudge()

    def test_hint_below_threshold_empty(self):
        from dashscope_agenticx.memory.reflection import StagnationTracker

        tracker = StagnationTracker(threshold=5)
        tracker.record(True)
        assert tracker.get_stagnation_hint() == ""

    def test_hint_with_hard_cap_countdown(self):
        from dashscope_agenticx.memory.reflection import StagnationTracker

        tracker = StagnationTracker(threshold=2)
        tracker.record(True)
        tracker.record(True)
        hint = tracker.get_stagnation_hint(hard_cap=10)
        assert "Stagnation warning" in hint
        assert "Hard stop in 8" in hint

    def test_hint_no_countdown_when_cap_reached(self):
        from dashscope_agenticx.memory.reflection import StagnationTracker

        tracker = StagnationTracker(threshold=2)
        tracker.record(True)
        tracker.record(True)
        hint = tracker.get_stagnation_hint(hard_cap=2)
        assert "Hard stop" not in hint


def _repeating_tracker():
    """Tracker whose read-only threshold is out of the way of the repeat tests."""
    from dashscope_agenticx.memory.reflection import StagnationTracker

    return StagnationTracker(threshold=8)


def _fail(tracker, result="boom", tool="run_command", args=None):
    """One mutating, failing call. Identity is fixed unless a test varies it."""
    tracker.record(
        False,
        tool_name=tool,
        arguments={"command": "pytest"} if args is None else args,
        ok=False,
        result=result,
    )


class TestStagnationRepeats:
    """A8 — same call, same arguments, same output, nothing changed between."""

    def test_identical_failures_accumulate(self):
        tracker = _repeating_tracker()
        _fail(tracker)
        _fail(tracker)
        assert tracker.repeat_streak == 2
        assert tracker.repeat_name == "run_command"
        assert tracker.repeat_ok is False

    def test_different_arguments_are_different_calls(self):
        tracker = _repeating_tracker()
        _fail(tracker, args={"command": "pytest a"})
        _fail(tracker, args={"command": "pytest b"})
        assert tracker.repeat_streak == 1

    def test_different_output_is_not_a_repeat(self):
        # A changed result means the call is still telling us something.
        tracker = _repeating_tracker()
        _fail(tracker, result="boom 1")
        _fail(tracker, result="boom 2")
        assert tracker.repeat_streak == 1

    def test_argument_order_does_not_change_identity(self):
        tracker = _repeating_tracker()
        _fail(tracker, args={"a": 1, "b": 2})
        _fail(tracker, args={"b": 2, "a": 1})
        assert tracker.repeat_streak == 2

    def test_unserialisable_arguments_do_not_raise(self):
        tracker = _repeating_tracker()
        blob = object()
        _fail(tracker, args={"obj": blob})
        _fail(tracker, args={"obj": blob})
        assert tracker.repeat_streak == 2

    def test_interleaved_read_does_not_break_the_streak(self):
        # The case a consecutive-only counter misses: check, look something
        # up, check again — same futility, no consecutive repeats.
        tracker = _repeating_tracker()
        _fail(tracker)
        tracker.record(
            True,
            tool_name="read_file",
            arguments={"path": "a.py"},
            ok=True,
            result="source",
        )
        _fail(tracker)
        assert tracker.repeat_streak == 2

    def test_successful_mutation_resets_the_streak(self):
        # Re-running the suite after an edit is legitimate work, not a loop.
        tracker = _repeating_tracker()
        _fail(tracker)
        tracker.record(
            False,
            tool_name="edit_file",
            arguments={"path": "a.py"},
            ok=True,
            result="written",
        )
        _fail(tracker)
        assert tracker.repeat_streak == 1

    def test_failed_mutation_is_not_a_state_change(self):
        tracker = _repeating_tracker()
        _fail(tracker)
        _fail(
            tracker,
            tool="edit_file",
            args={"path": "a.py"},
            result="denied",
        )
        _fail(tracker)
        assert tracker.repeat_streak == 2

    def test_repeated_success_needs_the_observation_threshold(self):
        tracker = _repeating_tracker()
        grep = {
            "tool_name": "search_files",
            "arguments": {"pattern": "TODO"},
            "ok": True,
            "result": "no matches",
        }
        for _ in range(REPEAT_OBSERVATION_THRESHOLD - 1):
            tracker.record(True, **grep)
        assert tracker.needs_replan() is False
        tracker.record(True, **grep)
        assert tracker.needs_replan() is True

    def test_repeated_success_is_never_refused(self):
        # Re-reading a file after thinking about it is legitimate, so a
        # repeated success escalates the prompt but still runs.
        tracker = _repeating_tracker()
        for _ in range(5):
            tracker.record(
                True,
                tool_name="read_file",
                arguments={"path": "a.py"},
                ok=True,
                result="source",
            )
        assert tracker.refuse_if_futile("read_file", {"path": "a.py"}) == ""

    def test_single_failure_is_not_refused(self):
        tracker = _repeating_tracker()
        _fail(tracker)
        assert (
            tracker.refuse_if_futile("run_command", {"command": "pytest"})
            == ""
        )

    def test_identical_failure_is_refused(self):
        tracker = _repeating_tracker()
        _fail(tracker, result="ModuleNotFoundError: yaml")
        _fail(tracker, result="ModuleNotFoundError: yaml")
        refusal = tracker.refuse_if_futile(
            "run_command",
            {"command": "pytest"},
        )
        assert "Not executed" in refusal
        assert "ModuleNotFoundError: yaml" in refusal

    def test_refusal_quotes_backticked_output_safely(self):
        tracker = _repeating_tracker()
        for _ in range(FUTILITY_THRESHOLD):
            _fail(tracker, result="```not a fence```")
        refusal = tracker.refuse_if_futile(
            "run_command",
            {"command": "pytest"},
        )
        before, quoted, after = refusal.split("````")
        assert "not a fence" in quoted
        assert "Make a DIFFERENT call" in after
        assert "````" not in before

    def test_refusal_escalates_to_a_replan_demand(self):
        # Otherwise the same sentence repeats while the turn budget burns.
        tracker = _repeating_tracker()
        for _ in range(FUTILITY_THRESHOLD):
            _fail(tracker)
        args = {"command": "pytest"}
        first = tracker.refuse_if_futile("run_command", args)
        second = tracker.refuse_if_futile("run_command", args)
        assert "Not executed" in first
        assert "Stopped: this call is being repeated" in second
        assert "revised plan" in second

    def test_state_change_clears_the_refusal(self):
        tracker = _repeating_tracker()
        for _ in range(FUTILITY_THRESHOLD):
            _fail(tracker)
        args = {"command": "pytest"}
        assert tracker.refuse_if_futile("run_command", args)
        tracker.record(
            False,
            tool_name="edit_file",
            arguments={"path": "a.py"},
            ok=True,
            result="written",
        )
        assert tracker.refuse_if_futile("run_command", args) == ""

    def test_replan_demand_replaces_the_readonly_nudge(self):
        tracker = _repeating_tracker()
        grep = {
            "tool_name": "search_files",
            "arguments": {"pattern": "TODO"},
            "ok": True,
            "result": "no matches",
        }
        for _ in range(8):
            tracker.record(True, **grep)
        hint = tracker.get_stagnation_hint(hard_cap=40)
        assert "Stopped: this call is being repeated" in hint
        assert "Stagnation warning" not in hint
        # Both detectors agree the turn is over: the countdown still shows.
        assert "Hard stop in 32 more read-only calls" in hint

    def test_preview_is_bounded(self):
        tracker = _repeating_tracker()
        _fail(tracker, result="x" * 5000)
        assert len(tracker.repeat_preview) < 1000

    def test_reset_clears_repeat_state(self):
        tracker = _repeating_tracker()
        for _ in range(FUTILITY_THRESHOLD):
            _fail(tracker)
        args = {"command": "pytest"}
        assert tracker.refuse_if_futile("run_command", args)
        tracker.reset()
        assert tracker.repeat_streak == 0
        assert tracker.repeat_name == ""
        assert tracker.refuse_if_futile("run_command", args) == ""
        # The state-change marker cleared too: one fresh failure is not yet
        # futile, exactly as at the start of a turn.
        _fail(tracker)
        assert tracker.refuse_if_futile("run_command", args) == ""


class TestReadonlyClassifier:
    def test_builtin_readonly_tools(self):
        from dashscope_agenticx.memory.reflection import is_readonly_tool_call

        for tool in (
            "read_file",
            "search_files",
            "list_directory",
            "memory_search",
            "web_search",
        ):
            assert is_readonly_tool_call(tool, {}) is True

    def test_mutating_tools(self):
        from dashscope_agenticx.memory.reflection import is_readonly_tool_call

        for tool in ("write_file", "edit_file", "delete_file"):
            assert is_readonly_tool_call(tool, {}) is False
        assert is_readonly_tool_call("mcp_fetch", {}) is False

    def test_readonly_shell_commands(self):
        from dashscope_agenticx.memory.reflection import is_readonly_tool_call

        cmds = [
            'grep -rn "foo" src/ | head -20',
            "cat a.txt; echo ---; ls dist",
            "docker ps; docker images --format x",
            "uname -sm; sw_vers 2>/dev/null | head -2",
            "colima status 2>&1 | head -1",
            "docker logs abc | tail -5",
            "jq .results runs/x/results.json",
        ]
        for c in cmds:
            assert (
                is_readonly_tool_call(
                    "run_command",
                    {"command": c},
                )
                is True
            ), c

    def test_mutating_shell_commands(self):
        from dashscope_agenticx.memory.reflection import is_readonly_tool_call

        cmds = [
            "echo hi > /tmp/x",
            "pip install pytest==8.4.1",
            "docker run --rm img ls",
            "colima start",
            "uv run tb run -t foo",
            "rm -rf /tmp/x",
            "setsid env FOO=1 uv run tb > /tmp/log 2>&1 &",
            "python3 -c 'print(1)'",
        ]
        for c in cmds:
            assert (
                is_readonly_tool_call(
                    "run_command",
                    {"command": c},
                )
                is False
            ), c

    def test_missing_command(self):
        from dashscope_agenticx.memory.reflection import is_readonly_tool_call

        assert is_readonly_tool_call("run_command", {}) is False
        assert is_readonly_tool_call("run_command", None) is False
