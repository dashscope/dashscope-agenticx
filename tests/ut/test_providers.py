# -*- coding: utf-8 -*-
from __future__ import annotations

import json
import types
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dashscope_agenticx.providers import (
    PROVIDER_API_KEY_ENVS,
    _create_provider,
    get_provider,
    get_provider_chain,
)
from dashscope_agenticx.providers.anthropic import AnthropicProvider
from dashscope_agenticx.providers.base import LLMChunk, LLMResponse
from dashscope_agenticx.providers.hardening import (
    HardenedProvider,
    RetriesExhausted,
)
from dashscope_agenticx.providers.hardening import (
    is_retryable_error as _is_retryable_error,
)
from dashscope_agenticx.providers.openai import OpenAIProvider
from dashscope_agenticx.providers.profile import (
    ProviderChain,
    ProviderProfile,
    _detect_provider_from_host,
    _host_of,
    _infer_fallback_names,
    build_profiles_from_config,
    validate_key_for_profile,
)
from dashscope_agenticx.providers.tongyi import (
    DASHSCOPE_BASE_URL,
    TongyiProvider,
)


class _MockResponse:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def __getattr__(self, name):
        return None


# ===== OpenAI provider =====


class TestOpenAIProvider:
    @pytest.mark.parametrize(
        "provider_class",
        [OpenAIProvider, AnthropicProvider, TongyiProvider],
    )
    def test_model_name_normalized_before_requests(self, provider_class):
        provider = provider_class(model=" My-MODEL ", api_key="sk-test")
        assert provider.model == "my-model"

    @pytest.fixture
    def make_provider(self):
        def _make():
            with patch(
                "dashscope_agenticx.providers.openai.AsyncOpenAI",
            ) as MockClient:
                instance = MockClient.return_value
                provider = type(
                    "OpenAIProvider",
                    (object,),
                    {
                        "__init__": lambda s, **kw: None,
                        "model": "gpt-4o",
                        "client": instance,
                    },
                )()
                from dashscope_agenticx.providers.openai import (
                    OpenAIProvider as _Real,
                )

                provider._convert_messages = _Real._convert_messages.__get__(
                    provider,
                    type(provider),
                )
                provider._convert_tools = _Real._convert_tools.__get__(
                    provider,
                    type(provider),
                )
                provider.chat = _Real.chat.__get__(provider, type(provider))
                provider.chat_stream = _Real.chat_stream.__get__(
                    provider,
                    type(provider),
                )
                return provider, instance

        return _make

    async def test_chat_text(self, make_provider):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_MockResponse(
                choices=[
                    _MockResponse(
                        message=_MockResponse(
                            content="hello",
                            reasoning_content=None,
                            tool_calls=None,
                        ),
                    ),
                ],
            ),
        )
        resp = await provider.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "hello"
        assert resp.tool_calls == []

    async def test_chat_usage_extracted(self, make_provider):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_MockResponse(
                choices=[
                    _MockResponse(
                        message=_MockResponse(
                            content="hello",
                            reasoning_content=None,
                            tool_calls=None,
                        ),
                    ),
                ],
                usage=_MockResponse(
                    prompt_tokens=11,
                    completion_tokens=7,
                    total_tokens=18,
                ),
            ),
        )
        resp = await provider.chat([{"role": "user", "content": "hi"}])
        assert resp.usage == {
            "input_tokens": 11,
            "output_tokens": 7,
            "total_tokens": 18,
            "cached_tokens": 0,
        }

    async def test_chat_tool_calls(self, make_provider):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_MockResponse(
                choices=[
                    _MockResponse(
                        message=_MockResponse(
                            content="",
                            reasoning_content=None,
                            tool_calls=[
                                _MockResponse(
                                    id="call_1",
                                    function=_MockResponse(
                                        name="read_file",
                                        arguments='{"path": "/tmp/a"}',
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )
        resp = await provider.chat([{"role": "user", "content": "hi"}])
        assert resp.tool_calls[0].name == "read_file"
        assert resp.tool_calls[0].arguments == {"path": "/tmp/a"}

    async def test_chat_malformed_tool_args(self, make_provider):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_MockResponse(
                choices=[
                    _MockResponse(
                        message=_MockResponse(
                            content="",
                            tool_calls=[
                                _MockResponse(
                                    id="call_1",
                                    function=_MockResponse(
                                        name="read_file",
                                        arguments="not json",
                                    ),
                                ),
                            ],
                        ),
                    ),
                ],
            ),
        )
        resp = await provider.chat([{"role": "user", "content": "hi"}])
        assert resp.tool_calls[0].arguments == {}

    async def test_chat_stream_content(self, make_provider):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=self._async_iter(
                [
                    _MockResponse(
                        choices=[
                            _MockResponse(
                                delta=_MockResponse(content="hello"),
                                finish_reason=None,
                            ),
                        ],
                    ),
                    _MockResponse(
                        choices=[
                            _MockResponse(
                                delta=_MockResponse(content=" world"),
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
        assert [c.delta_content for c in chunks if c.delta_content] == [
            "hello",
            " world",
        ]

    async def test_chat_stream_tool_call(self, make_provider):
        provider, client = make_provider()
        tc1 = _MockResponse(
            index=0,
            id="call_1",
            function=_MockResponse(name="read_file", arguments='{"path":'),
        )
        tc2 = _MockResponse(
            index=0,
            id=None,
            function=_MockResponse(name=None, arguments=' "/tmp/a"}'),
        )
        client.chat.completions.create = AsyncMock(
            return_value=self._async_iter(
                [
                    _MockResponse(
                        choices=[
                            _MockResponse(
                                delta=_MockResponse(tool_calls=[tc1]),
                                finish_reason=None,
                            ),
                        ],
                    ),
                    _MockResponse(
                        choices=[
                            _MockResponse(
                                delta=_MockResponse(tool_calls=[tc2]),
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
        tool_chunk = [c for c in chunks if c.tool_calls][0]
        assert tool_chunk.tool_calls[0].name == "read_file"
        assert tool_chunk.tool_calls[0].arguments == {"path": "/tmp/a"}

    @staticmethod
    async def _async_iter(items):
        for item in items:
            yield item


# ===== Anthropic provider =====


class TestAnthropicProvider:
    @pytest.fixture
    def make_provider(self):
        def _make():
            fake_anthropic = types.ModuleType("anthropic")
            fake_anthropic.APIError = Exception
            fake_async = MagicMock()
            fake_anthropic.AsyncAnthropic = fake_async
            with patch(
                "dashscope_agenticx.providers.anthropic.anthropic",
                fake_anthropic,
            ):
                from dashscope_agenticx.providers.anthropic import (
                    AnthropicProvider,
                )

                provider = object.__new__(AnthropicProvider)
                provider.model = "claude-test"
                provider.client = fake_async.return_value
                return provider, fake_async

        return _make

    async def test_chat_text(self, make_provider):
        provider, _ = make_provider()
        provider.client.messages.create = AsyncMock(
            return_value=_MockResponse(
                content=[_MockResponse(type="text", text="hello")],
            ),
        )
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        resp = await AnthropicProvider.chat(
            provider,
            [{"role": "user", "content": "hi"}],
        )
        assert resp.content == "hello"

    async def test_chat_tool_use(self, make_provider):
        provider, _ = make_provider()
        provider.client.messages.create = AsyncMock(
            return_value=_MockResponse(
                content=[
                    _MockResponse(
                        type="tool_use",
                        id="tu_1",
                        name="read_file",
                        input={"path": "/tmp/a"},
                    ),
                ],
            ),
        )
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        resp = await AnthropicProvider.chat(
            provider,
            [{"role": "user", "content": "hi"}],
        )
        assert resp.tool_calls[0].name == "read_file"
        assert resp.tool_calls[0].arguments == {"path": "/tmp/a"}

    async def test_chat_timeout(self, make_provider):
        provider, _ = make_provider()
        provider.client.messages.create = AsyncMock(
            side_effect=httpx.TimeoutException("timeout"),
        )
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        with pytest.raises(RuntimeError, match="API request timeout"):
            await AnthropicProvider.chat(
                provider,
                [{"role": "user", "content": "hi"}],
            )

    async def test_chat_connect_error(self, make_provider):
        provider, _ = make_provider()
        provider.client.messages.create = AsyncMock(
            side_effect=httpx.ConnectError("connect"),
        )
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        with pytest.raises(RuntimeError, match="Unable to connect"):
            await AnthropicProvider.chat(
                provider,
                [{"role": "user", "content": "hi"}],
            )

    async def test_chat_stream_text(self, make_provider):
        provider, _ = make_provider()
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(
            return_value=self._stream_events(
                [
                    {
                        "type": "content_block_delta",
                        "delta": _MockResponse(type="text_delta", text="hi"),
                    },
                    {"type": "message_stop"},
                ],
            ),
        )
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        provider.client.messages.stream = MagicMock(return_value=stream_cm)
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        chunks = [
            c
            async for c in AnthropicProvider.chat_stream(
                provider,
                [{"role": "user", "content": "x"}],
            )
        ]
        assert [c.delta_content for c in chunks if c.delta_content] == ["hi"]

    async def test_chat_usage_extracted(self, make_provider):
        provider, _ = make_provider()
        provider.client.messages.create = AsyncMock(
            return_value=_MockResponse(
                content=[_MockResponse(type="text", text="hello")],
                usage=_MockResponse(input_tokens=12, output_tokens=8),
            ),
        )
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        resp = await AnthropicProvider.chat(
            provider,
            [{"role": "user", "content": "hi"}],
        )
        assert resp.usage == {
            "input_tokens": 12,
            "output_tokens": 8,
            "total_tokens": 20,
            "cached_tokens": 0,
        }

    async def test_chat_stream_usage_on_final_chunk(self, make_provider):
        provider, _ = make_provider()
        stream_cm = MagicMock()
        stream_cm.__aenter__ = AsyncMock(
            return_value=self._stream_events(
                [
                    {
                        "type": "message_start",
                        "message": _MockResponse(
                            usage=_MockResponse(
                                input_tokens=9,
                                output_tokens=1,
                            ),
                        ),
                    },
                    {
                        "type": "content_block_delta",
                        "delta": _MockResponse(type="text_delta", text="hi"),
                    },
                    {
                        "type": "message_delta",
                        "usage": _MockResponse(output_tokens=4),
                    },
                    {"type": "message_stop"},
                ],
            ),
        )
        stream_cm.__aexit__ = AsyncMock(return_value=False)
        provider.client.messages.stream = MagicMock(return_value=stream_cm)
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        chunks = [
            c
            async for c in AnthropicProvider.chat_stream(
                provider,
                [{"role": "user", "content": "x"}],
            )
        ]
        final = [c for c in chunks if c.finish_reason][-1]
        assert final.usage == {
            "input_tokens": 9,
            "output_tokens": 4,
            "total_tokens": 13,
            "cached_tokens": 0,
        }

    @staticmethod
    async def _stream_events(events):
        for ev in events:
            yield _MockResponse(**ev)


class TestAnthropicImageConversion:
    def test_image_url_block_translated(self):
        from dashscope_agenticx.providers.anthropic import (
            _convert_content_block,
        )

        block = {
            "type": "image_url",
            "image_url": {"url": "data:image/png;base64,abc123"},
        }
        out = _convert_content_block(block)
        assert out["type"] == "image"
        assert out["source"]["type"] == "base64"
        assert out["source"]["media_type"] == "image/png"
        assert out["source"]["data"] == "abc123"

    def test_text_block_passes_through(self):
        from dashscope_agenticx.providers.anthropic import (
            _convert_content_block,
        )

        block = {"type": "text", "text": "hello"}
        assert _convert_content_block(block) == block

    def test_non_data_url_passes_through(self):
        from dashscope_agenticx.providers.anthropic import (
            _convert_content_block,
        )

        block = {"type": "image_url", "image_url": {"url": "https://x/y.png"}}
        out = _convert_content_block(block)
        # Non-data URLs are not translated — pass through unchanged
        assert out == block

    def test_malformed_data_url_passes_through(self):
        from dashscope_agenticx.providers.anthropic import (
            _convert_content_block,
        )

        block = {"type": "image_url", "image_url": {"url": "data:bad-format"}}
        out = _convert_content_block(block)
        assert out == block

    def test_convert_messages_translates_image_url_in_user_msg(
        self,
    ):
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        provider = object.__new__(AnthropicProvider)
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "describe"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/jpeg;base64,xyz"},
                    },
                ],
            },
        ]
        system, converted = provider._convert_messages(messages)
        assert system == ""
        assert len(converted) == 1
        blocks = converted[0]["content"]
        assert blocks[0] == {"type": "text", "text": "describe"}
        assert blocks[1]["type"] == "image"
        assert blocks[1]["source"]["media_type"] == "image/jpeg"
        assert blocks[1]["source"]["data"] == "xyz"


# ===== Anthropic tool_result merging (P3) =====


class TestAnthropicToolResultMerge:
    @staticmethod
    def _provider():
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        return object.__new__(AnthropicProvider)

    @staticmethod
    def _assistant_msg(call_ids):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {
                    "id": cid,
                    "function": {"name": "read_file", "arguments": "{}"},
                }
                for cid in call_ids
            ],
        }

    def test_consecutive_tool_messages_merge(self):
        provider = self._provider()
        messages = [
            {"role": "user", "content": "hi"},
            self._assistant_msg(["call_1", "call_2"]),
            {"role": "tool", "content": "aaa", "tool_use_id": "call_1"},
            {"role": "tool", "content": "bbb", "tool_use_id": "call_2"},
        ]
        _, converted = provider._convert_messages(messages)
        # Parallel tool results merge into ONE user message so
        # user/assistant alternation is preserved.
        assert [m["role"] for m in converted] == ["user", "assistant", "user"]
        blocks = converted[2]["content"]
        assert [b["type"] for b in blocks] == ["tool_result", "tool_result"]
        assert [b["tool_use_id"] for b in blocks] == ["call_1", "call_2"]
        assert [b["content"] for b in blocks] == ["aaa", "bbb"]
        # tool_use ids match the tool_result ids.
        use_ids = [
            b["id"] for b in converted[1]["content"] if b["type"] == "tool_use"
        ]
        assert use_ids == ["call_1", "call_2"]

    def test_lone_tool_message(self):
        provider = self._provider()
        messages = [
            {"role": "user", "content": "hi"},
            self._assistant_msg(["call_1"]),
            {"role": "tool", "content": "done", "tool_use_id": "call_1"},
            {"role": "assistant", "content": "ok"},
        ]
        _, converted = provider._convert_messages(messages)
        assert [m["role"] for m in converted] == [
            "user",
            "assistant",
            "user",
            "assistant",
        ]
        assert converted[2]["content"] == [
            {
                "type": "tool_result",
                "tool_use_id": "call_1",
                "content": "done",
            },
        ]

    def test_tool_groups_separated_by_assistant_stay_separate(self):
        provider = self._provider()
        messages = [
            {"role": "user", "content": "hi"},
            self._assistant_msg(["call_1"]),
            {"role": "tool", "content": "one", "tool_use_id": "call_1"},
            {"role": "assistant", "content": "mid"},
            self._assistant_msg(["call_2", "call_3"]),
            {"role": "tool", "content": "two", "tool_use_id": "call_2"},
            {"role": "tool", "content": "three", "tool_use_id": "call_3"},
        ]
        _, converted = provider._convert_messages(messages)
        assert [m["role"] for m in converted] == [
            "user",
            "assistant",
            "user",
            "assistant",
            "assistant",
            "user",
        ]
        assert len(converted[2]["content"]) == 1
        assert len(converted[5]["content"]) == 2

    def test_trailing_user_text_joins_the_tool_result_turn(self):
        # The agent loop puts its volatile reflection hint at the tail of the
        # request rather than rewriting the system message, so a user message
        # lands directly after tool results. Two consecutive user messages
        # would break alternation; the API allows tool_result and text to
        # share one user turn as long as the tool_result blocks come first.
        provider = self._provider()
        messages = [
            {"role": "user", "content": "hi"},
            self._assistant_msg(["call_1", "call_2"]),
            {"role": "tool", "content": "aaa", "tool_use_id": "call_1"},
            {"role": "tool", "content": "bbb", "tool_use_id": "call_2"},
            {"role": "user", "content": "## Reflection hint"},
        ]
        _, converted = provider._convert_messages(messages)
        assert [m["role"] for m in converted] == ["user", "assistant", "user"]
        blocks = converted[2]["content"]
        assert [b["type"] for b in blocks] == [
            "tool_result",
            "tool_result",
            "text",
        ]
        assert blocks[-1]["text"] == "## Reflection hint"
        assert [b["tool_use_id"] for b in blocks[:2]] == ["call_1", "call_2"]

    def test_empty_trailing_user_message_does_not_add_text_block(self):
        # An empty text block is rejected, so fall back to flushing the tool
        # results on their own.
        provider = self._provider()
        messages = [
            {"role": "user", "content": "hi"},
            self._assistant_msg(["call_1"]),
            {"role": "tool", "content": "aaa", "tool_use_id": "call_1"},
            {"role": "user", "content": ""},
        ]
        _, converted = provider._convert_messages(messages)
        assert [m["role"] for m in converted] == [
            "user",
            "assistant",
            "user",
            "user",
        ]
        assert [b["type"] for b in converted[2]["content"]] == ["tool_result"]
        assert converted[3]["content"] == ""

    def test_trailing_user_block_list_joins_the_tool_result_turn(self):
        provider = self._provider()
        messages = [
            {"role": "user", "content": "hi"},
            self._assistant_msg(["call_1"]),
            {"role": "tool", "content": "aaa", "tool_use_id": "call_1"},
            {
                "role": "user",
                "content": [{"type": "text", "text": "hint"}],
            },
        ]
        _, converted = provider._convert_messages(messages)
        assert [m["role"] for m in converted] == ["user", "assistant", "user"]
        blocks = converted[2]["content"]
        assert [b["type"] for b in blocks] == ["tool_result", "text"]
        assert blocks[-1]["text"] == "hint"


# ===== Tongyi provider =====


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


class TestTongyiProvider:
    async def test_chat_text_and_usage(self, monkeypatch):
        def handler(request: httpx.Request):
            assert b"qwen3.8-flash" in request.content
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "你好",
                                },
                                "finish_reason": "stop",
                            },
                        ],
                    },
                    "usage": {
                        "input_tokens": 3,
                        "output_tokens": 2,
                        "total_tokens": 5,
                    },
                    "request_id": "r1",
                },
            )

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        resp = await provider.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "你好"
        assert resp.usage == {
            "input_tokens": 3,
            "output_tokens": 2,
            "total_tokens": 5,
            "cached_tokens": 0,
        }

    async def test_chat_tool_calls(self, monkeypatch):
        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
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
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            },
                        ],
                    },
                    "request_id": "r1",
                },
            )

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        resp = await provider.chat([{"role": "user", "content": "hi"}])
        assert resp.tool_calls[0].name == "read_file"

    async def test_chat_error_status(self, monkeypatch):
        def handler(request: httpx.Request):
            return httpx.Response(400, text="bad request")

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        with pytest.raises(RuntimeError, match="DashScope API error"):
            await provider.chat([{"role": "user", "content": "hi"}])

    async def test_chat_timeout(self, monkeypatch):
        def handler(request: httpx.Request):
            raise httpx.TimeoutException("timeout")

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test", request_timeout=1)
        with pytest.raises(RuntimeError, match="API request timed out"):
            await provider.chat([{"role": "user", "content": "hi"}])

    async def test_chat_connect_error(self, monkeypatch):
        def handler(request: httpx.Request):
            raise httpx.ConnectError("connect")

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        with pytest.raises(RuntimeError, match="Cannot connect"):
            await provider.chat([{"role": "user", "content": "hi"}])

    async def test_chat_stream_text(self, monkeypatch):
        lines = [
            'data:{"output": {"choices": [{"finish_reason": "null", '
            '"message": {"role": "assistant", "content": "hello"}}]}, '
            '"request_id": "r1"}',
            'data:{"output": {"choices": [{"finish_reason": "stop", '
            '"message": {"role": "assistant", "content": " world"}}]}, '
            '"usage": {"input_tokens": 3, "output_tokens": 2, '
            '"total_tokens": 5}, "request_id": "r1"}',
        ]

        def handler(request: httpx.Request):
            return httpx.Response(200, content="\n".join(lines).encode())

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        assert [c.delta_content for c in chunks if c.delta_content] == [
            "hello",
            " world",
        ]

    async def test_chat_stream_usage_tail_chunk(self, monkeypatch):
        # usage arrives in a terminal chunk (empty choices) AFTER finish
        lines = [
            'data:{"output": {"choices": [{"finish_reason": "null", '
            '"message": {"role": "assistant", "content": "hi"}}]}, '
            '"request_id": "r1"}',
            'data:{"output": {"choices": [{"finish_reason": "stop", '
            '"message": {"role": "assistant", "content": ""}}]}, '
            '"request_id": "r1"}',
            'data:{"output": {"choices": []}, "usage": {"input_tokens": '
            '10, "output_tokens": 4, "total_tokens": 14, '
            '"prompt_tokens_details": {"cached_tokens": 6}}, '
            '"request_id": "r1"}',
        ]

        def handler(request: httpx.Request):
            return httpx.Response(200, content="\n".join(lines).encode())

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        usages = [c.usage for c in chunks if c.usage]
        assert usages == [
            {
                "input_tokens": 10,
                "output_tokens": 4,
                "total_tokens": 14,
                "cached_tokens": 6,
            },
        ]
        assert [c.finish_reason for c in chunks if c.finish_reason] == ["stop"]

    async def test_chat_stream_usage_on_finish_chunk_not_duplicated(
        self,
        monkeypatch,
    ):
        # Server attaches usage directly to the finish chunk: no trailing re-emit
        lines = [
            'data:{"output": {"choices": [{"finish_reason": "stop", '
            '"message": {"role": "assistant", "content": "hi"}}]}, '
            '"usage": {"input_tokens": 5, "output_tokens": 2, '
            '"total_tokens": 7}, "request_id": "r1"}',
        ]

        def handler(request: httpx.Request):
            return httpx.Response(200, content="\n".join(lines).encode())

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
            )
        ]
        usages = [c.usage for c in chunks if c.usage]
        assert usages == [
            {
                "input_tokens": 5,
                "output_tokens": 2,
                "total_tokens": 7,
                "cached_tokens": 0,
            },
        ]

    async def test_anthropic_protocol_chat(self, monkeypatch):
        def handler(request: httpx.Request):
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "ok",
                                },
                                "finish_reason": "stop",
                            },
                        ],
                    },
                    "request_id": "r1",
                },
            )

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test", protocol="anthropic")
        resp = await provider.chat(
            [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "hi"},
            ],
        )
        assert resp.content == "ok"

    async def test_anthropic_protocol_tool_roundtrip(self, monkeypatch):
        captured = {}

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
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
                                    ],
                                },
                                "finish_reason": "tool_calls",
                            },
                        ],
                    },
                    "request_id": "r1",
                },
            )

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test", protocol="anthropic")
        resp = await provider.chat(
            [
                {"role": "user", "content": "hi"},
                {
                    "role": "assistant",
                    "content": "",
                    "tool_calls": [
                        {
                            "id": "call_0",
                            "function": {
                                "name": "read_file",
                                "arguments": "{}",
                            },
                        },
                    ],
                },
                {
                    "role": "tool",
                    "content": "prev result",
                    "tool_use_id": "call_0",
                    "tool_call_id": "call_0",
                },
                {"role": "user", "content": "and?"},
            ],
        )
        # Response side: tool_use blocks round-trip back to ToolCall entries.
        assert resp.tool_calls[0].name == "read_file"
        assert resp.tool_calls[0].arguments == {"path": "/tmp/a"}
        # Request side: tool structures keep their ids for the backend.
        sent = captured["body"]["input"]["messages"]
        roles = [m["role"] for m in sent]
        assert roles == ["user", "assistant", "tool", "user"]
        assert sent[1]["tool_calls"][0]["id"] == "call_0"
        assert sent[2]["tool_call_id"] == "call_0"

    async def test_custom_base_url(self, monkeypatch):
        custom_url = (
            "https://llm-4075v54akjlm9vxo.cn-beijing.maas.aliyuncs.com"
        )

        def handler(request: httpx.Request):
            # First probe goes to multimodal-generation; the order is pinned
            # by test_tongyi_native.test_multimodal_path_is_probed_first.
            assert str(request.url) == (
                custom_url
                + "/api/v1/services/aigc/multimodal-generation/generation"
            )
            return httpx.Response(
                200,
                json={
                    "output": {
                        "choices": [
                            {
                                "message": {
                                    "role": "assistant",
                                    "content": "ok",
                                },
                                "finish_reason": "stop",
                            },
                        ],
                    },
                    "request_id": "r1",
                },
            )

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test", base_url=custom_url)
        resp = await provider.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "ok"


# ===== Provider factory / chain =====


class TestProviderFactory:
    def test_provider_api_key_envs(self):
        assert PROVIDER_API_KEY_ENVS["anthropic"] == "ANTHROPIC_API_KEY"
        assert PROVIDER_API_KEY_ENVS["openai"] == "OPENAI_API_KEY"
        assert PROVIDER_API_KEY_ENVS["tongyi"] == "DASHSCOPE_API_KEY"

    def test_create_provider_tongyi_dashscope(self):
        provider = _create_provider(
            ProviderProfile(
                name="tongyi",
                provider="tongyi",
                model="qwen",
                api_key="k",
                protocol="dashscope",
            ),
        )
        assert isinstance(provider, TongyiProvider)

    def test_create_provider_tongyi_base_url(self):
        custom_url = "https://example.com/compatible-mode/v1"
        provider = _create_provider(
            ProviderProfile(
                name="tongyi",
                provider="tongyi",
                model="qwen",
                api_key="k",
                base_url=custom_url,
                protocol="dashscope",
            ),
        )
        assert isinstance(provider, TongyiProvider)
        # legacy compatible-mode suffix normalizes to the service root
        assert provider.base_url == "https://example.com"

    def test_create_provider_anthropic_base_url(self):
        custom_url = "https://example.com/apps/anthropic"
        with patch(
            "dashscope_agenticx.providers.anthropic.anthropic",
        ) as fake_anthropic:
            fake_anthropic.APIError = Exception
            fake_async = MagicMock()
            fake_anthropic.AsyncAnthropic = fake_async
            _create_provider(
                ProviderProfile(
                    name="anthropic",
                    provider="anthropic",
                    model="claude-test",
                    api_key="k",
                    base_url=custom_url,
                    protocol="anthropic",
                ),
            )
            assert fake_async.call_args.kwargs["base_url"] == custom_url

    def test_create_provider_openai_default(self):
        with patch("dashscope_agenticx.providers.openai.AsyncOpenAI"):
            provider = _create_provider(
                ProviderProfile(
                    name="deepseek",
                    provider="deepseek",
                    model="deepseek-v4-pro",
                    api_key="k",
                    base_url="https://api.deepseek.com",
                    protocol="openai",
                ),
            )
            assert isinstance(provider, OpenAIProvider)

    def test_create_provider_ollama_no_auth(self, monkeypatch):
        from dashscope_agenticx.extensions import (
            CustomExtensions,
            CustomProvider,
        )

        ext = CustomExtensions(
            providers=[
                CustomProvider(
                    name="ollama",
                    base_url="http://localhost:11434/v1",
                    default_model="llama3",
                    protocol="openai",
                    auth=False,
                ),
            ],
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: ext,
        )
        with patch(
            "dashscope_agenticx.providers.openai.AsyncOpenAI",
        ) as MockClient:
            provider = _create_provider(
                ProviderProfile(
                    name="ollama",
                    provider="ollama",
                    model="llama3",
                    api_key="",
                    protocol="openai",
                ),
            )
            assert isinstance(provider, OpenAIProvider)
            assert (
                MockClient.call_args.kwargs["base_url"]
                == "http://localhost:11434/v1"
            )
            assert MockClient.call_args.kwargs["api_key"] == "ollama"

    def test_get_provider(self):
        with patch("dashscope_agenticx.providers.openai.AsyncOpenAI"):
            provider = get_provider("openai", "gpt-4o", "k")
            assert provider.model == "gpt-4o"

    def test_get_provider_chain(self, monkeypatch):
        config = types.SimpleNamespace(
            provider="tongyi",
            model="qwen-test",
            api_key="k",
            base_url=None,
            fallback_providers=[],
            timeout=30,
            protocol="openai",
            anthropic_api_key="",
            openai_api_key="",
            deepseek_api_key="",
            zhipu_api_key="",
        )
        chain = get_provider_chain(config)
        assert len(chain.profiles) == 1
        assert chain.profiles[0].provider == "tongyi"

    def test_get_provider_chain_has_no_default_gateway_warning(
        self,
        monkeypatch,
        capsys,
    ):
        from dashscope_agenticx.config import Config
        from dashscope_agenticx.extensions import CustomExtensions

        monkeypatch.delenv("AGENTICX_DISABLE_GATEWAY_WARNING", raising=False)
        monkeypatch.setattr(
            "dashscope_agenticx.providers.profile._LEGACY_GATEWAY_WARNED",
            set(),
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions._current",
            CustomExtensions(),
        )
        chain = get_provider_chain(Config(tongyi_api_key="sk-test"))

        assert chain.profiles[0].base_url == "https://dashscope.aliyuncs.com"
        output = capsys.readouterr().out
        assert "being phased out" not in output
        assert "AGENTICX_DISABLE_GATEWAY_WARNING" not in output


class TestExtensionProviders:
    @staticmethod
    def _make_ext_provider(protocol="openai"):
        from dashscope_agenticx.extensions import (
            CustomExtensions,
            CustomProvider,
        )

        return CustomExtensions(
            providers=[
                CustomProvider(
                    name="bailian-anthropic",
                    base_url="https://example.com/apps/anthropic",
                    api_key_env="DASHSCOPE_API_KEY",
                    default_model="qwen3.7-max",
                    models=["qwen3.5-plus"],
                    protocol=protocol,
                ),
            ],
        )

    def test_create_extension_provider_anthropic(self, monkeypatch):
        ext = self._make_ext_provider("anthropic").providers[0]
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: ext if name == "bailian-anthropic" else None,
        )
        with patch(
            "dashscope_agenticx.providers.anthropic.anthropic",
        ) as fake_anthropic:
            fake_anthropic.APIError = Exception
            fake_async = MagicMock()
            fake_anthropic.AsyncAnthropic = fake_async
            # protocol="" — no explicit choice, so the toml-declared
            # protocol (anthropic) is used as fallback.
            provider = _create_provider(
                ProviderProfile(
                    name="bailian-anthropic",
                    provider="bailian-anthropic",
                    model="qwen3.7-max",
                    api_key="k",
                    protocol="",
                ),
            )
            assert isinstance(provider, AnthropicProvider)
            assert (
                fake_async.call_args.kwargs["base_url"]
                == "https://example.com/apps/anthropic"
            )

    def test_create_extension_provider_openai(self, monkeypatch):
        ext = self._make_ext_provider("openai").providers[0]
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: ext if name == "bailian-anthropic" else None,
        )
        with patch(
            "dashscope_agenticx.providers.openai.AsyncOpenAI",
        ) as MockClient:
            provider = _create_provider(
                ProviderProfile(
                    name="bailian-anthropic",
                    provider="bailian-anthropic",
                    model="qwen3.7-max",
                    api_key="k",
                ),
            )
            assert isinstance(provider, OpenAIProvider)
            assert (
                MockClient.call_args.kwargs["base_url"]
                == "https://example.com/apps/anthropic"
            )

    def test_build_profiles_extension_protocol_fallback(self, monkeypatch):
        # Explicit config.protocol wins; toml protocol only fills in
        # when config has no protocol set.
        ext = self._make_ext_provider("anthropic").providers[0]
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: ext if name == "bailian-anthropic" else None,
        )
        config = types.SimpleNamespace(
            provider="bailian-anthropic",
            model="qwen3.7-max",
            api_key="",
            base_url=None,
            fallback_providers=[],
            timeout=30,
            protocol="",
            anthropic_api_key="",
            openai_api_key="",
            deepseek_api_key="",
            zhipu_api_key="",
        )
        profiles = build_profiles_from_config(config)
        assert len(profiles) == 1
        assert profiles[0].provider == "bailian-anthropic"
        assert profiles[0].protocol == "anthropic"

        config.protocol = "openai"
        profiles = build_profiles_from_config(config)
        assert profiles[0].protocol == "openai"


# ===== Provider profile helpers =====


class TestProviderProfileHelpers:
    def test_host_of(self):
        assert _host_of("https://api.openai.com/v1") == "api.openai.com"
        assert _host_of("localhost:11434") == "localhost:11434"
        assert _host_of("") == ""

    def test_detect_provider_from_host(self):
        assert _detect_provider_from_host("https://api.openai.com") == "openai"
        assert _detect_provider_from_host("https://unknown.com") is None

    def test_validate_key_empty(self):
        profile = ProviderProfile(
            name="p",
            provider="openai",
            model="m",
            api_key="",
        )
        warnings = validate_key_for_profile(profile)
        assert any("API key is empty" in w for w in warnings)

    def test_validate_key_prefix(self):
        profile = ProviderProfile(
            name="p",
            provider="openai",
            model="m",
            api_key="bad",
        )
        warnings = validate_key_for_profile(profile)
        assert any("does not start" in w for w in warnings)

    def test_validate_key_host_mismatch(self):
        profile = ProviderProfile(
            name="p",
            provider="anthropic",
            model="m",
            api_key="sk-test",
            base_url="https://api.openai.com",
        )
        warnings = validate_key_for_profile(profile)
        assert any("looks like 'openai'" in w for w in warnings)

    def test_is_retryable_error(self):
        assert _is_retryable_error(RuntimeError("timeout")) is True
        assert _is_retryable_error(RuntimeError("invalid api key")) is False
        assert _is_retryable_error(RuntimeError("random")) is False

    def test_is_retryable_error_chinese_retryable(self):
        # The messages providers actually raise — all transient.
        assert (
            _is_retryable_error(
                RuntimeError("API 请求超时，请检查网络连接或稍后重试"),
            )
            is True
        )
        assert (
            _is_retryable_error(
                RuntimeError("无法连接到 API 服务器，请检查网络连接"),
            )
            is True
        )
        assert _is_retryable_error(RuntimeError("请求被限流，请稍后重试")) is True
        assert (
            _is_retryable_error(
                RuntimeError(
                    "API 流式响应未返回任何内容，可能是限流、模型不可用或服务异常",
                ),
            )
            is True
        )
        assert _is_retryable_error(RuntimeError("API 请求失败: 服务内部错误")) is True

    def test_is_retryable_error_chinese_non_retryable(self):
        assert _is_retryable_error(RuntimeError("没有权限访问该模型")) is False
        assert (
            _is_retryable_error(
                RuntimeError(
                    "API 端点不存在 (404): base_url 与 protocol 可能不匹配，"
                    "请运行 /provider 检查配置",
                ),
            )
            is False
        )
        assert _is_retryable_error(RuntimeError("无效 api key，请检查密钥配置")) is False
        assert _is_retryable_error(RuntimeError("认证失败，请重新登录")) is False
        assert _is_retryable_error(RuntimeError("配额已用尽，请检查账单")) is False

    def test_hardening_shares_classifier(self):
        # ProviderChain (profile.py) and HardenedProvider must use the SAME
        # bilingual classifier, otherwise retry and fallback layers disagree.
        from dashscope_agenticx.providers import hardening

        assert hardening.is_retryable_error is _is_retryable_error
        assert (
            hardening.is_retryable_error(
                RuntimeError("API 请求超时，请检查网络连接"),
            )
            is True
        )
        assert (
            hardening.is_retryable_error(RuntimeError("API 端点不存在 (404)"))
            is False
        )

    def test_gateway_account_block_is_not_retryable(self):
        # The exact 403 the DashScope gateway raises on an overdue account.
        # Permanent until the user fixes billing, so neither the retry layer
        # nor the fallback chain may spend a budget on it.
        error = RuntimeError(
            "DashScope API error: 403 - Access denied, please make sure "
            "your account is in good standing. For details, see: "
            "https://help.aliyun.com/zh/model-studio/error-code"
            "#overdue-payment",
        )
        assert _is_retryable_error(error) is False
        assert _is_retryable_error(RuntimeError("账户已欠费，请充值后重试")) is False

    def test_account_error_hint(self):
        from dashscope_agenticx.providers.hardening import account_error_hint

        assert "account/billing" in account_error_hint(
            RuntimeError(
                "DashScope API error: 403 - Access denied, please make sure "
                "your account is in good standing ... #overdue-payment",
            ),
        )
        assert account_error_hint(RuntimeError("账户已欠费，请充值"))
        # Non-retryable is not the same as an account error: a bad key gets
        # no billing hint, and neither does a transient timeout.
        assert account_error_hint(RuntimeError("invalid api key")) == ""
        assert account_error_hint(RuntimeError("timeout")) == ""

    def test_fallback_does_not_inherit_primary_protocol(self, monkeypatch):
        # P2: an openai fallback under an anthropic primary must keep its
        # own protocol, otherwise it is built as the wrong client class.
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: None,
        )
        config = types.SimpleNamespace(
            provider="anthropic",
            model="claude-test",
            api_key="",
            base_url=None,
            fallback_providers=["openai"],
            timeout=60,
            protocol="anthropic",
            anthropic_api_key="ak",
            openai_api_key="ok",
            deepseek_api_key="",
            zhipu_api_key="",
        )
        profiles = build_profiles_from_config(config)
        # Primary still honors the explicit config.protocol.
        assert profiles[0].provider == "anthropic"
        assert profiles[0].protocol == "anthropic"
        fallback = next(p for p in profiles if p.provider == "openai")
        assert fallback.protocol == "openai"

    def test_fallback_extension_uses_toml_protocol(self, monkeypatch):
        # P2: an extension fallback uses its toml-declared protocol even
        # when the primary's config.protocol differs.
        from dashscope_agenticx.extensions import (
            CustomExtensions,
            CustomProvider,
        )

        ext = CustomExtensions(
            providers=[
                CustomProvider(
                    name="bailian-anthropic",
                    base_url="https://example.com/apps/anthropic",
                    default_model="qwen3.7-max",
                    protocol="anthropic",
                ),
            ],
        ).providers[0]
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: ext if name == "bailian-anthropic" else None,
        )
        config = types.SimpleNamespace(
            provider="tongyi",
            model="qwen",
            api_key="k",
            base_url=None,
            fallback_providers=["bailian-anthropic"],
            timeout=60,
            protocol="openai",
            anthropic_api_key="",
            openai_api_key="",
            deepseek_api_key="",
            zhipu_api_key="",
        )
        profiles = build_profiles_from_config(config)
        assert profiles[0].protocol == "openai"
        fallback = next(
            p for p in profiles if p.provider == "bailian-anthropic"
        )
        assert fallback.protocol == "anthropic"

    def test_infer_fallback_names(self, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "ak")
        config = types.SimpleNamespace(
            provider="tongyi",
            anthropic_api_key="",
            openai_api_key="",
            deepseek_api_key="",
            zhipu_api_key="",
        )
        assert "anthropic" in _infer_fallback_names(config)

    def test_build_profiles_from_config(self):
        config = types.SimpleNamespace(
            provider="tongyi",
            model="qwen",
            api_key="k",
            base_url=None,
            fallback_providers=["anthropic"],
            timeout=60,
            protocol="openai",
            anthropic_api_key="ak",
            openai_api_key="",
            deepseek_api_key="",
            zhipu_api_key="",
        )
        profiles = build_profiles_from_config(config)
        assert profiles[0].provider == "tongyi"
        assert any(p.provider == "anthropic" for p in profiles)


class _ChatProvider:
    def __init__(self, chat_responses=None, stream_responses=None):
        from collections import deque

        self._chat_q = deque(chat_responses or [])
        self._stream_q = deque(stream_responses or [])
        self.chat_calls = 0

    async def chat(self, messages, tools=None, response_format=None):
        if not self._chat_q:
            raise IndexError("no more chat responses")
        value = self._chat_q.popleft()
        self.chat_calls += 1
        if isinstance(value, Exception):
            raise value
        return value

    async def chat_stream(self, messages, tools=None, response_format=None):
        if not self._stream_q:
            raise IndexError("no more stream responses")
        value = self._stream_q.popleft()
        if isinstance(value, Exception):
            raise value
        for chunk in value:
            yield chunk


class TestProviderChain:
    async def test_chat_success(self):
        fake = _ChatProvider(chat_responses=[LLMResponse(content="ok")])
        profile = ProviderProfile(
            name="p",
            provider="tongyi",
            model="m",
            api_key="k",
        )
        chain = ProviderChain([profile])
        chain._instances[0] = fake
        resp = await chain.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "ok"

    async def test_chat_fallback(self):
        bad = _ChatProvider(chat_responses=[RuntimeError("timeout")])
        ok = _ChatProvider(chat_responses=[LLMResponse(content="fallback")])
        profiles = [
            ProviderProfile(
                name="bad",
                provider="tongyi",
                model="m",
                api_key="k",
                max_retries=0,
            ),
            ProviderProfile(
                name="ok",
                provider="tongyi",
                model="m",
                api_key="k",
                max_retries=0,
            ),
        ]
        chain = ProviderChain(profiles)
        chain._instances[0] = HardenedProvider(bad, max_retries=0)
        chain._instances[1] = ok
        resp = await chain.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "fallback"

    async def test_chat_non_retryable_stops(self):
        bad = _ChatProvider(chat_responses=[RuntimeError("invalid api key")])
        profile = ProviderProfile(
            name="bad",
            provider="tongyi",
            model="m",
            api_key="k",
            max_retries=0,
        )
        chain = ProviderChain([profile])
        chain._instances[0] = HardenedProvider(bad, max_retries=0)
        with pytest.raises(RuntimeError, match="invalid api key"):
            await chain.chat([{"role": "user", "content": "hi"}])

    async def test_chat_stream_fallback(self):
        bad = _ChatProvider(stream_responses=[RuntimeError("timeout")])
        ok = _ChatProvider(stream_responses=[[LLMChunk(delta_content="hi")]])
        profiles = [
            ProviderProfile(
                name="bad",
                provider="tongyi",
                model="m",
                api_key="k",
                max_retries=0,
            ),
            ProviderProfile(
                name="ok",
                provider="tongyi",
                model="m",
                api_key="k",
                max_retries=0,
            ),
        ]
        chain = ProviderChain(profiles)
        chain._instances[0] = HardenedProvider(bad, max_retries=0)
        chain._instances[1] = ok
        chunks = [
            c
            async for c in chain.chat_stream(
                [{"role": "user", "content": "x"}],
            )
        ]
        assert [c.delta_content for c in chunks] == ["hi"]


# ===== Hardened provider =====


class TestHardenedProvider:
    async def test_retry_then_success(self):
        inner = _ChatProvider(
            chat_responses=[
                RuntimeError("timeout"),
                LLMResponse(content="ok"),
            ],
        )
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        resp = await hardened.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "ok"
        assert inner.chat_calls == 2

    async def test_retry_chinese_timeout(self):
        # The exact RuntimeError providers raise on httpx.TimeoutException.
        inner = _ChatProvider(
            chat_responses=[
                RuntimeError("API 请求超时，请检查网络连接或稍后重试"),
                LLMResponse(content="ok"),
            ],
        )
        hardened = HardenedProvider(inner, max_retries=1, retry_delay=0.01)
        resp = await hardened.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "ok"
        assert inner.chat_calls == 2

    async def test_no_retry_chinese_404(self):
        # 端点不存在 (404) is non-retryable — fail fast, let the chain fall back.
        inner = _ChatProvider(
            chat_responses=[
                RuntimeError(
                    "API 端点不存在 (404): base_url 与 protocol 可能不匹配",
                ),
            ],
        )
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        with pytest.raises(RuntimeError, match="不存在"):
            await hardened.chat([{"role": "user", "content": "hi"}])
        assert inner.chat_calls == 1

    async def test_empty_response_recovery(self):
        inner = _ChatProvider(
            chat_responses=[
                LLMResponse(content=""),
                LLMResponse(content="recovered"),
            ],
        )
        hardened = HardenedProvider(inner, max_retries=1, retry_delay=0.01)
        resp = await hardened.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "recovered"

    async def test_non_retryable_raises(self):
        inner = _ChatProvider(chat_responses=[RuntimeError("invalid api key")])
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        with pytest.raises(RuntimeError, match="invalid api key"):
            await hardened.chat([{"role": "user", "content": "hi"}])

    async def test_stream_no_chunks_recovery(self):
        inner = _ChatProvider(
            stream_responses=[[], [LLMChunk(delta_content="hi")]],
        )
        hardened = HardenedProvider(inner, max_retries=1, retry_delay=0.01)
        chunks = [
            c
            async for c in hardened.chat_stream(
                [{"role": "user", "content": "x"}],
            )
        ]
        assert [c.delta_content for c in chunks] == ["hi"]

    async def test_stream_retryable_error(self):
        inner = _ChatProvider(
            stream_responses=[
                RuntimeError("timeout"),
                [LLMChunk(delta_content="hi")],
            ],
        )
        hardened = HardenedProvider(inner, max_retries=1, retry_delay=0.01)
        chunks = [
            c
            async for c in hardened.chat_stream(
                [{"role": "user", "content": "x"}],
            )
        ]
        assert [c.delta_content for c in chunks] == ["hi"]

    async def test_stream_no_retry_after_first_chunk(self):
        """First-chunk rule: once a chunk was yielded, errors propagate."""
        inner = _StreamYieldsThenFails(
            [LLMChunk(delta_content="hi")],
            RuntimeError("timeout"),
        )
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        chunks = []
        with pytest.raises(RuntimeError, match="timeout"):
            async for c in hardened.chat_stream(
                [{"role": "user", "content": "x"}],
            ):
                chunks.append(c)
        assert [c.delta_content for c in chunks] == ["hi"]
        assert inner.stream_calls == 1


# ===== P5: single retry layer (HardenedProvider owns retries) =====


class _StreamYieldsThenFails:
    """chat_stream yields some chunks, then raises."""

    def __init__(self, chunks, error):
        self._chunks = chunks
        self._error = error
        self.stream_calls = 0

    async def chat_stream(self, messages, tools=None, response_format=None):
        self.stream_calls += 1
        for chunk in self._chunks:
            yield chunk
        raise self._error


def _profile(name: str, max_retries: int = 0) -> ProviderProfile:
    return ProviderProfile(
        name=name,
        provider="tongyi",
        model="m",
        api_key="k",
        max_retries=max_retries,
    )


class TestProviderChainSingleRetryLayer:
    async def test_retryable_failure_calls_exactly_max_retries_plus_one(self):
        # max_retries=2 -> exactly 3 underlying calls, not (2+1)^2 = 9.
        inner = _ChatProvider(chat_responses=[RuntimeError("timeout")] * 3)
        chain = ProviderChain([_profile("p1", max_retries=2)])
        chain._instances[0] = HardenedProvider(
            inner,
            max_retries=2,
            retry_delay=0.01,
        )
        with pytest.raises(RuntimeError, match="timeout"):
            await chain.chat([{"role": "user", "content": "hi"}])
        assert inner.chat_calls == 3

    async def test_falls_to_next_profile_only_after_exhaustion(self):
        inner1 = _ChatProvider(chat_responses=[RuntimeError("timeout")] * 3)
        inner2 = _ChatProvider(
            chat_responses=[LLMResponse(content="fallback")],
        )
        chain = ProviderChain([_profile("p1", max_retries=2), _profile("p2")])
        chain._instances[0] = HardenedProvider(
            inner1,
            max_retries=2,
            retry_delay=0.01,
        )
        chain._instances[1] = HardenedProvider(
            inner2,
            max_retries=0,
            retry_delay=0.01,
        )
        resp = await chain.chat([{"role": "user", "content": "hi"}])
        assert resp.content == "fallback"
        assert inner1.chat_calls == 3
        assert inner2.chat_calls == 1

    async def test_stream_error_after_first_chunk_no_fallback_no_duplication(
        self,
    ):
        bad = _StreamYieldsThenFails(
            [LLMChunk(delta_content="hi")],
            RuntimeError("timeout"),
        )
        good = _ChatProvider(
            stream_responses=[[LLMChunk(delta_content="fallback")]],
        )
        chain = ProviderChain([_profile("p1", max_retries=2), _profile("p2")])
        chain._instances[0] = HardenedProvider(
            bad,
            max_retries=2,
            retry_delay=0.01,
        )
        chain._instances[1] = HardenedProvider(
            good,
            max_retries=0,
            retry_delay=0.01,
        )
        chunks = []
        with pytest.raises(RuntimeError, match="timeout"):
            async for c in chain.chat_stream(
                [{"role": "user", "content": "x"}],
            ):
                chunks.append(c)
        # Only the first profile's chunk; no retry, no fallback, no duplicate.
        assert [c.delta_content for c in chunks] == ["hi"]
        assert bad.stream_calls == 1

    async def test_stream_non_retryable_stops_immediately(self):
        bad = _ChatProvider(stream_responses=[RuntimeError("invalid api key")])
        good = _ChatProvider(
            stream_responses=[[LLMChunk(delta_content="fallback")]],
        )
        chain = ProviderChain([_profile("p1"), _profile("p2")])
        chain._instances[0] = HardenedProvider(bad, max_retries=0)
        chain._instances[1] = HardenedProvider(good, max_retries=0)
        with pytest.raises(RuntimeError, match="invalid api key"):
            async for _ in chain.chat_stream(
                [{"role": "user", "content": "x"}],
            ):
                pass
        assert len(good._stream_q) == 1  # profile 2 never attempted


class TestHardenedBackoff:
    """Capped exponential backoff for transient model-API blips."""

    def test_defaults(self):
        h = HardenedProvider(_ChatProvider(chat_responses=[]))
        assert h.max_retries == 3
        assert h.retry_delay == 4.0
        assert h.max_backoff == 16.0

    def test_backoff_is_exponential_and_capped(self):
        h = HardenedProvider(_ChatProvider(chat_responses=[]))
        assert h._backoff(0) == 4.0
        assert h._backoff(1) == 8.0
        assert h._backoff(2) == 16.0
        assert h._backoff(3) == 16.0  # capped at max_backoff
        assert h._backoff(9) == 16.0

    def test_total_retry_window_near_30s(self):
        h = HardenedProvider(_ChatProvider(chat_responses=[]))
        # Sleeps happen on attempts 0..max_retries-1 (last attempt raises).
        total = sum(h._backoff(i) for i in range(h.max_retries))
        assert 25 <= total <= 30  # 4 + 8 + 16 = 28s


# The literal message tongyi.py raises when a request times out.
_STALLED = "API request timed out; check network or retry later"


class TestRetriesExhausted:
    """The provider layer marks a retry budget it already spent.

    agent.py's turn-level retry used to re-run that whole budget on top of
    itself. Against an endpoint that accepts the connection and never
    replies, one turn measured 12 HTTP attempts and ~819s of silent wall
    clock from a 60s configured request_timeout (3 turn cycles x 4 attempts
    x 60s + backoff). These pin the signal that stops the multiplication
    without weakening ProviderChain's fallback.
    """

    async def test_chat_exhaustion_raises_retries_exhausted(self):
        # The literal message tongyi.py raises on a read timeout. Note it is
        # classified retryable via "network", not "timeout": the pattern list
        # has no entry for the "timed out" wording the provider actually uses.
        inner = _ChatProvider(chat_responses=[RuntimeError(_STALLED)] * 3)
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        with pytest.raises(RetriesExhausted) as exc:
            await hardened.chat([{"role": "user", "content": "hi"}])
        assert inner.chat_calls == 3
        # The message is carried verbatim: the classifier above still has to
        # see the original wording.
        assert str(exc.value) == _STALLED
        assert isinstance(exc.value.error, RuntimeError)

    async def test_stream_exhaustion_raises_retries_exhausted(self):
        inner = _ChatProvider(stream_responses=[RuntimeError(_STALLED)] * 3)
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        with pytest.raises(RetriesExhausted):
            async for _ in hardened.chat_stream(
                [{"role": "user", "content": "x"}],
            ):
                pass
        assert not inner._stream_q  # all three attempts were really made

    def test_the_message_stays_retryable(self):
        # ProviderChain decides fallback with is_retryable_error(). If the
        # wrapper altered the message, exhausting one profile would abort the
        # whole chain instead of moving to the next.
        err = RetriesExhausted(
            RuntimeError("API request timed out; check network"),
        )
        assert _is_retryable_error(err) is True

    async def test_fast_exhaustion_reports_the_backoff_it_spent(self):
        # agent.py decides whether re-attempting upstream is worth it from
        # this number, so it has to be the loop's real wall clock and not a
        # placeholder: three instant failures plus retry_delay 0.01 backoff
        # is well under the slow threshold.
        inner = _ChatProvider(chat_responses=[RuntimeError(_STALLED)] * 3)
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        with pytest.raises(RetriesExhausted) as exc:
            await hardened.chat([{"role": "user", "content": "hi"}])
        assert 0.0 < exc.value.elapsed_sec < RetriesExhausted.SLOW_BUDGET_SEC
        assert exc.value.exhausted_slowly is False

    def test_slow_threshold_flips_at_one_request_timeout(self):
        # Below it the attempts were failing fast and an upper layer
        # re-attempting is cheap; at or above it each attempt burned a full
        # request_timeout and re-attempting multiplies that.
        slow = RetriesExhausted(
            RuntimeError(_STALLED),
            RetriesExhausted.SLOW_BUDGET_SEC,
        )
        assert slow.exhausted_slowly is True
        fast = RetriesExhausted(
            RuntimeError(_STALLED),
            RetriesExhausted.SLOW_BUDGET_SEC - 1,
        )
        assert fast.exhausted_slowly is False
        # Unmeasured (the default) reads as fast: giving up on a number
        # nobody collected would recreate the type-only skip.
        assert RetriesExhausted(RuntimeError(_STALLED)).exhausted_slowly is (
            False
        )

    async def test_chain_still_falls_back_after_a_stream_exhaustion(self):
        bad = _ChatProvider(stream_responses=[RuntimeError("timeout")] * 3)
        good = _ChatProvider(
            stream_responses=[[LLMChunk(delta_content="fallback")]],
        )
        chain = ProviderChain(
            [_profile("p1", max_retries=2), _profile("p2")],
        )
        chain._instances[0] = HardenedProvider(
            bad,
            max_retries=2,
            retry_delay=0.01,
        )
        chain._instances[1] = HardenedProvider(
            good,
            max_retries=0,
            retry_delay=0.01,
        )
        chunks = [
            c
            async for c in chain.chat_stream(
                [{"role": "user", "content": "x"}],
            )
        ]
        assert [c.delta_content for c in chunks] == ["fallback"]
        assert not bad._stream_q

    async def test_non_retryable_stays_a_plain_error(self):
        inner = _ChatProvider(
            chat_responses=[RuntimeError("invalid api key")],
        )
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        with pytest.raises(RuntimeError) as exc:
            await hardened.chat([{"role": "user", "content": "hi"}])
        assert not isinstance(exc.value, RetriesExhausted)
        assert inner.chat_calls == 1

    async def test_mid_stream_failure_stays_a_plain_error(self):
        # Nothing was spent on it: the first-chunk rule propagates at once, so
        # an upper layer choosing to re-attempt is not duplicating work.
        inner = _StreamYieldsThenFails(
            [LLMChunk(delta_content="hi")],
            RuntimeError("timeout"),
        )
        hardened = HardenedProvider(inner, max_retries=2, retry_delay=0.01)
        with pytest.raises(RuntimeError) as exc:
            async for _ in hardened.chat_stream(
                [{"role": "user", "content": "x"}],
            ):
                pass
        assert not isinstance(exc.value, RetriesExhausted)
        assert inner.stream_calls == 1
