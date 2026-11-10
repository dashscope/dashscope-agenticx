# -*- coding: utf-8 -*-
"""Tests for ``response_format`` passthrough in the OpenAI and Tongyi providers.

Verifies that:
  * OpenAIProvider.chat / chat_stream forward ``response_format`` to the API
    call when provided, and omit it when ``None``.
  * TongyiProvider._build_request_body includes ``response_format`` when
    provided and omits it when ``None``.
  * TongyiProvider.chat / chat_stream forward ``response_format`` end-to-end
    via the mocked HTTP transport.
"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest

from dashscope_agenticx.providers.openai import OpenAIProvider
from dashscope_agenticx.providers.tongyi import TongyiProvider

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


class _MockResponse:
    def __init__(self, **attrs):
        self.__dict__.update(attrs)

    def __getattr__(self, name):
        return None


async def _async_iter(items):
    for item in items:
        yield item


# ===========================================================================
# OpenAI provider
# ===========================================================================


class TestOpenAIResponseFormat:
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
                # Bind the real methods so we exercise the actual chat logic.
                provider._convert_messages = (
                    OpenAIProvider._convert_messages.__get__(
                        provider,
                        type(provider),
                    )
                )
                provider._convert_tools = (
                    OpenAIProvider._convert_tools.__get__(
                        provider,
                        type(provider),
                    )
                )
                provider.chat = OpenAIProvider.chat.__get__(
                    provider,
                    type(provider),
                )
                provider.chat_stream = OpenAIProvider.chat_stream.__get__(
                    provider,
                    type(provider),
                )
                return provider, instance

        return _make

    # ---- chat ----

    async def test_chat_includes_response_format_when_provided(
        self,
        make_provider,
    ):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_MockResponse(
                choices=[
                    _MockResponse(
                        message=_MockResponse(content="{}", tool_calls=None),
                    ),
                ],
            ),
        )
        rf = {"type": "json_object"}
        await provider.chat(
            [{"role": "user", "content": "hi"}],
            response_format=rf,
        )

        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == rf

    async def test_chat_omits_response_format_when_none(self, make_provider):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_MockResponse(
                choices=[
                    _MockResponse(
                        message=_MockResponse(content="hi", tool_calls=None),
                    ),
                ],
            ),
        )
        await provider.chat([{"role": "user", "content": "hi"}])

        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert "response_format" not in call_kwargs

    async def test_chat_default_response_format_is_none(self, make_provider):
        """Calling chat without the kwarg must not accidentally send one."""
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_MockResponse(
                choices=[_MockResponse(message=_MockResponse(content="x"))],
            ),
        )
        await provider.chat(
            [{"role": "user", "content": "hi"}],
            response_format=None,
        )
        assert (
            "response_format"
            not in client.chat.completions.create.call_args.kwargs
        )

    async def test_chat_still_sends_model_and_messages(self, make_provider):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_MockResponse(
                choices=[_MockResponse(message=_MockResponse(content="x"))],
            ),
        )
        await provider.chat(
            [{"role": "user", "content": "hi"}],
            response_format={"type": "json_object"},
        )
        kwargs = client.chat.completions.create.call_args.kwargs
        assert kwargs["model"] == "gpt-4o"
        assert kwargs["messages"] == [{"role": "user", "content": "hi"}]

    # ---- chat_stream ----

    async def test_chat_stream_includes_response_format_when_provided(
        self,
        make_provider,
    ):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_async_iter(
                [
                    _MockResponse(
                        choices=[
                            _MockResponse(
                                delta=_MockResponse(content="{}"),
                                finish_reason="stop",
                            ),
                        ],
                    ),
                ],
            ),
        )
        rf = {"type": "json_object"}
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
                response_format=rf,
            )
        ]
        assert any(c.delta_content for c in chunks)

        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert call_kwargs["response_format"] == rf
        assert call_kwargs["stream"] is True

    async def test_chat_stream_omits_response_format_when_none(
        self,
        make_provider,
    ):
        provider, client = make_provider()
        client.chat.completions.create = AsyncMock(
            return_value=_async_iter(
                [
                    _MockResponse(
                        choices=[
                            _MockResponse(
                                delta=_MockResponse(content="hi"),
                                finish_reason="stop",
                            ),
                        ],
                    ),
                ],
            ),
        )
        async for _ in provider.chat_stream(
            [{"role": "user", "content": "hi"}],
        ):
            pass

        call_kwargs = client.chat.completions.create.call_args.kwargs
        assert "response_format" not in call_kwargs


# ===========================================================================
# Tongyi provider — _build_request_body (unit, no network)
# ===========================================================================


class TestTongyiBuildRequestBody:
    @pytest.fixture
    def provider(self):
        return TongyiProvider(model="qwen-test", api_key="sk-test")

    def test_includes_response_format_when_provided(self, provider):
        rf = {"type": "json_object"}
        body = provider._build_request_body(
            [{"role": "user", "content": "hi"}],
            tools=None,
            stream=False,
            response_format=rf,
        )
        assert body["parameters"]["response_format"] == rf

    def test_omits_response_format_when_none(self, provider):
        body = provider._build_request_body(
            [{"role": "user", "content": "hi"}],
            tools=None,
            stream=False,
            response_format=None,
        )
        assert "response_format" not in body["parameters"]

    def test_omits_response_format_by_default(self, provider):
        """When response_format is not passed at all, it must not appear."""
        body = provider._build_request_body(
            [{"role": "user", "content": "hi"}],
            tools=None,
            stream=False,
        )
        assert "response_format" not in body["parameters"]

    def test_body_always_has_model_messages_stream(self, provider):
        body = provider._build_request_body(
            [{"role": "user", "content": "hi"}],
            None,
            stream=True,
        )
        assert body["model"] == "qwen-test"
        assert body["input"]["messages"] == [
            {"role": "user", "content": "hi"},
        ]
        assert body["parameters"]["stream"] is True

    def test_stream_flag_reflected(self, provider):
        body_stream = provider._build_request_body([], None, stream=True)
        body_no_stream = provider._build_request_body([], None, stream=False)
        assert body_stream["parameters"]["stream"] is True
        assert "stream" not in body_no_stream["parameters"]

    def test_response_format_preserved_with_tools(self, provider):
        tools = [{"name": "t", "description": "d", "parameters": {}}]
        rf = {"type": "json_object"}
        body = provider._build_request_body(
            [],
            tools,
            stream=False,
            response_format=rf,
        )
        assert body["parameters"]["response_format"] == rf
        assert body["parameters"]["tools"] == [
            {
                "type": "function",
                "function": {
                    "name": "t",
                    "description": "d",
                    "parameters": {},
                },
            },
        ]


# ===========================================================================
# Tongyi provider — end-to-end via mocked HTTP transport
# ===========================================================================


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


class TestTongyiResponseFormatHttp:
    async def test_chat_sends_response_format_in_body(self, monkeypatch):
        captured: dict = {}

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
                                    "content": "{}",
                                },
                                "finish_reason": "stop",
                            },
                        ],
                    },
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    },
                    "request_id": "r1",
                },
            )

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        rf = {"type": "json_object"}
        resp = await provider.chat(
            [{"role": "user", "content": "hi"}],
            response_format=rf,
        )
        assert resp.content == "{}"
        assert captured["body"]["parameters"]["response_format"] == rf

    async def test_chat_omits_response_format_when_none(self, monkeypatch):
        captured: dict = {}

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
                                    "content": "ok",
                                },
                                "finish_reason": "stop",
                            },
                        ],
                    },
                    "usage": {
                        "input_tokens": 1,
                        "output_tokens": 1,
                        "total_tokens": 2,
                    },
                    "request_id": "r1",
                },
            )

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        await provider.chat([{"role": "user", "content": "hi"}])
        assert "response_format" not in captured["body"]["parameters"]

    async def test_chat_stream_sends_response_format_in_body(
        self,
        monkeypatch,
    ):
        captured: dict = {}
        lines = [
            'data:{"output": {"choices": [{"finish_reason": "stop", '
            '"message": {"role": "assistant", "content": "{}"}}]}, '
            '"request_id": "r1"}',
        ]

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, content="\n".join(lines).encode())

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        rf = {"type": "json_object"}
        chunks = [
            c
            async for c in provider.chat_stream(
                [{"role": "user", "content": "hi"}],
                response_format=rf,
            )
        ]
        assert any(c.delta_content for c in chunks)
        assert captured["body"]["parameters"]["response_format"] == rf
        assert captured["body"]["parameters"]["stream"] is True

    async def test_chat_stream_omits_response_format_when_none(
        self,
        monkeypatch,
    ):
        captured: dict = {}
        lines = [
            'data:{"output": {"choices": [{"finish_reason": "stop", '
            '"message": {"role": "assistant", "content": "ok"}}]}, '
            '"request_id": "r1"}',
        ]

        def handler(request: httpx.Request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, content="\n".join(lines).encode())

        _MockTransport(handler).inject(monkeypatch)
        provider = TongyiProvider(api_key="sk-test")
        async for _ in provider.chat_stream(
            [{"role": "user", "content": "hi"}],
        ):
            pass
        assert "response_format" not in captured["body"]["parameters"]
