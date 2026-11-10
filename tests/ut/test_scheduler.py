# -*- coding: utf-8 -*-
"""Tests for scheduler.py — parse_interval, parse_at_time, next_cron_fire,
SkillInvocation, ScheduleSpec, CronJob serialization."""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from dashscope_agenticx.scheduler import (
    CronJob,
    ScheduleSpec,
    SkillInvocation,
    _expand_dow,
    _parse_cron_field,
    next_cron_fire,
    parse_at_time,
    parse_interval,
)

# ── SkillInvocation ───────────────────────────────────────────────────────


def test_skill_invocation_roundtrip():
    si = SkillInvocation(name="weather", args=["Beijing"])
    d = si.to_dict()
    restored = SkillInvocation.from_dict(d)
    assert restored.name == "weather"
    assert restored.args == ["Beijing"]


def test_skill_invocation_no_args_default():
    si = SkillInvocation.from_dict({"name": "foo"})
    assert si.args == []


# ── ScheduleSpec ──────────────────────────────────────────────────────────


def test_schedule_spec_roundtrip_interval():
    spec = ScheduleSpec(
        kind="interval",
        raw="every 5m",
        interval_seconds=300.0,
    )
    restored = ScheduleSpec.from_dict(spec.to_dict())
    assert restored.kind == "interval"
    assert restored.interval_seconds == 300.0


def test_schedule_spec_roundtrip_cron():
    spec = ScheduleSpec(
        kind="cron",
        raw="cron 0 9 * * 1-5",
        cron_fields=["0", "9", "*", "*", "1-5"],
    )
    restored = ScheduleSpec.from_dict(spec.to_dict())
    assert restored.cron_fields == ["0", "9", "*", "*", "1-5"]


# ── CronJob ───────────────────────────────────────────────────────────────


def test_cron_job_roundtrip():
    job = CronJob(
        id="test-123",
        schedule=ScheduleSpec(
            kind="interval",
            raw="every 10m",
            interval_seconds=600.0,
        ),
        skills=[SkillInvocation(name="news", args=[])],
        enabled=True,
        run_count=3,
    )
    d = job.to_dict()
    restored = CronJob.from_dict(d)
    assert restored.id == "test-123"
    assert restored.run_count == 3
    assert restored.skills[0].name == "news"


# ── parse_interval ────────────────────────────────────────────────────────


def test_parse_interval_seconds():
    assert parse_interval("30s") == 30.0


def test_parse_interval_minutes():
    assert parse_interval("5m") == 300.0


def test_parse_interval_hours():
    assert parse_interval("2h") == 7200.0


def test_parse_interval_with_spaces():
    assert parse_interval("10 m") == 600.0


def test_parse_interval_uppercase():
    assert parse_interval("5M") == 300.0


def test_parse_interval_invalid():
    with pytest.raises(ValueError, match="cannot parse interval"):
        parse_interval("5d")


def test_parse_interval_zero():
    assert parse_interval("0s") == 0.0


# ── parse_at_time ─────────────────────────────────────────────────────────


def test_parse_at_time_iso():
    dt = parse_at_time("2026-12-31T09:00")
    assert dt.year == 2026
    assert dt.month == 12
    assert dt.hour == 9


def test_parse_at_time_hhmm_in_future():
    future = (datetime.now() + timedelta(hours=2)).strftime("%H:%M")
    dt = parse_at_time(future)
    assert dt > datetime.now()


def test_parse_at_time_hhmm_past_rolls_to_tomorrow():
    past = "00:01"
    dt = parse_at_time(past)
    assert dt > datetime.now()


def test_parse_at_time_invalid():
    with pytest.raises(ValueError):
        parse_at_time("not-a-time")


# ── _parse_cron_field ─────────────────────────────────────────────────────


def test_parse_cron_field_star():
    assert _parse_cron_field("*", 0, 5) == {0, 1, 2, 3, 4, 5}


def test_parse_cron_field_single():
    assert _parse_cron_field("3", 0, 59) == {3}


def test_parse_cron_field_range():
    assert _parse_cron_field("1-3", 0, 59) == {1, 2, 3}


def test_parse_cron_field_step():
    result = _parse_cron_field("*/15", 0, 59)
    assert 0 in result and 15 in result and 30 in result and 45 in result


def test_parse_cron_field_list():
    assert _parse_cron_field("1,3,5", 0, 59) == {1, 3, 5}


def test_parse_cron_field_zero_step_raises():
    with pytest.raises(ValueError, match="cron step must be > 0"):
        _parse_cron_field("*/0", 0, 59)


# ── _expand_dow ───────────────────────────────────────────────────────────


def test_expand_dow_sunday_zero():
    dows = _expand_dow("0")
    assert 6 in dows  # Python weekday 6 = Sunday


def test_expand_dow_sunday_seven():
    dows = _expand_dow("7")
    assert 6 in dows


def test_expand_dow_monday():
    dows = _expand_dow("1")
    assert 0 in dows  # Python weekday 0 = Monday


def test_expand_dow_range_weekdays():
    dows = _expand_dow("1-5")
    assert dows == {0, 1, 2, 3, 4}  # Mon-Fri


# ── next_cron_fire ────────────────────────────────────────────────────────


def test_next_cron_fire_every_minute():
    base = datetime(2026, 7, 15, 10, 0, 0)
    fields = ["*", "*", "*", "*", "*"]
    result = next_cron_fire(fields, after=base)
    assert result == datetime(2026, 7, 15, 10, 1, 0)


def test_next_cron_fire_specific_time():
    base = datetime(2026, 7, 15, 8, 0, 0)
    fields = ["30", "9", "*", "*", "*"]
    result = next_cron_fire(fields, after=base)
    assert result == datetime(2026, 7, 15, 9, 30, 0)


def test_next_cron_fire_skips_past():
    base = datetime(2026, 7, 15, 10, 30, 0)
    fields = ["30", "9", "*", "*", "*"]  # 09:30 already passed
    result = next_cron_fire(fields, after=base)
    assert result is not None
    assert result > base
    assert result.hour == 9 and result.minute == 30
    assert result.day == 16  # next day


def test_next_cron_fire_weekday_filter():
    # Find next Monday (weekday=0 in Python)
    base = datetime(2026, 7, 15, 0, 0, 0)  # Wednesday
    fields = ["0", "9", "*", "*", "1"]  # Mon 09:00
    result = next_cron_fire(fields, after=base)
    assert result is not None
    assert result.weekday() == 0  # Monday
    assert result.hour == 9


# ── Scheduler._execute_job privacy_mode ──────────────────────────────────

from types import SimpleNamespace
from unittest.mock import patch

from dashscope_agenticx.scheduler import Scheduler


def _make_privacy_job():
    """Build a minimal CronJob suitable for _execute_job tests."""
    return CronJob(
        id="priv-test",
        schedule=ScheduleSpec(
            kind="interval",
            raw="every 5m",
            interval_seconds=300.0,
        ),
        skills=[SkillInvocation(name="weather", args=["Beijing"])],
        enabled=True,
    )


class TestExecuteJobPrivacyMode:
    async def test_skips_when_privacy_mode_true(self):
        """When config.privacy_mode is True, _execute_job should skip
        execution entirely — no skills invoked, no prompts queued."""
        config = SimpleNamespace(privacy_mode=True)
        agent = SimpleNamespace()
        scheduler = Scheduler(config, agent)

        job = _make_privacy_job()
        scheduler.jobs[job.id] = job

        with patch.object(scheduler, "_persist"):
            await scheduler._execute_job(job)

        # Job was skipped: last_result mentions privacy mode
        assert job.last_result is not None
        assert "privacy mode" in job.last_result
        # run_count was incremented (the skip is still "processed")
        assert job.run_count == 1
        # No prompts were queued (skill not executed)
        assert scheduler.get_pending_prompts() == []
        # last_run was set
        assert job.last_run is not None

    async def test_does_not_skip_when_privacy_mode_false(self):
        """When privacy_mode is False, _execute_job should proceed past the
        privacy check and attempt to execute skills."""
        config = SimpleNamespace(privacy_mode=False)
        agent = SimpleNamespace()
        scheduler = Scheduler(config, agent)

        job = _make_privacy_job()
        scheduler.jobs[job.id] = job

        # Mock BUILTIN_SKILLS as empty so the skill loop just records
        # "未知技能" without importing real skills.
        with (
            patch.object(scheduler, "_persist"),
            patch("dashscope_agenticx.skills.base.BUILTIN_SKILLS", {}),
        ):
            await scheduler._execute_job(job)

        # Job was executed (not skipped): last_result should NOT mention privacy
        assert job.last_result is not None
        assert "privacy mode" not in job.last_result
        # The skill name appears in the result (unknown skill message)
        assert "weather" in job.last_result
        assert job.run_count == 1

    async def test_defaults_to_not_skipping_without_attribute(self):
        """A config object without a privacy_mode attribute should default
        to not-skipping (getattr returns False)."""
        config = SimpleNamespace()  # no privacy_mode attribute
        agent = SimpleNamespace()
        scheduler = Scheduler(config, agent)

        job = _make_privacy_job()
        scheduler.jobs[job.id] = job

        with (
            patch.object(scheduler, "_persist"),
            patch("dashscope_agenticx.skills.base.BUILTIN_SKILLS", {}),
        ):
            await scheduler._execute_job(job)

        # Job was executed, not skipped
        assert job.last_result is not None
        assert "privacy mode" not in job.last_result
        assert "weather" in job.last_result

    async def test_skip_does_not_invoke_skills(self):
        """Verify that no skill import or rendering happens when privacy_mode
        is on — the method returns before reaching the skill execution loop."""
        config = SimpleNamespace(privacy_mode=True)
        agent = SimpleNamespace()
        scheduler = Scheduler(config, agent)

        job = _make_privacy_job()
        scheduler.jobs[job.id] = job

        with (
            patch.object(scheduler, "_persist"),
            patch(
                "dashscope_agenticx.skills.base.BUILTIN_SKILLS",
                {},
            ) as mock_builtin,
            patch(
                "dashscope_agenticx.skills.base.render_skill",
            ) as mock_render,
        ):
            await scheduler._execute_job(job)

        # BUILTIN_SKILLS was never accessed for skill lookup because we
        # returned early.  (The mock's __getitem__ / .get would only be
        # called if execution proceeded.)
        assert job.last_result is not None
        assert "privacy mode" in job.last_result
        mock_render.assert_not_called()


# ── M2a: tz-aware `at` schedules ──────────────────────────────────────────

import asyncio
import time
from datetime import timezone


def _make_at_job(job_id: str, target: datetime) -> CronJob:
    return CronJob(
        id=job_id,
        schedule=ScheduleSpec(
            kind="at",
            raw=f"at {target.isoformat()}",
            at_datetime=target.isoformat(),
        ),
        skills=[SkillInvocation(name="s")],
        enabled=True,
    )


class TestTzAwareAtSchedule:
    def test_seconds_until_next_fire_tz_aware_future(self):
        """A tz-aware ISO target must not raise TypeError against naive now."""
        scheduler = Scheduler(SimpleNamespace(), SimpleNamespace())
        target = datetime.now(timezone.utc) + timedelta(hours=1)
        job = _make_at_job("tz-future", target)
        delay = scheduler._seconds_until_next_fire(job)
        assert 3500 < delay <= 3600

    def test_seconds_until_next_fire_tz_aware_past_is_zero(self):
        scheduler = Scheduler(SimpleNamespace(), SimpleNamespace())
        target = datetime.now(timezone.utc) - timedelta(hours=1)
        job = _make_at_job("tz-past", target)
        assert scheduler._seconds_until_next_fire(job) == 0.0

    def test_seconds_until_next_fire_naive_still_works(self):
        scheduler = Scheduler(SimpleNamespace(), SimpleNamespace())
        target = datetime.now() + timedelta(hours=2)
        job = _make_at_job("naive", target)
        delay = scheduler._seconds_until_next_fire(job)
        assert 7100 < delay <= 7200

    async def test_at_job_tz_aware_fires_without_type_error(self):
        """End-to-end: a tz-aware one-shot job schedules, fires, disables."""
        scheduler = Scheduler(SimpleNamespace(), SimpleNamespace())
        aware = datetime.now(timezone.utc) + timedelta(seconds=0.1)
        job = _make_at_job("tz-fire", aware)
        scheduler.jobs[job.id] = job
        executed = []

        async def _fake_execute(j):
            executed.append(j.id)

        with (
            patch.object(scheduler, "_persist"),
            patch.object(scheduler, "_execute_job", side_effect=_fake_execute),
        ):
            await asyncio.wait_for(scheduler._job_loop(job.id), timeout=5)

        assert executed == ["tz-fire"]
        assert job.enabled is False


# ── M2b: next_cron_fire efficiency ────────────────────────────────────────


class TestNextCronFireEfficient:
    def test_sparse_yearly_cron_returns_correctly_and_fast(self):
        """`0 0 1 1 *` (yearly) must resolve without a per-minute year scan."""
        base = datetime(2026, 7, 15, 10, 0, 0)
        start = time.monotonic()
        result = next_cron_fire(["0", "0", "1", "1", "*"], after=base)
        elapsed = time.monotonic() - start
        assert result == datetime(2027, 1, 1, 0, 0)
        assert elapsed < 1.0

    def test_impossible_day_returns_none_bounded(self):
        """Feb 31 never matches — must give up quickly, not scan a year."""
        base = datetime(2026, 7, 15, 10, 0, 0)
        start = time.monotonic()
        result = next_cron_fire(["0", "0", "31", "2", "*"], after=base)
        assert result is None
        assert time.monotonic() - start < 1.0

    def test_step_expression_still_correct(self):
        base = datetime(2026, 7, 15, 10, 7, 0)
        result = next_cron_fire(["*/15", "*", "*", "*", "*"], after=base)
        assert result == datetime(2026, 7, 15, 10, 15, 0)

    def test_month_boundary_rollover(self):
        base = datetime(2026, 1, 31, 23, 59, 30)
        result = next_cron_fire(["0", "0", "*", "*", "*"], after=base)
        assert result == datetime(2026, 2, 1, 0, 0)

    def test_weekday_cron_still_correct(self):
        # 2026-07-15 is a Wednesday; next Mon-Fri 09:00 is Thu 2026-07-16.
        base = datetime(2026, 7, 15, 10, 0, 0)
        result = next_cron_fire(["0", "9", "*", "*", "1-5"], after=base)
        assert result == datetime(2026, 7, 16, 9, 0)
