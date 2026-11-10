# -*- coding: utf-8 -*-
from __future__ import annotations

import pytest

from dashscope_agenticx.compression import (
    estimate_tokens,
    preserve_recent_messages,
    should_compress,
)


class TestEstimateTokens:
    def test_cjk_counts_roughly_one_token_per_char(self):
        text = "你好世界今天天气不错"  # 9 CJK chars
        assert estimate_tokens(text) >= 9

    def test_cjk_much_higher_than_old_formula(self):
        text = "你好" * 50  # 100 CJK chars — old chars/3.5 gave ~28
        old_estimate = int(len(text) / 3.5)
        assert estimate_tokens(text) > old_estimate * 2

    def test_ascii_roughly_four_chars_per_token(self):
        assert estimate_tokens("a" * 400) == 100

    def test_mixed_text(self):
        # 10 CJK + 40 ASCII → 10 + 10 = 20
        assert estimate_tokens("你" * 10 + "a" * 40) == 20

    def test_empty(self):
        assert estimate_tokens("") == 0


class TestShouldCompress:
    def test_few_huge_messages_trigger_compression(self):
        """The dangerous overflow case: very few messages, but huge ones —
        must compress on token budget alone, without any count threshold."""
        messages = [
            {
                "role": "user" if i % 2 == 0 else "assistant",
                "content": "x" * 4000,
            }
            for i in range(5)
        ]
        should, reason = should_compress(messages, context_window=1000)
        assert should
        assert "token" in reason

    def test_single_huge_tool_output_triggers(self):
        """One giant tool result is the classic overflow case; estimation
        must see its real size (the compress dump truncates tool text)."""
        messages = [
            {"role": "user", "content": "read the file"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {
                        "id": "c1",
                        "function": {"name": "read_file", "arguments": "{}"},
                    },
                ],
            },
            {"role": "tool", "tool_call_id": "c1", "content": "x" * 20000},
            {"role": "user", "content": "now what?"},
        ]
        should, reason = should_compress(messages, context_window=2000)
        assert should
        assert "token" in reason

    def test_tiny_chat_never_compresses(self):
        messages = [
            {"role": "user", "content": "x" * 100000} for _ in range(3)
        ]
        should, reason = should_compress(messages, context_window=1000)
        assert not should
        assert reason == ""

    def test_normal_chat_under_budget(self):
        messages = [{"role": "user", "content": "hi"} for _ in range(10)]
        should, _ = should_compress(messages, context_window=128_000)
        assert not should


def _tool_exchange() -> list[dict]:
    return [
        {"role": "user", "content": "task"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "function": {"name": "run", "arguments": "{}"},
                },
                {
                    "id": "call_2",
                    "function": {"name": "run", "arguments": "{}"},
                },
            ],
        },
        {"role": "tool", "tool_call_id": "call_1", "content": "r1"},
        {"role": "tool", "tool_call_id": "call_2", "content": "r2"},
        {"role": "user", "content": "next"},
    ]


class TestPreserveRecentMessages:
    def test_split_never_starts_with_orphan_tool(self):
        """Naive keep=2 split would start the tail on the second tool
        result — an orphan whose assistant tool_calls message was cut."""
        msgs = _tool_exchange()
        _older, recent = preserve_recent_messages(msgs, keep=2)
        assert recent[0]["role"] == "user"
        assert not any(m.get("role") == "tool" for m in recent[:1])

    def test_whole_tool_results_block_is_skipped(self):
        msgs = _tool_exchange()
        older, recent = preserve_recent_messages(msgs, keep=3)
        assert recent == [msgs[-1]]
        assert older == msgs[:-1]

    def test_assistant_with_answered_tool_calls_is_safe_start(self):
        msgs = _tool_exchange()
        _older, recent = preserve_recent_messages(msgs, keep=4)
        # Both tool results are inside the kept range → boundary unchanged.
        assert len(recent) == 4
        assert recent[0]["role"] == "assistant"

    def test_pending_tool_calls_boundary_advances(self):
        """Assistant with tool_calls unanswered in the kept range must not
        start the tail."""
        msgs = [
            {"role": "user", "content": "a"},
            {"role": "assistant", "content": "x"},
            {"role": "user", "content": "b"},
            {
                "role": "assistant",
                "content": "",
                "tool_calls": [
                    {"id": "c9", "function": {"name": "t", "arguments": "{}"}},
                ],
            },
        ]
        older, recent = preserve_recent_messages(msgs, keep=1)
        assert recent == []
        assert len(older) == len(msgs)

    def test_short_history_untouched(self):
        msgs = [{"role": "user", "content": "hi"}]
        older, recent = preserve_recent_messages(msgs, keep=4)
        assert older == []
        assert recent == msgs


class _FakeChat:
    def __init__(self, summary: str = "摘要内容"):
        self.summary = summary
        self.calls = 0

    async def __call__(self, messages, tools=None):
        self.calls += 1

        class _Resp:
            content = self.summary

        return _Resp()


def _long_chat(n: int = 8, size: int = 400) -> list[dict]:
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": "x" * size}
        for i in range(n)
    ]


class TestCompressMessages:
    @pytest.mark.asyncio
    async def test_extra_tokens_push_over_threshold(self):
        """System prompt + tools schema overhead must count toward the
        budget: messages alone fit, messages + overhead do not."""
        from dashscope_agenticx.compression import compress_messages

        messages = _long_chat()
        chat = _FakeChat()
        without = await compress_messages(messages, chat, context_window=10000)
        assert without is None

        result = await compress_messages(
            messages,
            chat,
            context_window=10000,
            extra_tokens=9000,
        )
        assert result is not None
        assert chat.calls == 1

    @pytest.mark.asyncio
    async def test_summary_framing_no_fabricated_ack(self):
        from dashscope_agenticx.compression import compress_messages

        result = await compress_messages(
            _long_chat(),
            _FakeChat(),
            context_window=1000,
        )
        assert result is not None
        assert result[0]["role"] == "user"
        assert (
            "Summary of earlier conversation (auto-compressed"
            in result[0]["content"]
        )
        assert "摘要内容" in result[0]["content"]
        # The old fabricated assistant acknowledgement is gone.
        assert not any(
            m["role"] == "assistant" and "已了解" in m.get("content", "")
            for m in result
        )

    @pytest.mark.asyncio
    async def test_summarizer_failure_falls_back_to_omission_marker(self):
        """Overflow protection must not depend on the summarizer succeeding:
        a failing chat still yields a shortened list, with an omission
        marker in place of the summary."""
        from dashscope_agenticx.compression import compress_messages

        async def failing_chat(messages, tools=None):
            raise RuntimeError("boom")

        messages = _long_chat()
        result = await compress_messages(
            messages,
            failing_chat,
            context_window=1000,
        )
        assert result is not None
        assert result[0]["role"] == "user"
        assert "Omitted" in result[0]["content"]
        assert len(result) < len(messages)


class TestContextWindowForModel:
    def test_prefix_match_longest_wins(self):
        from dashscope_agenticx.config import context_window_for_model

        assert context_window_for_model("qwen-turbo") == 1_000_000
        assert context_window_for_model("qwen-turbo-2025-04-28") == 1_000_000
        assert context_window_for_model("qwen3.7-plus") == 128_000
        assert context_window_for_model("claude-sonnet-4-20250514") == 200_000
        assert context_window_for_model("gpt-4o") == 128_000
        assert context_window_for_model("o3-mini") == 200_000

    def test_unknown_model_gets_default(self):
        from dashscope_agenticx.config import (
            DEFAULT_CONTEXT_WINDOW,
            context_window_for_model,
        )

        assert (
            context_window_for_model("some-future-model")
            == DEFAULT_CONTEXT_WINDOW
        )
        assert context_window_for_model("") == DEFAULT_CONTEXT_WINDOW
