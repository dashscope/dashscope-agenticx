# -*- coding: utf-8 -*-
"""Fake LLM provider for end-to-end tests.

The fake implements the ``LLMProvider`` protocol and returns deterministic
responses based on a per-test response map.  It can emit synthetic tool calls
so the executor/tool loop can be exercised without real network traffic.
"""

from __future__ import annotations

from typing import Any, AsyncIterator, Callable

from dashscope_agenticx.providers.base import (
    LLMChunk,
    LLMProvider,
    LLMResponse,
    ToolCall,
)
from dashscope_agenticx.providers.profile import ProviderChain, ProviderProfile

Responder = Callable[[list[dict], int], Any]


def _coerce_response(value: Any) -> LLMResponse:
    """Normalize test-friendly response values to ``LLMResponse``."""
    if isinstance(value, LLMResponse):
        return value
    if isinstance(value, str):
        return LLMResponse(content=value)
    if value is None:
        return LLMResponse(content="")
    return LLMResponse(content=str(value))


class FakeLLMProvider:
    """Deterministic LLM stand-in for e2e tests."""

    def __init__(self, response_map: dict[str, Any] | Responder | None = None):
        self.response_map = response_map or {}
        self.calls: list[list[dict]] = []
        self._turn = 0

    def _last_user_text(self, messages: list[dict]) -> str:
        for msg in reversed(messages):
            if msg.get("role") == "user":
                content = msg.get("content", "")
                if isinstance(content, str):
                    return content
                # Multimodal content: take the first text block.
                for block in content:
                    if isinstance(block, dict) and block.get("type") == "text":
                        return block.get("text", "")
                return str(content)
        return ""

    def _resolve(self, messages: list[dict]) -> LLMResponse:
        self.calls.append(messages)
        self._turn += 1

        if callable(self.response_map):
            return _coerce_response(self.response_map(messages, self._turn))

        text = self._last_user_text(messages)
        response = self.response_map.get(text)
        if response is None:
            response = self.response_map.get("__default__", "")
        if callable(response):
            response = response(messages, self._turn)
        return _coerce_response(response)

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> LLMResponse:
        return self._resolve(messages)

    async def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> AsyncIterator[LLMChunk]:
        response = self._resolve(messages)
        if response.content:
            yield LLMChunk(delta_content=response.content)
        if response.reasoning_content:
            yield LLMChunk(delta_reasoning_content=response.reasoning_content)
        for tool_call in response.tool_calls:
            yield LLMChunk(tool_calls=[tool_call])
        yield LLMChunk(finish_reason="end_turn")


def fake_provider_chain(
    fake: LLMProvider,
    name: str = "tongyi",
    model: str = "qwen3.7-plus",
) -> ProviderChain:
    """Wrap ``fake`` in a ``ProviderChain`` so it satisfies ``Agent.provider``."""
    profile = ProviderProfile(
        name=name,
        provider=name,
        model=model,
        api_key="sk-fake",
        base_url=None,
        protocol="openai",
    )
    chain = ProviderChain([profile])
    # Bypass real SDK instantiation; the chain will yield our fake directly.
    chain._instances[0] = fake
    return chain
