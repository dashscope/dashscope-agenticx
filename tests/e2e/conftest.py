# -*- coding: utf-8 -*-
"""Shared fixtures for the agenticx end-to-end test suite."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

import pytest

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.config import Config
from dashscope_agenticx.executor import Executor
from dashscope_agenticx.providers import _create_provider
from dashscope_agenticx.session import SessionManager, set_session_manager
from tests.e2e.fake_provider import FakeLLMProvider, fake_provider_chain


class _MemorySecretBackend:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, secret):
        self.values[(service, account)] = secret

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture(autouse=True)
def isolated_secret_store():
    from dashscope_agenticx.utils.secret_store import set_secret_backend

    set_secret_backend(_MemorySecretBackend())
    try:
        yield
    finally:
        set_secret_backend(None)


def _patch_module_attr(
    monkeypatch,
    module_name: str,
    attr: str,
    value: Any,
) -> None:
    """Patch ``attr`` on an already-imported module by name."""
    import importlib

    module = importlib.import_module(module_name)
    monkeypatch.setattr(module, attr, value)


@pytest.fixture
def isolated_agenticx_home(tmp_path, monkeypatch):
    """Redirect agenticx's config/session/audit paths to temp directories.

    Both global (``~/.agenticx``) and workspace (``./.agenticx``) directories are
    remapped so tests never read or write the developer's real agenticx config.
    """
    monkeypatch.setenv("AGENTICX_DISABLE_MIGRATION", "1")
    home = tmp_path / "home"
    home.mkdir()
    workspace = tmp_path / "workspace"
    workspace.mkdir()

    global_agenticx = home / ".agenticx"
    workspace_agenticx = workspace / ".agenticx"

    # Config constants used for loading/saving settings.
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.config",
        "CONFIG_DIR",
        global_agenticx,
    )
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.config",
        "CONFIG_FILE",
        global_agenticx / "config.toml",
    )
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.config",
        "WORKSPACE_DIR",
        workspace_agenticx,
    )
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.config",
        "WORKSPACE_CONFIG_FILE",
        workspace_agenticx / "config.toml",
    )
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.config",
        "WORKSPACE_SYSTEM_PROMPT_FILE",
        workspace_agenticx / "system-prompt.md",
    )

    # Agent also holds a reference to WORKSPACE_DIR for the MemoryManager.
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.agent",
        "WORKSPACE_DIR",
        workspace_agenticx,
    )

    # Extension catalog paths.
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.extensions",
        "GLOBAL_EXTENSIONS_FILE",
        global_agenticx / "custom-extensions.toml",
    )
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.extensions",
        "WORKSPACE_EXTENSIONS_FILE",
        workspace_agenticx / "custom-extensions.toml",
    )

    # Audit log path is computed at import time.
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.audit",
        "_AUDIT_FILE",
        workspace_agenticx / "audit.log",
    )

    # Session manager uses WORKSPACE_DIR at construction time.
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.session",
        "WORKSPACE_DIR",
        workspace_agenticx,
    )

    # Local conversation history used by /memory.
    _patch_module_attr(
        monkeypatch,
        "dashscope_agenticx.platforms.local.history",
        "WORKSPACE_DIR",
        workspace_agenticx,
    )

    # Ensure relative ``./.agenticx`` resolves to the temp workspace.
    monkeypatch.chdir(workspace)

    # Reset the session-manager singleton so it uses the patched workspace.
    set_session_manager(SessionManager(workspace_agenticx))

    return {"home": global_agenticx, "workspace": workspace_agenticx}


@pytest.fixture
def e2e_config(isolated_agenticx_home):
    """Load a minimal agenticx Config inside the isolated home."""
    config = Config.load(
        default_provider="tongyi",
        default_model="qwen3.7-plus",
    )
    config.tongyi_api_key = "sk-fake"
    config.memory_enabled = False
    config.tui = False
    config.enabled_capabilities = []
    return config


@pytest.fixture
def make_agent(e2e_config, monkeypatch):
    """Factory that builds a real ``Agent`` wired to a fake LLM provider.

    The returned callable accepts an optional ``response_map`` and returns
    ``(agent, fake_provider, config)``.
    """

    def _make(
        response_map: dict[str, Any] | Callable | None = None,
    ) -> tuple[Agent, FakeLLMProvider, Config]:
        fake = FakeLLMProvider(response_map=response_map)
        chain = fake_provider_chain(
            fake,
            name=e2e_config.provider,
            model=e2e_config.model,
        )

        # Intercept provider instantiation at the profile boundary so every
        # code path (Agent, /provider use, /dev test provider, SDK) talks to
        # the fake instead of a real SDK.
        monkeypatch.setattr(
            "dashscope_agenticx.providers._create_provider",
            lambda _profile: fake,
        )

        # Import tools so the registry is populated for tool-call tests.
        import dashscope_agenticx.tools.filesystem  # noqa: F401
        import dashscope_agenticx.tools.shell  # noqa: F401
        import dashscope_agenticx.tools.web_search  # noqa: F401

        executor = Executor(auto_approve=True)
        agent = Agent(
            provider=chain,
            executor=executor,
            max_turns=10,
            provider_name=e2e_config.provider,
            model_name=e2e_config.model,
            system_prompt="You are the agenticx test assistant.",
        )
        return agent, fake, e2e_config

    return _make


@pytest.fixture
def dispatch(make_agent, capsys):
    """Run a slash command and capture stdout.

    Returns ``(agent, fake_provider, config, captured)`` where ``captured``
    is the ``capsys`` capture object.
    """

    async def _run(
        cmd: str,
        response_map: dict[str, Any] | Callable | None = None,
    ) -> tuple[Agent, FakeLLMProvider, Config, Any]:
        agent, fake, config = make_agent(response_map=response_map)
        from dashscope_agenticx.cli.dispatch import (
            _handle_slash_command,
            dispatch_async_command,
        )

        result = _handle_slash_command(cmd, agent, config)
        if result == "async":
            await dispatch_async_command(cmd, config, agent)

        captured = capsys.readouterr()
        return agent, fake, config, captured

    return _run
