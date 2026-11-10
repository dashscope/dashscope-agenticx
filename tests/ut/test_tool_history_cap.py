# -*- coding: utf-8 -*-
"""Tests for tool-result history capping and old tool message shrinking."""

from __future__ import annotations

from dashscope_agenticx.agent import _tool_message
from dashscope_agenticx.compression import (
    OLD_TOOL_MESSAGE_MAX_CHARS,
    shrink_old_tool_messages,
)
from dashscope_agenticx.providers.base import ToolCall
from dashscope_agenticx.utils.messages import (
    MAX_TOOL_RESULT_HISTORY_CHARS,
    tool_result_for_history,
)


class TestToolResultForHistory:
    def test_small_result_unchanged(self):
        assert tool_result_for_history("short") == "short"

    def test_exact_limit_unchanged(self):
        text = "x" * MAX_TOOL_RESULT_HISTORY_CHARS
        assert tool_result_for_history(text) == text

    def test_large_result_truncated_with_marker(self):
        text = "abcdefgh" * 4000  # 32k chars
        out = tool_result_for_history(text)
        assert len(out) <= len(text)
        assert len(out) < MAX_TOOL_RESULT_HISTORY_CHARS + 200
        assert "[omitted" in out
        assert out.startswith(text[:1000])
        assert out.endswith(text[-1000:])

    def test_custom_max_chars(self):
        out = tool_result_for_history("y" * 500, max_chars=100)
        assert "[omitted" in out

    def test_note_lands_inside_the_marker(self):
        out = tool_result_for_history("z" * 9000, note=" — re-run read_file")
        assert "[omitted 1000 chars — re-run read_file]" in out


class TestToolMessageRestoreNote:
    def test_restorable_tool_gets_a_note_at_history_cap(self):
        tc = ToolCall(id="c1", name="read_file", arguments={"path": "a.py"})
        msg = _tool_message(tc, "x" * 9000)
        assert 're-run read_file with {"path": "a.py"}' in msg["content"]
        assert msg["tool_call_id"] == "c1"
        assert msg["name"] == "read_file"

    def test_shell_result_gets_no_note(self):
        tc = ToolCall(
            id="c2",
            name="run_command",
            arguments={"command": "make"},
        )
        assert "re-run" not in _tool_message(tc, "x" * 9000)["content"]

    def test_small_result_stored_verbatim(self):
        tc = ToolCall(id="c3", name="read_file", arguments={"path": "a.py"})
        assert _tool_message(tc, "short")["content"] == "short"


class TestToolMessageRedaction:
    """Secrets are scrubbed on the way into context, not just onto disk.

    ``save_session`` already ran ``sanitize`` over the persisted history, so
    the session file was the only thing protected: a key read out of the
    environment was redacted there and handed to the model in full.
    """

    SECRET = "sk-unittest-0123456789"

    def _env_call(self, call_id: str) -> ToolCall:
        return ToolCall(
            id=call_id,
            name="run_command",
            arguments={"command": "env"},
        )

    def test_secret_in_a_small_result_is_redacted(self):
        msg = _tool_message(self._env_call("c4"), f"key={self.SECRET}\n")
        assert self.SECRET not in msg["content"]
        assert "[REDACTED]" in msg["content"]

    def test_assignment_keeps_the_variable_name(self):
        """Only the value goes.

        Redacting the whole line would hide that the variable is set at all,
        which is the fact the model usually needs in order to answer.
        """
        msg = _tool_message(
            self._env_call("c5"),
            f"DASHSCOPE_API_KEY={self.SECRET}\nPATH=/usr/bin\n",
        )
        assert self.SECRET not in msg["content"]
        assert "DASHSCOPE_API_KEY=" in msg["content"]
        assert "PATH=/usr/bin" in msg["content"]

    def test_redaction_happens_before_truncation(self):
        """A secret straddling the cut is removed, not split in half.

        ``truncate_head_tail`` keeps ``int(8000 * 0.6)`` = 4800 head chars, so
        a secret starting at 4795 is cut down to ``sk-u``. That fragment is
        too short for the ``sk-`` pattern to match, which is exactly what
        makes scrubbing after truncation a leak.
        """
        text = "x" * 4794 + "\n" + self.SECRET + "\n" + "y" * 4000
        msg = _tool_message(self._env_call("c6"), text)
        assert self.SECRET not in msg["content"]
        # The discriminating assertion. Scrubbing after truncation would leave
        # this head fragment behind, because a lone "u" is too short for the
        # sk- pattern's {6,} quantifier. ([REDACTED] straddles the same cut and
        # so is deliberately not asserted on.)
        assert "sk-" not in msg["content"]
        assert "[omitted" in msg["content"]  # still truncated as before

    def test_ordinary_output_is_untouched(self):
        tc = ToolCall(id="c7", name="read_file", arguments={"path": "a.py"})
        text = "def main():\n    print('hello')\n    return 0\n"
        assert _tool_message(tc, text)["content"] == text


class TestShrinkOldToolMessages:
    def _messages(self, sizes: list[int]) -> list[dict]:
        return [
            {"role": "tool", "name": "read_file", "content": "x" * s}
            for s in sizes
        ]

    def test_shrinks_old_tool_messages_in_place(self):
        msgs = self._messages([5000, 100, 5000, 5000])
        n = shrink_old_tool_messages(msgs, keep_recent=2)
        assert n == 1  # only index 0 is outside the recent tail of 2
        assert len(msgs[0]["content"]) < 5000
        assert msgs[2]["content"] == "x" * 5000  # recent tail untouched

    def test_recent_tail_protected(self):
        msgs = self._messages([5000, 5000])
        n = shrink_old_tool_messages(msgs, keep_recent=2)
        assert n == 0

    def test_idempotent(self):
        msgs = self._messages([5000, 5000, 5000])
        first = shrink_old_tool_messages(msgs, keep_recent=1)
        second = shrink_old_tool_messages(msgs, keep_recent=1)
        assert first >= 1
        assert second == 0

    def test_non_tool_messages_untouched(self):
        msgs = [
            {"role": "user", "content": "x" * 9000},
            {"role": "assistant", "content": "y" * 9000},
            {"role": "tool", "content": "z" * 9000},
            {"role": "user", "content": "tail"},
        ]
        shrink_old_tool_messages(msgs, keep_recent=1)
        assert len(msgs[0]["content"]) == 9000
        assert len(msgs[1]["content"]) == 9000
        assert len(msgs[2]["content"]) <= OLD_TOOL_MESSAGE_MAX_CHARS + 200

    def test_list_content_untouched(self):
        msgs = [
            {
                "role": "tool",
                "content": [{"type": "text", "text": "x" * 9000}],
            },
            {"role": "user", "content": "tail"},
        ]
        n = shrink_old_tool_messages(msgs, keep_recent=1)
        assert n == 0


def _thread(name: str, arguments, result: str) -> list[dict]:
    """An assistant tool_call, its tool result, and a recent tail message."""
    return [
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": "call_1",
                    "type": "function",
                    "function": {"name": name, "arguments": arguments},
                },
            ],
        },
        {
            "role": "tool",
            "content": result,
            "name": name,
            "tool_call_id": "call_1",
            "tool_use_id": "call_1",
        },
        {"role": "user", "content": "tail"},
    ]


class TestShrinkRestoreNote:
    def test_restorable_tool_records_the_original_call(self):
        msgs = _thread("read_file", '{"path": "/tmp/a.py"}', "x" * 9000)
        assert shrink_old_tool_messages(msgs, keep_recent=1) == 1
        assert (
            're-run read_file with {"path": "/tmp/a.py"}' in msgs[1]["content"]
        )

    def test_shell_output_gets_no_restore_hint(self):
        # Re-running a command may have side effects, so no false promise.
        msgs = _thread("run_command", '{"command": "make"}', "x" * 9000)
        shrink_old_tool_messages(msgs, keep_recent=1)
        assert "[omitted 7000 chars]" in msgs[1]["content"]
        assert "re-run" not in msgs[1]["content"]

    def test_missing_arguments_degrade_to_plain_marker(self):
        msgs = _thread("read_file", None, "x" * 9000)
        shrink_old_tool_messages(msgs, keep_recent=1)
        assert "[omitted 7000 chars]" in msgs[1]["content"]
        assert "re-run" not in msgs[1]["content"]

    def test_empty_arguments_degrade_to_plain_marker(self):
        msgs = _thread("read_file", "", "x" * 9000)
        shrink_old_tool_messages(msgs, keep_recent=1)
        assert "re-run" not in msgs[1]["content"]

    def test_dict_arguments_are_serialized(self):
        msgs = _thread(
            "search_files",
            {"pattern": "TODO", "path": "."},
            "y" * 9000,
        )
        shrink_old_tool_messages(msgs, keep_recent=1)
        assert "re-run search_files" in msgs[1]["content"]
        assert "TODO" in msgs[1]["content"]

    def test_idempotent_with_note(self):
        msgs = _thread("list_directory", '{"path": "/tmp"}', "z" * 9000)
        assert shrink_old_tool_messages(msgs, keep_recent=1) == 1
        assert shrink_old_tool_messages(msgs, keep_recent=1) == 0

    def test_read_file_window_trailer_is_not_a_truncation_marker(self):
        # read_file's own trailer says "[omitted N of M lines ...]".  That is
        # content, so the result must still be shrunk.
        trailer = (
            "\n[omitted 3000 of 5000 lines — "
            "read_file path=a.py offset=201 to continue]"
        )
        msgs = _thread("read_file", '{"path": "a.py"}', "x" * 3000 + trailer)
        assert shrink_old_tool_messages(msgs, keep_recent=1) == 1
        assert "re-run read_file" in msgs[1]["content"]

    def test_unmatched_call_id_degrades(self):
        msgs = _thread("read_file", '{"path": "a.py"}', "x" * 9000)
        msgs[1]["tool_call_id"] = "call_other"
        msgs[1]["tool_use_id"] = "call_other"
        shrink_old_tool_messages(msgs, keep_recent=1)
        assert "re-run" not in msgs[1]["content"]
