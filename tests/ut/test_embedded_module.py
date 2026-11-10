# -*- coding: utf-8 -*-
"""Embedded hosts can stamp a scenario module into x-dashscope-sdk-client."""

from dashscope_agenticx import __version__ as agenticx_version

CLIENT_HEADER = "x-dashscope-sdk-client"


def test_sdk_and_embedded_defaults_use_flash(monkeypatch):
    import inspect

    from dashscope_agenticx import sdk
    from dashscope_agenticx.config import Config
    from dashscope_agenticx.ui.embedded import run

    assert inspect.signature(run).parameters["default_model"].default == "qwen3.8-flash"
    assert inspect.signature(sdk.run_interactive).parameters["default_model"].default == "qwen3.8-flash"
    captured = {}

    def load(**kwargs):
        captured.update(kwargs)
        return Config(provider=kwargs["default_provider"], model=kwargs["default_model"])

    monkeypatch.setattr(Config, "load", load)
    config = sdk.load_config()
    assert config.provider == "tongyi" and config.model == "qwen3.8-flash"
    assert captured == {"default_provider": "tongyi", "default_model": "qwen3.8-flash"}


def _embedded_config(module):
    from types import SimpleNamespace

    return SimpleNamespace(
        provider="tongyi",
        model="qwen3.8-max",
        tongyi_api_key="sk-x",
        base_url="",
        protocol="openai",
        timeout=60,
        fallback_providers=[],
        anthropic_api_key="",
        openai_api_key="",
        _embedded_module=module,
    )


def _provider_for(config, monkeypatch):
    # pylint: disable=protected-access
    monkeypatch.setattr(
        "dashscope_agenticx.extensions.find_provider",
        lambda *a, **k: None,
    )
    from dashscope_agenticx.providers import _create_provider
    from dashscope_agenticx.providers.profile import build_profiles_from_config

    profile = build_profiles_from_config(config)[0]
    return _create_provider(profile)


def _check_client_header(value, client, version, module=""):
    parts = value.split("/")
    assert parts[0] == client
    assert parts[1] == version
    if module:
        assert len(parts) == 3
        assert parts[2] == module
    else:
        assert len(parts) == 2


def test_embedded_module_segment_flows_to_header(monkeypatch):
    provider = _provider_for(_embedded_config("expert"), monkeypatch)
    value = provider._get_headers()[CLIENT_HEADER]
    _check_client_header(
        value,
        "dashscope-agenticx",
        agenticx_version,
        "expert",
    )


def test_embedded_module_defaults_to_app(monkeypatch):
    provider = _provider_for(_embedded_config(""), monkeypatch)
    value = provider._get_headers()[CLIENT_HEADER]
    _check_client_header(value, "dashscope-agenticx", agenticx_version, "app")
