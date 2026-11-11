# -*- coding: utf-8 -*-
from __future__ import annotations

from dashscope_agenticx.providers.base import (
    LLMProvider,
    LLMResponse,
    ToolCall,
)
from dashscope_agenticx.providers.profile import (
    ProviderChain,
    ProviderProfile,
    build_profiles_from_config,
    maas_gateway,
    validate_key_for_profile,
    warn_legacy_gateway,
)
from dashscope_agenticx.providers.tongyi import TongyiProvider

__all__ = [
    "LLMProvider",
    "LLMResponse",
    "ToolCall",
    "TongyiProvider",
    "ProviderProfile",
    "ProviderChain",
    "build_profiles_from_config",
    "validate_key_for_profile",
    "maas_gateway",
    "warn_legacy_gateway",
    "get_provider",
    "get_provider_chain",
    "_create_provider",
    "PROVIDER_API_KEY_ENVS",
]

# Env var hints for the /key command and profile validation. NOT used for
# dispatch — protocol routing is driven by profile.protocol (filled from
# the user's custom-extensions.toml, see the basic-chat Expert).
_WARNED_UNSUPPORTED_RUNTIME_OPTIONS: set[tuple[str, str, str]] = set()


PROVIDER_API_KEY_ENVS = {
    "anthropic": "ANTHROPIC_API_KEY",
    "openai": "OPENAI_API_KEY",
    "tongyi": "DASHSCOPE_API_KEY",
}


def _create_provider(profile: ProviderProfile):
    """Instantiate a concrete provider from a profile.

    Dispatch priority:
      1. Custom extension provider (custom-extensions.toml) → use its
         resolved protocol
      2. Built-in provider name:
         - tongyi / dashscope → TongyiProvider (protocol controls
           message conversion)
         - anthropic          → AnthropicProvider
         - openai / other     → OpenAIProvider (default)

    base_url / api_key / protocol are filled by build_profiles_from_config
    (Config field wins) and refined here via find_provider (user
    custom-extensions.toml provides fallbacks for declared providers).
    """
    model = profile.model
    api_key = profile.api_key
    base_url = profile.base_url
    proto = (profile.protocol or "openai").lower()
    provider_name = (profile.provider or "").lower()

    from dashscope_agenticx.extensions import (
        find_provider as _find_ext_provider,
    )

    ext = _find_ext_provider(profile.provider)
    if ext is not None:
        if not base_url:
            base_url = ext.base_url
        if not api_key:
            api_key = ext.resolve_api_key()
        if not (profile.protocol or "").strip():
            proto = ext.resolved_protocol()

    if provider_name in ("tongyi", "dashscope"):
        return TongyiProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            protocol=proto,
            module=profile.module,
        )
    if proto == "anthropic" or provider_name == "anthropic":
        from dashscope_agenticx.providers.anthropic import AnthropicProvider

        return AnthropicProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            module=profile.module,
        )
    if proto == "dashscope":
        return TongyiProvider(
            model=model,
            api_key=api_key,
            base_url=base_url,
            protocol=proto,
            module=profile.module,
        )
    from dashscope_agenticx.providers.openai import OpenAIProvider

    return OpenAIProvider(
        model=model,
        api_key=api_key,
        base_url=base_url,
        module=profile.module,
    )


def get_provider(
    provider_name: str,
    model: str,
    api_key: str,
    base_url: str | None = None,
    protocol: str = "openai",
):
    """Backward-compatible single-provider factory."""
    profile = ProviderProfile(
        name=provider_name,
        provider=provider_name,
        model=model,
        api_key=api_key,
        base_url=base_url,
        protocol=protocol,
    )
    return _create_provider(profile)


def get_provider_chain(config) -> ProviderChain:
    """Build a primary and fallback ProviderChain from AgenticX config."""
    legacy = (
        getattr(config, "thinking_provider", ""),
        getattr(config, "thinking_model", ""),
        getattr(config, "loop_mode", "auto"),
    )
    if (legacy[0] or legacy[1] or legacy[2] not in ("", "auto")) and (
        legacy not in _WARNED_UNSUPPORTED_RUNTIME_OPTIONS
    ):
        _WARNED_UNSUPPORTED_RUNTIME_OPTIONS.add(legacy)
        from rich.console import Console

        Console().print(
            "[yellow]⚠ thinking_provider/thinking_model/structured "
            "loop_mode are legacy compatibility settings and are not active "
            "in this build; execution uses the primary provider/model and "
            "the standard Agent loop.[/yellow]",
        )

    profiles = build_profiles_from_config(config)
    if not profiles:
        raise RuntimeError("No usable provider configuration")

    # Surface key/url mismatches as warnings for the primary provider only.
    if profiles:
        warnings = validate_key_for_profile(profiles[0])
        if warnings:
            from rich.console import Console

            console = Console()
            for w in warnings:
                console.print(f"[yellow]⚠️  {w}[/yellow]")

    return ProviderChain(profiles)
