# -*- coding: utf-8 -*-
from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from dashscope_agenticx import SDK_SESSION_ID, __version__
from dashscope_agenticx.dashscope_headers import (
    dashscope_sdk_headers,
    is_dashscope_endpoint,
)
from dashscope_agenticx.providers import _create_provider
from dashscope_agenticx.providers.profile import ProviderProfile


@pytest.mark.parametrize(
    "base_url",
    [
        "https://dashscope.aliyuncs.com/compatible-mode/v1",
        "https://dashscope-intl.aliyuncs.com/compatible-mode/v1",
        "https://workspace.cn-hangzhou.maas.aliyuncs.com/v1",
        "dashscope.aliyuncs.com/compatible-mode/v1",
    ],
)
def test_dashscope_endpoints_accepted(base_url):
    assert is_dashscope_endpoint(base_url)


@pytest.mark.parametrize(
    "base_url",
    [
        "",
        "https://api.openai.com/v1",
        "https://dashscope.aliyuncs.com.example.com/v1",
        "https://workspace.maas.aliyuncs.com.example.com/v1",
        "https://evilmaas.aliyuncs.com/v1",
    ],
)
def test_third_party_and_lookalike_endpoints_rejected(base_url):
    assert not is_dashscope_endpoint(base_url)


def test_headers_include_agenticx_version_module_and_session(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_DISABLE_SDK_HEADERS", raising=False)

    headers = dashscope_sdk_headers(
        "expert",
        base_url="https://dashscope.aliyuncs.com",
    )

    assert headers == {
        "x-dashscope-sdk-client": (f"dashscope-agenticx/{__version__}/expert"),
        "x-dashscope-sdk-session-id": SDK_SESSION_ID,
    }


def test_headers_can_be_disabled(monkeypatch):
    monkeypatch.setenv("DASHSCOPE_DISABLE_SDK_HEADERS", "1")
    assert dashscope_sdk_headers() == {}


def test_third_party_endpoint_receives_no_sdk_headers(monkeypatch):
    monkeypatch.delenv("DASHSCOPE_DISABLE_SDK_HEADERS", raising=False)
    assert dashscope_sdk_headers(base_url="https://api.openai.com/v1") == {}


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_provider_factory_propagates_module_and_headers(protocol, monkeypatch):
    monkeypatch.delenv("DASHSCOPE_DISABLE_SDK_HEADERS", raising=False)
    base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"
    profile = ProviderProfile(
        name="tongyi-compatible",
        provider="tongyi-compatible",
        model="qwen-test",
        api_key="sk-test",
        base_url=base_url,
        protocol=protocol,
        module="expert",
    )

    if protocol == "anthropic":
        fake_anthropic = MagicMock()
        fake_anthropic.AsyncAnthropic = MagicMock()
        patcher = patch(
            "dashscope_agenticx.providers.anthropic.anthropic",
            fake_anthropic,
        )
    else:
        fake_anthropic = None
        patcher = patch("dashscope_agenticx.providers.openai.AsyncOpenAI")

    with patcher as client_class:
        if fake_anthropic is not None:
            client_class = fake_anthropic.AsyncAnthropic
        _create_provider(profile)

    headers = client_class.call_args.kwargs["default_headers"]
    assert headers["x-dashscope-sdk-client"] == (
        f"dashscope-agenticx/{__version__}/expert"
    )
    assert headers["x-dashscope-sdk-session-id"] == SDK_SESSION_ID


@pytest.mark.parametrize("protocol", ["openai", "anthropic"])
def test_compatible_providers_do_not_leak_headers_to_third_parties(
    protocol,
    monkeypatch,
):
    monkeypatch.delenv("DASHSCOPE_DISABLE_SDK_HEADERS", raising=False)
    profile = ProviderProfile(
        name="third-party",
        provider="third-party",
        model="test-model",
        api_key="sk-test",
        base_url="https://api.example.com/v1",
        protocol=protocol,
    )

    if protocol == "anthropic":
        fake_anthropic = MagicMock()
        fake_anthropic.AsyncAnthropic = MagicMock()
        patcher = patch(
            "dashscope_agenticx.providers.anthropic.anthropic",
            fake_anthropic,
        )
    else:
        fake_anthropic = None
        patcher = patch("dashscope_agenticx.providers.openai.AsyncOpenAI")

    with patcher as client_class:
        if fake_anthropic is not None:
            client_class = fake_anthropic.AsyncAnthropic
        _create_provider(profile)

    assert client_class.call_args.kwargs["default_headers"] == {}


def test_mcp_marks_dashscope_requests(monkeypatch):
    from dashscope_agenticx.platforms.bailian.mcp import MCPClient

    monkeypatch.delenv("DASHSCOPE_DISABLE_SDK_HEADERS", raising=False)
    client = MCPClient("time", "sk-test")

    assert client._headers["x-dashscope-sdk-client"] == (
        f"dashscope-agenticx/{__version__}/mcp"
    )
    assert client._headers["x-dashscope-sdk-session-id"] == SDK_SESSION_ID


def test_mcp_custom_third_party_endpoint_receives_no_sdk_headers(monkeypatch):
    from dashscope_agenticx.platforms.bailian.mcp import MCPClient

    monkeypatch.delenv("DASHSCOPE_DISABLE_SDK_HEADERS", raising=False)
    client = MCPClient(
        "time",
        "sk-test",
        url="https://mcp.example.com/service",
    )

    assert "x-dashscope-sdk-client" not in client._headers
    assert "x-dashscope-sdk-session-id" not in client._headers
