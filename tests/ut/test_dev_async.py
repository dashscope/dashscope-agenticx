# -*- coding: utf-8 -*-
from __future__ import annotations

import asyncio
import types

import pytest

import dashscope_agenticx.extensions as ext_mod
import dashscope_agenticx.providers as providers_mod
from dashscope_agenticx.cli.dispatch import dispatch_async_command
from dashscope_agenticx.config import Config
from dashscope_agenticx.dev import _test_provider, handle_dev_command
from dashscope_agenticx.tools.registry import (
    PermissionLevel,
    ToolDefinition,
    registry,
)


@pytest.fixture
def echo_tool():
    calls = []

    async def fake_tool(**kwargs):
        calls.append(kwargs)
        return "tool-result"

    registry.register(
        ToolDefinition(
            name="ut_echo_tool",
            description="test",
            permission=PermissionLevel.AUTO,
            func=fake_tool,
            parameters={},
        ),
    )
    yield calls
    registry.unregister("ut_echo_tool")


class TestDebugCall:
    async def test_dispatch_awaits_inside_running_loop(self, echo_tool):
        """Regression: /dev debug call nested a second event loop inside the
        running REPL loop and crashed."""
        config = types.SimpleNamespace()
        await dispatch_async_command(
            '/dev debug call ut_echo_tool {"a": 1}',
            config,
            agent=None,
        )
        assert echo_tool == [{"a": 1}]

    async def test_handle_dev_command_no_nested_loop(self, echo_tool):
        """Even via the sync handler, no nested-loop error inside a loop."""
        handle_dev_command(
            '/dev debug call ut_echo_tool {"b": 2}',
            types.SimpleNamespace(),
        )
        for _ in range(3):
            await asyncio.sleep(0)
        assert echo_tool == [{"b": 2}]

    def test_handle_dev_command_without_loop(self, echo_tool):
        handle_dev_command(
            '/dev debug call ut_echo_tool {"c": 3}',
            types.SimpleNamespace(),
        )
        assert echo_tool == [{"c": 3}]


class _FakeResp:
    content = "OK"
    usage = {"input_tokens": 1, "output_tokens": 1}


class _FakeProvider:
    async def chat(self, messages, tools=None, **kwargs):
        return _FakeResp()


class TestTestProvider:
    async def test_resolves_via_build_profiles_from_config(self, monkeypatch):
        """The test provider must be built through the runtime profile
        pipeline, not hand-rolled key/base_url resolution."""
        monkeypatch.setattr(ext_mod, "find_provider", lambda name: None)

        build_calls = []
        real_build = providers_mod.build_profiles_from_config

        def spy_build(cfg):
            build_calls.append(cfg)
            return real_build(cfg)

        created = {}

        def fake_create(profile):
            created["profile"] = profile
            return _FakeProvider()

        monkeypatch.setattr(
            providers_mod,
            "build_profiles_from_config",
            spy_build,
        )
        monkeypatch.setattr(providers_mod, "_create_provider", fake_create)

        config = Config()
        config.tongyi_api_key = "sk-test"

        await _test_provider("tongyi", config)

        assert build_calls, "must resolve via build_profiles_from_config"
        assert build_calls[0].provider == "tongyi"
        assert created["profile"].api_key == "sk-test"

    async def test_extension_toml_resolution(self, monkeypatch):
        """Extension provider protocol/base_url/env key resolve identically
        to normal runtime operation."""
        from dashscope_agenticx.extensions import CustomProvider

        ext = CustomProvider(
            name="moonshot",
            base_url="https://api.moonshot.cn/v1",
            api_key_env="MOONSHOT_API_KEY",
            default_model="kimi-k2",
            protocol="openai",
        )
        monkeypatch.setattr(
            ext_mod,
            "find_provider",
            lambda name: ext if name == "moonshot" else None,
        )
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-moon")

        created = {}

        def fake_create(profile):
            created["profile"] = profile
            return _FakeProvider()

        monkeypatch.setattr(providers_mod, "_create_provider", fake_create)

        await _test_provider("moonshot", Config())

        profile = created["profile"]
        assert profile.base_url == "https://api.moonshot.cn/v1"
        assert profile.api_key == "sk-moon"
        assert profile.model == "kimi-k2"
        assert profile.protocol == "openai"
