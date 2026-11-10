# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.platforms.local.history — summary extraction."""

import json
import os
from pathlib import Path

import pytest

from dashscope_agenticx.platforms.local import history
from dashscope_agenticx.platforms.local.history import extract_summary


class TestExtractSummary:
    def test_basic_user_message(self):
        messages = [
            {"role": "user", "content": "帮我写一个 Python 脚本"},
            {"role": "assistant", "content": "好的"},
        ]
        assert extract_summary(messages) == "帮我写一个 Python 脚本"

    def test_strips_task_number_prefix(self):
        messages = [
            {"role": "user", "content": "任务1: 修复登录接口 bug"},
            {"role": "assistant", "content": "OK"},
        ]
        assert extract_summary(messages) == "修复登录接口 bug"

    def test_strips_chinese_number_prefix(self):
        messages = [
            {"role": "user", "content": "任务三：优化数据库查询"},
            {"role": "assistant", "content": "OK"},
        ]
        assert extract_summary(messages) == "优化数据库查询"

    def test_strips_task_with_space(self):
        messages = [
            {"role": "user", "content": "任务 2 添加单元测试"},
            {"role": "assistant", "content": "OK"},
        ]
        assert extract_summary(messages) == "添加单元测试"

    def test_strips_english_task_prefix(self):
        messages = [
            {"role": "user", "content": "Task 1: refactor config loader"},
            {"role": "assistant", "content": "OK"},
        ]
        assert extract_summary(messages) == "refactor config loader"

    def test_strips_numbered_list_prefix(self):
        messages = [
            {"role": "user", "content": "1. 升级 pyproject.toml 版本号"},
            {"role": "assistant", "content": "OK"},
        ]
        assert extract_summary(messages) == "升级 pyproject.toml 版本号"

    def test_skips_followup_only_message(self):
        messages = [
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": "把刚才的函数改成异步"},
        ]
        assert extract_summary(messages) == "把刚才的函数改成异步"

    def test_skips_bare_ok(self):
        messages = [
            {"role": "user", "content": "ok"},
            {"role": "assistant", "content": "OK"},
            {"role": "user", "content": "检查下 git status"},
        ]
        assert extract_summary(messages) == "检查下 git status"

    def test_appends_tools_used(self):
        messages = [
            {"role": "user", "content": "任务2: 查下最近提交"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"function": {"name": "run_command"}},
                    {"function": {"name": "read_file"}},
                ],
            },
        ]
        summary = extract_summary(messages)
        assert summary.startswith("查下最近提交")
        assert "run_command" in summary
        assert "read_file" in summary

    def test_empty_messages(self):
        assert extract_summary([]) == ""

    def test_only_noise_messages(self):
        messages = [
            {"role": "user", "content": "继续"},
            {"role": "assistant", "content": "OK"},
        ]
        assert extract_summary(messages) == ""

    def test_multimodal_text_content(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "任务1: 分析这张图片"},
                    {
                        "type": "image_url",
                        "image_url": "http://example.com/x.png",
                    },
                ],
            },
        ]
        assert extract_summary(messages) == "分析这张图片"


# ---------------------------------------------------------------------------
# C3: history _save must be atomic — temp file in the same directory, then
# os.replace; a crash mid-write must not truncate the existing history.
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Redirect the history module's WORKSPACE_DIR to an isolated tmp dir."""
    fake_workspace = tmp_path / "ws"
    fake_workspace.mkdir()
    monkeypatch.setattr(history, "WORKSPACE_DIR", fake_workspace)
    return fake_workspace


class TestHistoryAtomicSave:
    def test_save_uses_tmp_then_replace(self, workspace, monkeypatch):
        calls = []
        real_replace = os.replace

        def spy_replace(src, dst):
            calls.append((src, dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)

        history._save(
            [{"id": "x", "summary": "测试", "created_at": "", "turns": 1}],
        )

        history_file = workspace / "memory" / "history.json"
        assert len(calls) == 1
        src, dst = calls[0]
        assert Path(dst) == history_file
        assert Path(src).parent == history_file.parent
        assert str(src).endswith(".tmp")
        assert (
            json.loads(history_file.read_text(encoding="utf-8"))[0]["id"]
            == "x"
        )
        assert not (history_file.parent / "history.json.tmp").exists()

    def test_crash_during_replace_preserves_existing_file(
        self,
        workspace,
        monkeypatch,
    ):
        history_file = workspace / "memory" / "history.json"
        history_file.parent.mkdir(parents=True)
        history_file.write_text('[{"id": "old"}]', encoding="utf-8")

        def boom(src, dst):
            raise OSError("模拟崩溃")

        monkeypatch.setattr(os, "replace", boom)

        with pytest.raises(OSError):
            history._save([{"id": "new"}])

        assert history_file.read_text(encoding="utf-8") == '[{"id": "old"}]'
        assert not (history_file.parent / "history.json.tmp").exists()
