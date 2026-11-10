# -*- coding: utf-8 -*-
from __future__ import annotations

import inspect
from dataclasses import dataclass, field
from typing import AsyncIterator, Protocol


async def close_provider(provider: object) -> None:
    """Close a provider or its owned SDK client when a close API exists."""
    target = provider
    close = getattr(target, "aclose", None) or getattr(target, "close", None)
    if not callable(close):
        target = getattr(provider, "client", None)
        close = getattr(target, "aclose", None) or getattr(
            target,
            "close",
            None,
        )
    if callable(close):
        result = close()
        if inspect.isawaitable(result):
            await result


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict
    # The generation ended before the arguments were complete (output cap or
    # a dropped stream). The name survives even when the JSON tail does not,
    # so callers must not execute such a call as if its arguments were final.
    truncated: bool = False


@dataclass
class LLMResponse:
    content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    reasoning_content: str = ""
    usage: dict | None = (
        None  # Token usage: {input_tokens, output_tokens, total_tokens}
    )


@dataclass
class LLMChunk:
    delta_content: str = ""
    delta_reasoning_content: str = ""
    tool_calls: list[ToolCall] = field(default_factory=list)
    finish_reason: str | None = None
    usage: dict | None = None  # Token usage for streaming


class LLMProvider(Protocol):
    async def aclose(self) -> None:
        ...

    async def chat(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> LLMResponse:
        ...

    def chat_stream(
        self,
        messages: list[dict],
        tools: list[dict] | None = None,
        response_format: dict | None = None,
    ) -> AsyncIterator[LLMChunk]:
        ...
