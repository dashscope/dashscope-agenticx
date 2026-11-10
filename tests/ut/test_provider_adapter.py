# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.providers.adapter — protocol conversion functions."""

import json

from dashscope_agenticx.providers.adapter import (
    anthropic_to_openai_request,
    anthropic_to_openai_response,
    openai_to_anthropic_response,
)


class TestAnthropicToOpenAIRequest:
    def test_system_prompt(self):
        result = anthropic_to_openai_request(
            messages=[{"role": "user", "content": "hello"}],
            system="you are helpful",
        )
        assert result["messages"][0] == {
            "role": "system",
            "content": "you are helpful",
        }
        assert result["messages"][1] == {"role": "user", "content": "hello"}

    def test_no_system(self):
        result = anthropic_to_openai_request(
            messages=[{"role": "user", "content": "hi"}],
        )
        assert all(m["role"] != "system" for m in result["messages"])

    def test_content_blocks(self):
        result = anthropic_to_openai_request(
            messages=[
                {
                    "role": "user",
                    "content": [
                        {"type": "text", "text": "part 1"},
                        {"type": "text", "text": "part 2"},
                    ],
                },
            ],
        )
        assert result["messages"][0]["content"] == "part 1\npart 2"

    def test_empty_content_blocks(self):
        result = anthropic_to_openai_request(
            messages=[
                {"role": "user", "content": [{"type": "image", "source": {}}]},
            ],
        )
        assert result["messages"][0]["content"] == ""

    def test_tools_conversion(self):
        result = anthropic_to_openai_request(
            messages=[{"role": "user", "content": "hi"}],
            tools=[
                {
                    "name": "read_file",
                    "description": "read a file",
                    "input_schema": {"type": "object", "properties": {}},
                },
            ],
        )
        assert result["tools"][0]["type"] == "function"
        assert result["tools"][0]["function"]["name"] == "read_file"
        assert result["tools"][0]["function"]["parameters"] == {
            "type": "object",
            "properties": {},
        }

    def test_no_tools(self):
        result = anthropic_to_openai_request(
            messages=[{"role": "user", "content": "hi"}],
        )
        assert result["tools"] is None

    def test_kwargs_passthrough(self):
        result = anthropic_to_openai_request(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.7,
            max_tokens=100,
        )
        assert result["temperature"] == 0.7
        assert result["max_tokens"] == 100


class TestOpenaiToAnthropicResponse:
    def test_text_only(self):
        resp = openai_to_anthropic_response(
            {
                "id": "chatcmpl-1",
                "choices": [
                    {
                        "message": {"content": "hello world"},
                        "finish_reason": "stop",
                    },
                ],
                "usage": {
                    "prompt_tokens": 5,
                    "completion_tokens": 2,
                    "total_tokens": 7,
                },
            },
        )
        assert resp["type"] == "message"
        assert resp["role"] == "assistant"
        assert resp["content"][0]["type"] == "text"
        assert resp["content"][0]["text"] == "hello world"
        assert resp["stop_reason"] == "end_turn"
        assert resp["usage"]["input_tokens"] == 5
        assert resp["usage"]["output_tokens"] == 2

    def test_tool_calls(self):
        resp = openai_to_anthropic_response(
            {
                "id": "chatcmpl-2",
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "read_file",
                                        "arguments": '{"path": "/tmp/test.py"}',
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                ],
                "usage": {
                    "prompt_tokens": 10,
                    "completion_tokens": 5,
                    "total_tokens": 15,
                },
            },
        )
        assert resp["stop_reason"] == "tool_use"
        tool_block = next(
            b for b in resp["content"] if b["type"] == "tool_use"
        )
        assert tool_block["name"] == "read_file"
        assert tool_block["input"] == {"path": "/tmp/test.py"}

    def test_tool_calls_invalid_json(self):
        resp = openai_to_anthropic_response(
            {
                "choices": [
                    {
                        "message": {
                            "content": "",
                            "tool_calls": [
                                {
                                    "id": "call_1",
                                    "function": {
                                        "name": "t",
                                        "arguments": "not json",
                                    },
                                },
                            ],
                        },
                        "finish_reason": "tool_calls",
                    },
                ],
            },
        )
        tool_block = next(
            b for b in resp["content"] if b["type"] == "tool_use"
        )
        assert tool_block["input"] == {}

    def test_finish_reason_mapping(self):
        for openai_reason, anthropic_reason in [
            ("stop", "end_turn"),
            ("tool_calls", "tool_use"),
            ("length", "max_tokens"),
        ]:
            resp = openai_to_anthropic_response(
                {
                    "choices": [
                        {
                            "message": {"content": "x"},
                            "finish_reason": openai_reason,
                        },
                    ],
                },
            )
            assert resp["stop_reason"] == anthropic_reason

    def test_empty_response(self):
        resp = openai_to_anthropic_response({})
        assert resp["role"] == "assistant"
        assert resp["content"] == []


class TestToolRoundTrip:
    """P4: tool structures must survive the adapter in both directions."""

    def test_assistant_tool_calls_and_tool_messages(self):
        # Agent internal format: assistant with 2 tool_calls + 2 tool msgs.
        result = anthropic_to_openai_request(
            messages=[
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_1",
                            "function": {
                                "name": "read_file",
                                "arguments": '{"path": "/tmp/a"}',
                            },
                        },
                        {
                            "id": "call_2",
                            "function": {
                                "name": "read_file",
                                "arguments": {"path": "/tmp/b"},
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "content": "aaa",
                    "tool_use_id": "call_1",
                    "tool_call_id": "call_1",
                },
                {
                    "role": "tool",
                    "content": "bbb",
                    "tool_use_id": "call_2",
                    "tool_call_id": "call_2",
                },
            ],
        )
        msgs = result["messages"]
        assert [m["role"] for m in msgs] == [
            "user",
            "assistant",
            "tool",
            "tool",
        ]
        assistant = msgs[1]
        assert [tc["id"] for tc in assistant["tool_calls"]] == [
            "call_1",
            "call_2",
        ]
        # arguments are always serialized to JSON strings on the wire.
        assert (
            assistant["tool_calls"][1]["function"]["arguments"]
            == '{"path": "/tmp/b"}'
        )
        # Tool messages keep the ids that correlate them to the calls.
        assert msgs[2] == {
            "role": "tool",
            "content": "aaa",
            "tool_call_id": "call_1",
        }
        assert msgs[3] == {
            "role": "tool",
            "content": "bbb",
            "tool_call_id": "call_2",
        }

    def test_tool_message_falls_back_to_tool_use_id(self):
        result = anthropic_to_openai_request(
            messages=[{"role": "tool", "content": "x", "tool_use_id": "tu_1"}],
        )
        assert result["messages"][0]["tool_call_id"] == "tu_1"

    def test_anthropic_tool_use_blocks(self):
        # True Anthropic format: tool_use / tool_result content blocks.
        result = anthropic_to_openai_request(
            messages=[
                {
                    "role": "assistant",
                    "content": [
                        {"type": "text", "text": "let me check"},
                        {
                            "type": "tool_use",
                            "id": "tu_1",
                            "name": "read_file",
                            "input": {"path": "/tmp/a"},
                        },
                    ],
                },
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "tool_result",
                            "tool_use_id": "tu_1",
                            "content": "file body",
                        },
                    ],
                },
            ],
        )
        msgs = result["messages"]
        # tool_result-only user message becomes a tool message, not an
        # empty user message that would break tool_call correlation.
        assert [m["role"] for m in msgs] == ["assistant", "tool"]
        assert msgs[0]["content"] == "let me check"
        call = msgs[0]["tool_calls"][0]
        assert call["id"] == "tu_1"
        assert call["function"]["name"] == "read_file"
        assert json.loads(call["function"]["arguments"]) == {"path": "/tmp/a"}
        assert msgs[1] == {
            "role": "tool",
            "content": "file body",
            "tool_call_id": "tu_1",
        }

    def test_anthropic_to_openai_response(self):
        out = anthropic_to_openai_response(
            {
                "content": [
                    {"type": "text", "text": "done"},
                    {
                        "type": "tool_use",
                        "id": "tu_1",
                        "name": "read_file",
                        "input": {"path": "/tmp/a"},
                    },
                ],
            },
        )
        assert out["content"] == "done"
        assert len(out["tool_calls"]) == 1
        call = out["tool_calls"][0]
        assert call["id"] == "tu_1"
        assert call["function"]["name"] == "read_file"
        assert json.loads(call["function"]["arguments"]) == {"path": "/tmp/a"}

    def test_anthropic_to_openai_response_text_only(self):
        out = anthropic_to_openai_response(
            {"content": [{"type": "text", "text": "hi"}]},
        )
        assert out == {"content": "hi", "tool_calls": []}
