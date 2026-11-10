# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.memory.plan — PlanStep, Plan, PlanTracker."""

from dashscope_agenticx.memory.plan import Plan, PlanStep, PlanTracker


class TestPlanStep:
    def test_defaults(self):
        step = PlanStep(description="do something")
        assert step.completed is False
        assert step.tool_calls == []

    def test_with_tool_calls(self):
        step = PlanStep(description="read file", tool_calls=["read_file"])
        assert step.tool_calls == ["read_file"]


class TestPlan:
    def test_progress_summary_empty(self):
        plan = Plan(goal="test")
        assert plan.progress_summary() == ""

    def test_progress_summary_with_steps(self):
        plan = Plan(
            goal="build app",
            steps=[
                PlanStep(description="step 1"),
                PlanStep(description="step 2", completed=True),
            ],
        )
        summary = plan.progress_summary()
        assert "1/2" in summary
        assert "step 1" in summary
        assert "step 2" in summary
        assert "✓" in summary
        assert "→" in summary

    def test_mark_step_complete(self):
        plan = Plan(
            goal="g",
            steps=[PlanStep(description="s1"), PlanStep(description="s2")],
        )
        plan.mark_step_complete(0, "read_file")
        assert plan.steps[0].completed is True
        assert plan.steps[0].tool_calls == ["read_file"]
        assert plan.steps[1].completed is False

    def test_mark_step_out_of_range(self):
        plan = Plan(goal="g", steps=[PlanStep(description="s")])
        plan.mark_step_complete(5, "tool")
        plan.mark_step_complete(-1, "tool")
        assert plan.steps[0].completed is False


class TestPlanTracker:
    def test_create_plan(self):
        tracker = PlanTracker()
        msg = tracker.create_plan("build feature", ["step 1", "step 2"])
        assert "build feature" in msg
        assert tracker.current_plan is not None
        assert len(tracker.current_plan.steps) == 2

    def test_get_plan_section_empty(self):
        tracker = PlanTracker()
        assert tracker.get_plan_section() == ""

    def test_get_plan_section_with_plan(self):
        tracker = PlanTracker()
        tracker.create_plan("goal", ["s1"])
        section = tracker.get_plan_section()
        assert "0/1" in section
        assert "s1" in section

    def test_mark_step(self):
        tracker = PlanTracker()
        tracker.create_plan("goal", ["s1", "s2"])
        tracker.mark_step_complete(0, "read_file")
        assert tracker.current_plan.steps[0].completed is True

    def test_clear_plan(self):
        tracker = PlanTracker()
        tracker.create_plan("goal", ["s1"])
        tracker.clear_plan()
        assert tracker.current_plan is None

    def test_is_complete_no_plan(self):
        tracker = PlanTracker()
        assert tracker.is_complete() is False

    def test_is_complete_partial(self):
        tracker = PlanTracker()
        tracker.create_plan("g", ["s1", "s2"])
        tracker.mark_step_complete(0)
        assert tracker.is_complete() is False

    def test_is_complete_all_done(self):
        tracker = PlanTracker()
        tracker.create_plan("g", ["s1", "s2"])
        tracker.mark_step_complete(0)
        tracker.mark_step_complete(1)
        assert tracker.is_complete() is True


class TestPlanTTL:
    def test_fresh_plan_renders(self):
        now = [1000.0]
        tracker = PlanTracker(ttl_seconds=60, clock=lambda: now[0])
        tracker.create_plan("goal", ["s1"])
        assert "s1" in tracker.get_plan_section()

    def test_expired_plan_stops_rendering(self):
        now = [1000.0]
        tracker = PlanTracker(ttl_seconds=60, clock=lambda: now[0])
        tracker.create_plan("goal", ["s1"])
        now[0] += 61
        assert tracker.get_plan_section() == ""
        assert tracker.current_plan is None

    def test_completed_plan_auto_clears(self):
        tracker = PlanTracker()
        tracker.create_plan("goal", ["s1"])
        tracker.mark_step_complete(0)
        assert tracker.get_plan_section() == ""
        assert tracker.current_plan is None

    def test_mark_on_expired_plan_is_noop(self):
        now = [1000.0]
        tracker = PlanTracker(ttl_seconds=60, clock=lambda: now[0])
        tracker.create_plan("goal", ["s1"])
        now[0] += 61
        tracker.mark_step_complete(0)
        assert tracker.current_plan is None

    def test_new_plan_resets_clock(self):
        now = [1000.0]
        tracker = PlanTracker(ttl_seconds=60, clock=lambda: now[0])
        tracker.create_plan("old", ["s1"])
        now[0] += 30
        tracker.create_plan("new", ["s2"])
        now[0] += 45  # 75s after first plan, 45s after second
        section = tracker.get_plan_section()
        assert "new" not in section  # goal text not rendered
        assert "s2" in section
