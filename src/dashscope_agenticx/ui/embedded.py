# -*- coding: utf-8 -*-
"""Embedded mode — run agenticx as a library inside another CLI application.

Usage:
    from dashscope_agenticx.ui.embedded import run

    run(
        system_prompt="You are ...",
        app_name="My App",
        default_model="qwen3.8-flash",
        default_provider="tongyi",
        api_key="sk-...",
    )

The full agenticx interactive loop is reused (completions, slash commands,
keybindings, /skill menus, etc.). Only identity and defaults are overridden.
"""
# pylint: disable=protected-access,unused-import
from __future__ import annotations

import asyncio
import sys
from typing import Optional

from dashscope_agenticx.config import DEFAULT_MODEL


def run(
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
):
    """Run the full agenticx agent loop with a custom identity.

    Constructs an agenticx Config with the caller's overrides, then delegates
    to agenticx's complete interactive loop (slash commands, completions,
    keybindings, /skill, /provider sub-menus — everything).

    Args:
        system_prompt: The system prompt. If None, loads from
            .agenticx/system-prompt.md.
        app_name: Display name shown in the banner.
        default_model: Default LLM model name.
        default_provider: Default provider (tongyi/anthropic/openai/...).
        api_key: API key. If None, loads from dashscope_agenticx config or env.
        base_url: Custom base URL for the provider.
        command: If set, run one-shot mode with this prompt then exit.
        prompt_symbol: The input prompt symbol shown to the user.
        sdk_index: List of SDK index files loaded (e.g., ["python-sdk",
            "python-cli"]).
        tui: If set, override config.tui. None = use config value.
        module: Scenario segment for the x-dashscope-sdk-client header
            (dashscope-agenticx/<version>/<module>); empty keeps the default
            "app".
        guide_url: Scenario doc link shown in the startup banner.
    """
    from dashscope_agenticx.config import Config

    # Pass caller's defaults as initial values — Config.load() will let
    # workspace/global config and env vars override them naturally.
    config = Config.load(
        default_provider=default_provider,
        default_model=default_model,
    )

    # Explicit api_key from caller fills in MISSING key; saved config
    # takes priority.
    if api_key and not config.api_key:
        config.api_key = api_key
    if base_url and not config.base_url:
        config.base_url = base_url

    # Inject custom system prompt and display settings into config
    # so _run_loop can pick them up.
    if system_prompt:
        config._embedded_system_prompt = system_prompt
    config._embedded_app_name = app_name
    config._embedded_prompt_symbol = prompt_symbol
    if module:
        config._embedded_module = module
    if guide_url:
        config._embedded_guide_url = guide_url
    if sdk_index:
        config._embedded_sdk_index = sdk_index
    if tui is not None:
        config.tui = tui

    try:
        if command:
            asyncio.run(_run_oneshot_embedded(config, command, system_prompt))
        elif config.tui:
            from dashscope_agenticx.cli import _run_tui_mode

            _run_tui_mode(config)
        else:
            asyncio.run(_run_loop_embedded(config))
    except (KeyboardInterrupt, SystemExit):
        pass


async def _run_oneshot_embedded(
    config,
    prompt: str,
    system_prompt: str | None,
):
    """One-shot mode using the same fully wired stack as the SDK."""
    from dashscope_agenticx.sdk import create_agent

    agent = create_agent(
        config=config,
        system_prompt=system_prompt,
        oneshot=True,
    )

    async for chunk in agent.run_stream(prompt):
        sys.stdout.write(chunk)
        sys.stdout.flush()
    sys.stdout.write("\n")


async def _run_loop_embedded(config):
    """Delegate to agenticx's full interactive loop."""
    from dashscope_agenticx.cli import _run_loop

    await _run_loop(config)
