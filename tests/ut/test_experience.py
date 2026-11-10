# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.memory.experience — ExperienceTracker."""

import json

from dashscope_agenticx.memory.experience import ExperienceTracker


class TestExperienceTracker:
    def test_record_and_search(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="deploy app to production",
            tools_used=["shell", "write_file"],
            outcome="success",
            lesson="always check env vars",
        )
        results = tracker.search_experiences("deploy production")
        assert len(results) == 1
        assert results[0]["task"] == "deploy app to production"
        assert results[0]["outcome"] == "success"

    def test_search_no_file(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        assert tracker.search_experiences("anything") == []

    def test_search_no_match(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="deploy app",
            tools_used=["shell"],
            outcome="success",
        )
        results = tracker.search_experiences("cooking recipe")
        assert results == []

    def test_search_scoring(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="deploy app to production",
            tools_used=["shell"],
            outcome="success",
        )
        tracker.record_experience(
            task_summary="deploy app to staging",
            tools_used=["shell"],
            outcome="success",
        )
        tracker.record_experience(
            task_summary="cook dinner",
            tools_used=[],
            outcome="failure",
        )
        results = tracker.search_experiences("deploy app")
        assert len(results) == 2
        assert "deploy" in results[0]["task"]

    def test_search_boosts_failure_lessons(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="deploy app to production",
            tools_used=["shell"],
            outcome="success",
        )
        tracker.record_experience(
            task_summary="deploy app rollback",
            tools_used=["shell"],
            outcome="failure",
            lesson="check credentials first",
        )
        results = tracker.search_experiences("deploy app")
        assert results[0]["outcome"] == "failure"
        assert results[0]["lesson"] == "check credentials first"

    def test_search_limit(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        for i in range(5):
            tracker.record_experience(
                task_summary=f"deploy app number {i}",
                tools_used=["shell"],
                outcome="success",
            )
        results = tracker.search_experiences("deploy app", limit=2)
        assert len(results) == 2

    def test_format_experiences_empty(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        assert tracker.format_experiences_for_prompt([]) == ""

    def test_format_experiences(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        experiences = [
            {
                "task": "deploy app",
                "outcome": "success",
                "lesson": "check env",
                "tools": ["shell", "write_file"],
            },
            {
                "task": "fix bug",
                "outcome": "failure",
                "lesson": "",
                "tools": [],
            },
        ]
        text = tracker.format_experiences_for_prompt(experiences)
        assert "deploy app" in text
        assert "✓" in text
        assert "✗" in text
        assert "check env" in text
        assert "shell" in text

    def test_short_words_skipped(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="deploy ok app",
            tools_used=["shell"],
            outcome="success",
        )
        results = tracker.search_experiences("ok")
        assert results == []

    def test_sanitization(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="key=sk-1234567890abcdef deploy",
            tools_used=[],
            outcome="success",
            lesson="secret=AKIAIOSFODNN7EXAMPLE",
        )
        with open(tracker.experience_file) as f:
            entry = json.loads(f.readline())
        assert "sk-1234567890abcdef" not in entry["task"]
        assert "AKIAIOSFODNN7EXAMPLE" not in entry["lesson"]


class TestChineseRetrieval:
    def test_chinese_query_matches(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="修复登录接口的空指针错误",
            tools_used=["read_file", "write_file"],
            outcome="success",
        )
        tracker.record_experience(
            task_summary="deploy app to production",
            tools_used=["shell"],
            outcome="success",
        )
        results = tracker.search_experiences("登录报错")
        assert len(results) == 1
        assert "登录" in results[0]["task"]

    def test_chinese_no_false_positive_from_single_chars(self, tmp_path):
        # "我" appears in the stored task but is only a single-char keyword;
        # with a longer keyword present it must not drive matches by itself.
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="我想吃苹果",
            tools_used=[],
            outcome="success",
        )
        results = tracker.search_experiences("部署应用")
        assert results == []

    def test_lesson_boosts_score(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="修复登录错误",
            tools_used=["write_file"],
            outcome="failure",
        )
        tracker.record_experience(
            task_summary="修复登录错误重试",
            tools_used=["write_file"],
            outcome="failure",
            lesson="先确认路径存在",
        )
        results = tracker.search_experiences("修复登录")
        assert results[0]["lesson"] == "先确认路径存在"


class TestTiesAndDedupe:
    def test_tie_favors_newest(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="deploy app v1",
            tools_used=["shell"],
            outcome="success",
        )
        tracker.record_experience(
            task_summary="deploy app v2",
            tools_used=["shell"],
            outcome="success",
        )
        results = tracker.search_experiences("deploy app")
        assert results[0]["task"] == "deploy app v2"

    def test_duplicate_task_lesson_keeps_newest(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        for _ in range(3):
            tracker.record_experience(
                task_summary="工具连续失败",
                tools_used=["write_file"],
                outcome="failure",
                lesson="连续 3 次失败，需要换策略",
            )
        results = tracker.search_experiences("工具连续失败", limit=3)
        assert len(results) == 1


class TestRotation:
    def test_oversize_file_rotates(self, tmp_path, monkeypatch):
        monkeypatch.setattr(ExperienceTracker, "MAX_FILE_BYTES", 400)
        monkeypatch.setattr(ExperienceTracker, "KEEP_LINES", 3)
        tracker = ExperienceTracker(tmp_path)
        for i in range(10):
            tracker.record_experience(
                task_summary=f"task number {i} with some padding text",
                tools_used=["shell"],
                outcome="success",
            )
        lines = [
            l
            for l in tracker.experience_file.read_text(
                encoding="utf-8",
            ).splitlines()
            if l.strip()
        ]
        assert len(lines) == 3
        assert "task number 9" in lines[-1]

    def test_small_file_not_rotated(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="tiny",
            tools_used=[],
            outcome="success",
        )
        assert len(tracker.experience_file.read_text().splitlines()) == 1


class TestMtimeCache:
    def test_repeated_search_uses_cache(self, tmp_path, monkeypatch):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="deploy app",
            tools_used=["shell"],
            outcome="success",
        )
        assert tracker.search_experiences("deploy")
        cache = tracker._cache
        assert cache is not None
        # Second search without file change returns the same object.
        assert tracker.search_experiences("deploy")
        assert tracker._cache is cache

    def test_new_record_invalidates_cache(self, tmp_path):
        tracker = ExperienceTracker(tmp_path)
        tracker.record_experience(
            task_summary="deploy app",
            tools_used=["shell"],
            outcome="success",
        )
        tracker.search_experiences("deploy")
        tracker.record_experience(
            task_summary="deploy staging",
            tools_used=["shell"],
            outcome="success",
        )
        results = tracker.search_experiences("deploy", limit=5)
        assert len(results) == 2
