---
description: "LLM provider protocol, built-in providers, routing, and hardening API reference."
---
# Providers

Providers adapt model backends to a single protocol (`LLMProvider`:
`chat`, `chat_stream`, `aclose`). The built-in Tongyi provider talks to
DashScope over httpx directly; OpenAI and Anthropic providers are
optional extras.

| Provider | Install | Notes |
|---|---|---|
| `tongyi` (default) | core | DashScope native generation endpoints over httpx |
| `openai` | `pip install "dashscope-agenticx[provider-openai]"` | OpenAI and compatible gateways |
| `anthropic` | `pip install "dashscope-agenticx[provider-anthropic]"` | Anthropic Messages API |

Custom providers can be declared in `.agenticx/custom-extensions.toml`
without writing Python — see
[Tools, Skills & Experts](../guides/tools-and-skills.md).

Routing honors `Config.fallback_providers`, MaaS gateway settings
(`workspace`, `region`, `maas_api_key`), and per-provider profiles built
from config. Transient failures are retried by the hardening wrapper;
`provider.switched` events report routing changes.

## Protocol & shared types

::: dashscope_agenticx.providers.base.LLMProvider

::: dashscope_agenticx.providers.base.ToolCall

::: dashscope_agenticx.providers.base.LLMResponse

::: dashscope_agenticx.providers.base.LLMChunk

## Built-in providers

::: dashscope_agenticx.providers.tongyi.TongyiProvider

::: dashscope_agenticx.providers.openai.OpenAIProvider

::: dashscope_agenticx.providers.anthropic.AnthropicProvider

## Profiles & routing

::: dashscope_agenticx.providers.profile.ProviderProfile

::: dashscope_agenticx.providers.profile.ProviderChain

::: dashscope_agenticx.providers.profile.build_profiles_from_config

::: dashscope_agenticx.providers.profile.validate_key_for_profile

::: dashscope_agenticx.providers.profile.maas_gateway

::: dashscope_agenticx.providers.profile.warn_legacy_gateway

## Hardening

::: dashscope_agenticx.providers.hardening.HardenedProvider

::: dashscope_agenticx.providers.hardening.RetriesExhausted

::: dashscope_agenticx.providers.hardening.is_retryable_error

::: dashscope_agenticx.providers.hardening.account_error_hint

## Protocol adapters

::: dashscope_agenticx.providers.adapter.anthropic_to_openai_request

::: dashscope_agenticx.providers.adapter.openai_to_anthropic_response

::: dashscope_agenticx.providers.adapter.anthropic_to_openai_response