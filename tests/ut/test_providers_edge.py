# -*- coding: utf-8 -*-
"""Edge-case / multi-angle tests for dashscope_agenticx.providers.

Complements test_providers.py / test_provider_response_format.py by covering
streaming SSE parsing, error injection, protocol conversion round-trips and
profile-chain state machines that the happy-path suite does not reach.
"""

from __future__ import annotations

import json
import types
from types import SimpleNamespace

import httpx
import pytest

from dashscope_agenticx.providers import _create_provider, get_provider_chain
from dashscope_agenticx.providers.anthropic import AnthropicProvider
from dashscope_agenticx.providers.base import LLMChunk
from dashscope_agenticx.providers.openai import OpenAIProvider
from dashscope_agenticx.providers.profile import ProviderChain, ProviderProfile
from dashscope_agenticx.providers.tongyi import TongyiProvider


class _MockTransport:
    def __init__(self, handler):
        self.handler = handler

    def inject(self, monkeypatch):
        transport = httpx.MockTransport(self.handler)
        original_client = httpx.AsyncClient

        def factory(*args, **kwargs):
            kwargs["transport"] = transport
            return original_client(*args, **kwargs)

        monkeypatch.setattr(
            "dashscope_agenticx.providers.tongyi.httpx.AsyncClient",
            factory,
        )


def _sse(lines: list[str]) -> bytes:
    return ("\n".join(lines) + "\n").encode()


def _d(obj) -> str:
    """Build one native SSE data line from an OpenAI-shaped payload.

    The native route nests choices under output and streams
    choices[].message (not .delta); usage is keyed input/output_tokens.
    """
    native: dict = {}
    choices = obj.get("choices")
    if choices is not None:
        native["output"] = {
            "choices": [
                {
                    "message": {"role": "assistant", **c.get("delta", {})},
                    "finish_reason": c.get("finish_reason"),
                }
                for c in choices
            ],
        }
    usage = obj.get("usage")
    if usage:
        native_usage = {
            "input_tokens": usage.get("prompt_tokens"),
            "output_tokens": usage.get("completion_tokens"),
            "total_tokens": usage.get("total_tokens"),
        }
        if "prompt_tokens_details" in usage:
            native_usage["prompt_tokens_details"] = usage[
                "prompt_tokens_details"
            ]
        native["usage"] = native_usage
    return "data: " + json.dumps(native, ensure_ascii=False)


# ===========================================================================
# Tongyi streaming SSE parsing
# ===========================================================================


class TestTongyiStreamParsing:
    async def test_content_and_reasoning_deltas(self, monkeypatch):
        lines = [
            'data: {"output": {"choices": [{"finish_reason": "null", '
            '"message": {"role": "assistant", "reasoning_content": '
            '"想"}}]}, "request_id": "r1"}',
            'data: {"output": {"choices": [{"finish_reason": "null", '
            '"message": {"role": "assistant", "content": "你"}}]}, '
            '"request_id": "r1"}',
            'data: {"output": {"choices": [{"finish_reason": "stop", '
            '"message": {"role": "assistant", "content": "好"}}]}, '
            '"request_id": "r1"}',
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert [
            c.delta_reasoning_content
            for c in chunks
            if c.delta_reasoning_content
        ] == ["想"]
        assert "".join(c.delta_content for c in chunks) == "你好"
        assert chunks[-1].finish_reason == "stop"

    async def test_tool_calls_accumulated_across_chunks(self, monkeypatch):
        lines = [
            _d(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {
                                            "name": "read_file",
                                            "arguments": '{"pa',
                                        },
                                    },
                                ],
                            },
                            "finish_reason": None,
                        },
                    ],
                },
            ),
            _d(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {
                                            "arguments": 'th": "/x"}',
                                        },
                                    },
                                ],
                            },
                            "finish_reason": None,
                        },
                    ],
                },
            ),
            _d(
                {
                    "choices": [{"delta": {}, "finish_reason": "tool_calls"}],
                    "usage": {
                        "prompt_tokens": 3,
                        "completion_tokens": 4,
                        "total_tokens": 7,
                    },
                },
            ),
            "data: [DONE]",
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        final = [c for c in chunks if c.tool_calls][-1]
        assert final.tool_calls[0].id == "c1"
        assert final.tool_calls[0].name == "read_file"
        assert final.tool_calls[0].arguments == {"path": "/x"}
        assert final.usage == {
            "input_tokens": 3,
            "output_tokens": 4,
            "total_tokens": 7,
            "cached_tokens": 0,
        }

    async def test_parallel_tool_calls_separate_slots(self, monkeypatch):
        lines = [
            _d(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {
                                            "name": "a",
                                            "arguments": "{}",
                                        },
                                    },
                                    {
                                        "index": 1,
                                        "id": "c2",
                                        "function": {
                                            "name": "b",
                                            "arguments": '{"x": 1}',
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        },
                    ],
                },
            ),
            "data: [DONE]",
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        calls = chunks[0].tool_calls
        assert [c.name for c in calls] == ["a", "b"]
        assert calls[1].arguments == {"x": 1}

    async def test_split_json_line_buffered(self, monkeypatch):
        # Server splits one JSON payload across two data: lines.
        lines = [
            'data: {"output": {"choi',
            'data: ces": [{"finish_reason": "stop", "message": {"role": '
            '"assistant", "content": "拼合"}}]}, "request_id": "r1"}',
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert "".join(c.delta_content for c in chunks) == "拼合"

    async def test_truncated_tool_args_repaired(self, monkeypatch):
        lines = [
            _d(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {
                                            "name": "f",
                                            "arguments": '{"path": "/tmp',
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        },
                    ],
                },
            ),
            "data: [DONE]",
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[0].tool_calls[0].arguments == {"path": "/tmp"}

    async def test_unrepairable_tool_args_become_empty(self, monkeypatch):
        lines = [
            _d(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {
                                            "name": "f",
                                            "arguments": "!!!not json",
                                        },
                                    },
                                ],
                            },
                            "finish_reason": "tool_calls",
                        },
                    ],
                },
            ),
            "data: [DONE]",
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[0].tool_calls[0].arguments == {}

    async def test_orphan_tools_flushed_without_finish(self, monkeypatch):
        # Stream drops before any finish_reason — pending tools must surface.
        lines = [
            _d(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "id": "c1",
                                        "function": {
                                            "name": "f",
                                            "arguments": '{"a": 1}',
                                        },
                                    },
                                ],
                            },
                            "finish_reason": None,
                        },
                    ],
                },
            ),
            "data: [DONE]",
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[0].tool_calls[0].name == "f"
        assert chunks[0].finish_reason == "stop"

    async def test_orphan_tool_without_name_skipped(self, monkeypatch):
        lines = [
            _d(
                {
                    "choices": [
                        {
                            "delta": {
                                "tool_calls": [
                                    {
                                        "index": 0,
                                        "function": {"arguments": "{}"},
                                    },
                                ],
                            },
                            "finish_reason": None,
                        },
                    ],
                },
            ),
            "data: [DONE]",
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert all(not c.tool_calls for c in chunks)

    async def test_stream_error_status_raises(self, monkeypatch):
        def handler(request):
            return httpx.Response(500, content=b"server boom")

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        with pytest.raises(RuntimeError, match="500"):
            async for _ in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            ):
                pass

    async def test_stream_timeout_raises_chinese(self, monkeypatch):
        def handler(request):
            raise httpx.ReadTimeout("slow", request=request)

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        with pytest.raises(RuntimeError, match="timed out"):
            async for _ in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            ):
                pass

    async def test_stream_connect_error_raises_chinese(self, monkeypatch):
        def handler(request):
            raise httpx.ConnectError("refused", request=request)

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        with pytest.raises(RuntimeError, match="Cannot connect"):
            async for _ in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            ):
                pass

    async def test_non_data_lines_ignored(self, monkeypatch):
        lines = [
            ": ping",
            "",
            'data: {"output": {"choices": [{"finish_reason": "stop", '
            '"message": {"role": "assistant", "content": "x"}}]}, '
            '"request_id": "r1"}',
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert "".join(c.delta_content for c in chunks) == "x"

    async def test_chunk_without_choices_skipped(self, monkeypatch):
        lines = [
            'data: {"output": {"choices": []}, "usage": {"input_tokens": '
            '1, "output_tokens": 2, "total_tokens": 3}, '
            '"request_id": "r1"}',
            'data: {"output": {"choices": [{"finish_reason": "stop", '
            '"message": {"role": "assistant", "content": "x"}}]}, '
            '"request_id": "r1"}',
        ]

        def handler(request):
            return httpx.Response(200, content=_sse(lines))

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[-1].usage == {
            "input_tokens": 1,
            "output_tokens": 2,
            "total_tokens": 3,
            "cached_tokens": 0,
        }

    async def test_anthropic_protocol_streaming(self, monkeypatch):
        captured: dict = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                content=_sse(
                    [
                        'data: {"output": {"choices": [{"finish_reason": '
                        '"stop", "message": {"role": "assistant", '
                        '"content": "ok"}}]}, "request_id": "r1"}',
                    ],
                ),
            )

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-t", protocol="anthropic")
        messages = [
            {"role": "system", "content": "sys"},
            {"role": "user", "content": "hi"},
        ]
        chunks = [c async for c in provider.chat_stream(messages)]
        assert "".join(c.delta_content for c in chunks) == "ok"
        # system message must be re-injected as OpenAI system role
        assert captured["body"]["input"]["messages"][0]["role"] == "system"
        assert captured["body"]["input"]["messages"][0]["content"] == "sys"


class TestTongyiHelpers:
    def test_parse_tool_calls_dict_args(self):
        p = TongyiProvider(api_key="k")
        calls = p._parse_tool_calls(
            [{"id": "1", "function": {"name": "f", "arguments": {"a": 1}}}],
        )
        assert calls[0].arguments == {"a": 1}

    def test_parse_tool_calls_bad_types(self):
        p = TongyiProvider(api_key="k")
        calls = p._parse_tool_calls(
            [
                {"id": "1", "function": {"name": "f", "arguments": 123}},
                {"id": "2", "function": {"name": "", "arguments": "{}"}},
                {"id": "3", "function": {"name": "g", "arguments": None}},
            ],
        )
        # bad-typed args -> {}, empty name skipped, None args -> {}
        assert [c.name for c in calls] == ["f", "g"]
        assert calls[0].arguments == {}
        assert calls[1].arguments == {}

    def test_parse_tool_calls_object_style(self):
        p = TongyiProvider(api_key="k")
        call = SimpleNamespace(
            id="x",
            function=SimpleNamespace(name="f", arguments='{"a": 2}'),
        )
        calls = p._parse_tool_calls([call])
        assert calls[0].arguments == {"a": 2}

    def test_convert_tools_passthrough_openai_format(self):
        p = TongyiProvider(api_key="k")
        already = [
            {
                "type": "function",
                "function": {
                    "name": "f",
                    "description": "d",
                    "parameters": {},
                },
            },
        ]
        assert p._convert_tools(already) == already

    def test_convert_tools_none_and_empty(self):
        p = TongyiProvider(api_key="k")
        assert p._convert_tools(None) is None
        assert p._convert_tools([]) is None

    def test_safe_get_fallbacks(self):
        from dashscope_agenticx.providers.tongyi import _safe_get

        assert _safe_get({"a": 1}, "a") == 1
        assert _safe_get(SimpleNamespace(a=2), "a") == 2
        assert _safe_get(None, "a", "d") == "d"

    def test_extract_usage_none(self):
        from dashscope_agenticx.providers.tongyi import _extract_usage

        assert _extract_usage({}) is None
        assert _extract_usage({"usage": None}) is None


# ===========================================================================
# OpenAI provider edge cases
# ===========================================================================


class _R:
    """Mock response whose missing attrs resolve to None (like SDK models)."""

    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def __getattr__(self, name):
        return None


def _make_openai_provider(monkeypatch):
    from unittest.mock import patch

    with patch(
        "dashscope_agenticx.providers.openai.AsyncOpenAI",
    ) as MockClient:
        instance = MockClient.return_value
        provider = OpenAIProvider.__new__(OpenAIProvider)
        provider.model = "gpt-4o"
        provider.client = instance
        return provider, instance


async def _aiter(items):
    for i in items:
        yield i


class TestOpenAIMessageConversion:
    def test_tool_role_message(self, monkeypatch):
        provider, _ = _make_openai_provider(monkeypatch)
        out = provider._convert_messages(
            [{"role": "tool", "content": "res", "tool_use_id": "tu1"}],
        )
        assert out == [
            {"role": "tool", "content": "res", "tool_call_id": "tu1"},
        ]

    def test_assistant_reasoning_echoed(self, monkeypatch):
        provider, _ = _make_openai_provider(monkeypatch)
        out = provider._convert_messages(
            [{"role": "assistant", "content": "c", "reasoning_content": "r"}],
        )
        assert out[0]["reasoning_content"] == "r"

    def test_assistant_tool_calls_dict_args_serialized(self, monkeypatch):
        provider, _ = _make_openai_provider(monkeypatch)
        out = provider._convert_messages(
            [
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "1",
                            "function": {"name": "f", "arguments": {"a": 1}},
                        },
                        {
                            "id": "2",
                            "function": {"name": "g", "arguments": '{"b": 2}'},
                        },
                    ],
                },
            ],
        )
        args = [tc["function"]["arguments"] for tc in out[0]["tool_calls"]]
        assert json.loads(args[0]) == {"a": 1}
        assert args[1] == '{"b": 2}'

    def test_plain_user_passthrough(self, monkeypatch):
        provider, _ = _make_openai_provider(monkeypatch)
        out = provider._convert_messages([{"role": "user", "content": "hi"}])
        assert out == [{"role": "user", "content": "hi"}]


class TestOpenAIErrors:
    async def test_chat_404_wrapped(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)

        class _E(Exception):
            status_code = 404

        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(side_effect=_E("nope"))
        with pytest.raises(RuntimeError, match="404"):
            await provider.chat([{"role": "user", "content": "hi"}])

    async def test_chat_other_error_passthrough(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)
        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(side_effect=ValueError("x"))
        with pytest.raises(ValueError):
            await provider.chat([{"role": "user", "content": "hi"}])

    async def test_stream_404_wrapped(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)

        class _E(Exception):
            status_code = 404

        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(side_effect=_E("nope"))
        with pytest.raises(RuntimeError, match="404"):
            async for _ in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            ):
                pass


class TestOpenAIStreaming:
    async def test_usage_chunk_attached_to_finish(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)
        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(
            return_value=_aiter(
                [
                    _R(
                        choices=[
                            _R(delta=_R(content="hi"), finish_reason=None),
                        ],
                    ),
                    _R(choices=[_R(delta=_R(), finish_reason="stop")]),
                    # terminal usage-only chunk (empty choices)
                    _R(
                        choices=[],
                        usage=_R(
                            prompt_tokens=5,
                            completion_tokens=6,
                            total_tokens=11,
                        ),
                    ),
                ],
            ),
        )
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        final = chunks[-1]
        assert final.finish_reason == "stop"
        assert final.usage == {
            "input_tokens": 5,
            "output_tokens": 6,
            "total_tokens": 11,
            "cached_tokens": 0,
        }

    async def test_finish_without_usage_chunk(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)
        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(
            return_value=_aiter(
                [
                    _R(
                        choices=[
                            _R(delta=_R(content="x"), finish_reason="stop"),
                        ],
                    ),
                ],
            ),
        )
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[-1].finish_reason == "stop"
        assert chunks[-1].usage is None

    async def test_length_finish_emitted(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)
        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(
            return_value=_aiter(
                [
                    _R(
                        choices=[
                            _R(delta=_R(content="x"), finish_reason="length"),
                        ],
                    ),
                ],
            ),
        )
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[-1].finish_reason == "length"

    async def test_stream_reasoning_delta(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)
        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(
            return_value=_aiter(
                [
                    _R(
                        choices=[
                            _R(
                                delta=_R(reasoning_content="思考"),
                                finish_reason="stop",
                            ),
                        ],
                    ),
                ],
            ),
        )
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[0].delta_reasoning_content == "思考"

    async def test_stream_malformed_tool_args_repaired(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)
        from unittest.mock import AsyncMock

        tc = _R(index=0, id="c1", function=_R(name="f", arguments='{"a": "x'))
        client.chat.completions.create = AsyncMock(
            return_value=_aiter(
                [
                    _R(
                        choices=[
                            _R(
                                delta=_R(tool_calls=[tc]),
                                finish_reason="tool_calls",
                            ),
                        ],
                    ),
                ],
            ),
        )
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[0].tool_calls[0].arguments == {"a": "x"}
        assert chunks[-1].finish_reason == "tool_calls"

    async def test_stream_null_delta_skipped(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)
        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(
            return_value=_aiter(
                [
                    _R(choices=[_R(delta=None, finish_reason=None)]),
                    _R(
                        choices=[
                            _R(delta=_R(content="y"), finish_reason="stop"),
                        ],
                    ),
                ],
            ),
        )
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert "".join(c.delta_content for c in chunks) == "y"

    async def test_stream_options_requested(self, monkeypatch):
        provider, client = _make_openai_provider(monkeypatch)
        from unittest.mock import AsyncMock

        client.chat.completions.create = AsyncMock(return_value=_aiter([]))
        async for _ in provider.chat_stream(
            [{"role": "user", "content": "hi"}],
        ):
            pass
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["stream_options"] == {"include_usage": True}


# ===========================================================================
# Anthropic streaming + error paths
# ===========================================================================


def _make_anthropic_provider(monkeypatch):
    provider = AnthropicProvider.__new__(AnthropicProvider)
    provider.model = "claude-test"
    from unittest.mock import MagicMock

    provider.client = MagicMock()
    return provider


class _FakeStream:
    def __init__(self, events, final=None):
        self.events = events
        self._final = final

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    def __aiter__(self):
        return self._gen()

    async def _gen(self):
        for e in self.events:
            yield e

    async def get_final_message(self):
        if self._final is None:
            raise RuntimeError("no final")
        return self._final


class TestAnthropicStreaming:
    async def test_tool_use_flow(self, monkeypatch):
        provider = _make_anthropic_provider(monkeypatch)
        events = [
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(
                    type="tool_use",
                    id="tu1",
                    name="f",
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json='{"a":',
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json=" 1}",
                ),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(type="message_stop"),
        ]
        provider.client.messages.stream = lambda **kw: _FakeStream(events)
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        tool_chunk = [c for c in chunks if c.tool_calls][0]
        assert tool_chunk.tool_calls[0].name == "f"
        assert tool_chunk.tool_calls[0].arguments == {"a": 1}
        assert chunks[-1].finish_reason == "end_turn"

    async def test_malformed_tool_json_surfaces_notice(self, monkeypatch):
        provider = _make_anthropic_provider(monkeypatch)
        events = [
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(
                    type="tool_use",
                    id="tu1",
                    name="f",
                ),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(
                    type="input_json_delta",
                    partial_json="{bad",
                ),
            ),
            SimpleNamespace(type="content_block_stop"),
            SimpleNamespace(type="message_stop"),
        ]
        provider.client.messages.stream = lambda **kw: _FakeStream(events)
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert any(
            "Failed to parse tool args" in c.delta_content for c in chunks
        )
        tool_chunk = [c for c in chunks if c.tool_calls][0]
        assert tool_chunk.tool_calls[0].arguments == {}

    async def test_text_delta_variants(self, monkeypatch):
        provider = _make_anthropic_provider(monkeypatch)
        events = [
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="text", text="A"),
            ),
            SimpleNamespace(
                type="content_block_delta",
                delta=SimpleNamespace(type="custom", text="B"),
            ),
            SimpleNamespace(type="message_stop"),
        ]
        provider.client.messages.stream = lambda **kw: _FakeStream(events)
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert "".join(c.delta_content for c in chunks) == "AB"

    async def test_full_text_on_block_start(self, monkeypatch):
        provider = _make_anthropic_provider(monkeypatch)
        events = [
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(type="text", text="整段"),
            ),
            SimpleNamespace(type="message_stop"),
        ]
        provider.client.messages.stream = lambda **kw: _FakeStream(events)
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert chunks[0].delta_content == "整段"

    async def test_usage_accumulation(self, monkeypatch):
        provider = _make_anthropic_provider(monkeypatch)
        events = [
            SimpleNamespace(
                type="content_block_start",
                content_block=SimpleNamespace(type="text", text="x"),
            ),
            SimpleNamespace(
                type="message_start",
                message=SimpleNamespace(usage=SimpleNamespace(input_tokens=9)),
            ),
            SimpleNamespace(
                type="message_delta",
                usage=SimpleNamespace(output_tokens=4),
            ),
            SimpleNamespace(type="message_stop"),
        ]
        provider.client.messages.stream = lambda **kw: _FakeStream(events)
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        final = chunks[-1]
        assert final.usage == {
            "input_tokens": 9,
            "output_tokens": 4,
            "total_tokens": 13,
            "cached_tokens": 0,
        }

    async def test_silent_failure_generic(self, monkeypatch):
        provider = _make_anthropic_provider(monkeypatch)
        final = SimpleNamespace(stop_reason="end_turn")
        provider.client.messages.stream = lambda **kw: _FakeStream(
            [],
            final=final,
        )
        with pytest.raises(RuntimeError, match="returned no content"):
            async for _ in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            ):
                pass

    async def test_silent_failure_specific_stop_reason(self, monkeypatch):
        # Regression: the specific stop_reason error used to be swallowed by
        # an inner except and replaced by the generic message.
        provider = _make_anthropic_provider(monkeypatch)
        final = SimpleNamespace(stop_reason="max_tokens")
        provider.client.messages.stream = lambda **kw: _FakeStream(
            [],
            final=final,
        )
        with pytest.raises(RuntimeError, match="stop_reason=max_tokens"):
            async for _ in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            ):
                pass

    async def test_stream_timeout_wrapped(self, monkeypatch):
        provider = _make_anthropic_provider(monkeypatch)

        def _stream(**kw):
            raise httpx.ReadTimeout(
                "slow",
                request=httpx.Request("POST", "http://x"),
            )

        provider.client.messages.stream = _stream
        with pytest.raises(RuntimeError, match="timeout"):
            async for _ in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            ):
                pass


class TestAnthropicChatErrors:
    async def test_not_found_wrapped(self, monkeypatch):
        anthropic = pytest.importorskip("anthropic")
        provider = _make_anthropic_provider(monkeypatch)
        req = httpx.Request("POST", "http://x")
        resp = httpx.Response(404, request=req)
        err = anthropic.NotFoundError("nf", response=resp, body=None)

        from unittest.mock import AsyncMock

        provider.client.messages.create = AsyncMock(side_effect=err)
        with pytest.raises(RuntimeError, match="404"):
            await provider.chat([{"role": "user", "content": "hi"}])

    async def test_api_error_wrapped(self, monkeypatch):
        anthropic = pytest.importorskip("anthropic")
        provider = _make_anthropic_provider(monkeypatch)
        req = httpx.Request("POST", "http://x")
        err = anthropic.APIError("boom", req, body=None)

        from unittest.mock import AsyncMock

        provider.client.messages.create = AsyncMock(side_effect=err)
        with pytest.raises(RuntimeError, match="API request failed"):
            await provider.chat([{"role": "user", "content": "hi"}])

    async def test_stream_api_error_wrapped(self, monkeypatch):
        anthropic = pytest.importorskip("anthropic")
        provider = _make_anthropic_provider(monkeypatch)
        req = httpx.Request("POST", "http://x")

        def _stream(**kw):
            raise anthropic.APIError("boom", req, body=None)

        provider.client.messages.stream = _stream
        with pytest.raises(RuntimeError, match="API request failed"):
            async for _ in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            ):
                pass


# ===========================================================================
# Profile / chain state machine
# ===========================================================================


def _config(**over):
    base = dict(
        provider="tongyi",
        model="qwen-plus",
        base_url=None,
        timeout=30,
        protocol="openai",
        fallback_providers=[],
        tongyi_api_key="sk-x",
        anthropic_api_key="",
        openai_api_key="",
    )
    base.update(over)
    return SimpleNamespace(**base)


class TestProfileEdge:
    def test_extension_lookup_failure_swallowed(self, monkeypatch):
        import dashscope_agenticx.extensions as ext

        monkeypatch.setattr(
            ext,
            "find_provider",
            lambda *a, **k: (_ for _ in ()).throw(IOError("bad")),
        )
        from dashscope_agenticx.providers.profile import (
            build_profiles_from_config,
        )

        profiles = build_profiles_from_config(_config())
        assert profiles[0].provider == "tongyi"

    def test_fallback_without_key_skipped(self):
        from dashscope_agenticx.providers.profile import (
            build_profiles_from_config,
        )

        cfg = _config(fallback_providers=["anthropic"], anthropic_api_key="")
        profiles = build_profiles_from_config(cfg)
        assert [p.provider for p in profiles] == ["tongyi"]

    def test_fallback_dedup_with_primary(self):
        from dashscope_agenticx.providers.profile import (
            build_profiles_from_config,
        )

        cfg = _config(fallback_providers=["tongyi"])
        profiles = build_profiles_from_config(cfg)
        assert len(profiles) == 1

    def test_chain_instance_cached(self, monkeypatch):
        profiles = [
            ProviderProfile(
                name="tongyi",
                provider="tongyi",
                model="m",
                api_key="sk-1",
            ),
        ]
        chain = ProviderChain(profiles)
        a = chain._get_instance(0)
        b = chain._get_instance(0)
        assert a is b

    def test_chain_empty_profiles_raises(self):
        import dashscope_agenticx.providers as providers_mod

        cfg = _config()
        # Simulate an empty profile build to hit the defensive guard.
        import dashscope_agenticx.providers.profile as profile_mod

        orig = profile_mod.build_profiles_from_config
        providers_mod.build_profiles_from_config = lambda c: []
        try:
            with pytest.raises(RuntimeError):
                providers_mod.get_provider_chain(cfg)
        finally:
            providers_mod.build_profiles_from_config = orig

    def test_create_provider_dashscope_protocol(self):
        p = _create_provider(
            ProviderProfile(
                name="custom",
                provider="custom",
                model="m",
                api_key="k",
                protocol="dashscope",
            ),
        )
        assert isinstance(p, TongyiProvider)
