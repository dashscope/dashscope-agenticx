# -*- coding: utf-8 -*-
"""Lightweight programmatic SDK for embedding agenticx in other applications.

This module exposes a stable, typed API surface on top of the lower-level
``Agent``, ``Executor``, and ``ProviderChain`` internals.  It is intended for
scripts, CI pipelines, IDE plugins, and other Python programs that want to
reuse agenticx's agent stack without invoking the full interactive CLI.

Example::

    import asyncio
    from dashscope_agenticx.sdk import run_once

    result = asyncio.run(run_once("Summarize README.md"))
    print(result)
"""
# pylint: disable=unused-import

from __future__ import annotations

import asyncio
import contextlib
from typing import AsyncIterator, Optional

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.config import DEFAULT_MODEL, Config
from dashscope_agenticx.events import (
    AgentEvent,
    AgentEventEnvelope,
    AgentEventType,
)
from dashscope_agenticx.runtime import Runtime, RuntimeBuilder, RuntimeContext


def _ensure_tools_imported() -> None:
    """Import every static built-in tool module."""
    from dashscope_agenticx.runtime import import_builtin_tools

    import_builtin_tools()


def load_config(
    provider: Optional[str] = None,
    model: Optional[str] = None,
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
) -> Config:
    """Load agenticx configuration with caller-supplied overrides.

    Workspace/global config and environment variables still take their normal
    priority; explicit arguments only fill in missing defaults.
    """
    config = Config.load(
        default_provider=provider or "tongyi",
        default_model=model or DEFAULT_MODEL,
    )
    if api_key and not config.api_key:
        config.api_key = api_key
    if base_url and not config.base_url:
        config.base_url = base_url
    return config


def create_agent(
    config: Optional[Config] = None,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
    *,
    oneshot: bool = False,
) -> Agent:
    """Create an ``Agent`` instance ready for programmatic use.

    Args:
        config: agenticx configuration.  If None, loads default config.
        system_prompt: Optional system prompt override.
        session_id: Optional safe topic identifier. If provided, conversation
            history is persisted under
            ``.agenticx/session/<id>/history.json``.
        oneshot: Enable autonomous one-shot guards and acceptance behavior.
    """
    _ensure_tools_imported()
    config = config or load_config()

    from dashscope_agenticx.cli import (
        _compose_system_prompt,
        _load_system_prompt,
    )
    from dashscope_agenticx.runtime import (  # pylint: disable=reimported
        RuntimeBuilder as _RuntimeBuilder,
    )

    resolved_prompt = _compose_system_prompt(
        system_prompt or _load_system_prompt(),
    )
    runtime = _RuntimeBuilder(config).build()
    return runtime.create_agent(
        system_prompt=resolved_prompt,
        session_id=session_id,
        oneshot=oneshot,
    )


async def run_events(
    prompt: str,
    config: Optional[Config] = None,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
) -> AsyncIterator[AgentEvent]:
    """Run one turn as typed events for TUI, Client, IDE, or automation."""
    agent = create_agent(
        config=config,
        system_prompt=system_prompt,
        session_id=session_id,
    )
    try:
        async with contextlib.aclosing(agent.run_events(prompt)) as events:
            async for event in events:
                yield event
    finally:
        if isinstance(getattr(agent, "runtime", None), Runtime):
            await agent.aclose()


async def run_stream(
    prompt: str,
    config: Optional[Config] = None,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
) -> AsyncIterator[str]:
    """Stream the agent's response for ``prompt`` as an async iterator."""
    agent = create_agent(
        config=config,
        system_prompt=system_prompt,
        session_id=session_id,
    )
    try:
        async with contextlib.aclosing(agent.run_stream(prompt)) as chunks:
            async for chunk in chunks:
                yield chunk
    finally:
        if isinstance(getattr(agent, "runtime", None), Runtime):
            await agent.aclose()


async def run_once(
    prompt: str,
    config: Optional[Config] = None,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Run one non-interactive turn and return the full response text.

    This is the easiest entry point for scripts and CI: it streams the full
    agent turn, executes any tool calls, and returns the final assistant text.
    """
    agent = create_agent(
        config=config,
        system_prompt=system_prompt,
        session_id=session_id,
        oneshot=True,
    )
    parts: list[str] = []
    try:
        async with contextlib.aclosing(agent.run_stream(prompt)) as chunks:
            async for chunk in chunks:
                parts.append(chunk)
    finally:
        if isinstance(getattr(agent, "runtime", None), Runtime):
            await agent.aclose()
    return "".join(parts)


def run_once_sync(
    prompt: str,
    config: Optional[Config] = None,
    system_prompt: Optional[str] = None,
    session_id: Optional[str] = None,
) -> str:
    """Synchronous wrapper around :func:`run_once`."""
    return asyncio.run(run_once(prompt, config, system_prompt, session_id))


def run_interactive(
    system_prompt: Optional[str] = None,
    app_name: str = "Agent",
    default_model: str = DEFAULT_MODEL,
    default_provider: str = "tongyi",
    api_key: Optional[str] = None,
    base_url: Optional[str] = None,
    command: Optional[str] = None,
    prompt_symbol: str = "You> ",
    sdk_index: Optional[list[str]] = None,
    tui: Optional[bool] = None,
    module: str = "",
    guide_url: str = "",
) -> None:
    """Run the full agenticx interactive loop with a custom identity.

    This is a thin SDK wrapper around the existing embedded mode; see
    ``dashscope_agenticx.ui.embedded.run`` for details.
    """
    from dashscope_agenticx.ui.embedded import run as _embedded_run

    _embedded_run(
        system_prompt=system_prompt,
        app_name=app_name,
        default_model=default_model,
        default_provider=default_provider,
        api_key=api_key,
        base_url=base_url,
        command=command,
        prompt_symbol=prompt_symbol,
        sdk_index=sdk_index,
        tui=tui,
        module=module,
        guide_url=guide_url,
    )


__all__ = [
    "Agent",
    "AgentEvent",
    "AgentEventEnvelope",
    "AgentEventType",
    "Config",
    "Runtime",
    "RuntimeBuilder",
    "RuntimeContext",
    "load_config",
    "create_agent",
    "run_events",
    "run_stream",
    "run_once",
    "run_once_sync",
    "run_interactive",
]
