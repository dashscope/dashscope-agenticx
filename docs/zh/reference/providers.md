---
description: "LLM Provider 协议、内置 Provider、路由与加固 API 参考。"
---
# 模型提供者

Provider 将模型后端适配到统一协议（`LLMProvider`：`chat`、
`chat_stream`、`aclose`）。内置的 Tongyi Provider 直接通过 httpx 与
DashScope 通信；OpenAI 与 Anthropic Provider 是可选扩展。

| Provider | 安装 | 说明 |
|---|---|---|
| `tongyi`（默认） | 核心 | 通过 httpx 访问 DashScope 原生生成端点 |
| `openai` | `pip install "dashscope-agenticx[provider-openai]"` | OpenAI 及兼容网关 |
| `anthropic` | `pip install "dashscope-agenticx[provider-anthropic]"` | Anthropic Messages API |

自定义 Provider 可在 `.agenticx/custom-extensions.toml` 中声明，无需
编写 Python — 参见
[工具、技能与专家](../guides/tools-and-skills.md)。

路由遵循 `Config.fallback_providers`、MaaS 网关设置（`workspace`、
`region`、`maas_api_key`）以及由配置构建的各 Provider 档案。瞬时故障
由加固封装层重试；`provider.switched` 事件报告路由变化。

## 协议与共享类型

::: dashscope_agenticx.providers.base.LLMProvider

::: dashscope_agenticx.providers.base.ToolCall

::: dashscope_agenticx.providers.base.LLMResponse

::: dashscope_agenticx.providers.base.LLMChunk

## 内置 Provider

::: dashscope_agenticx.providers.tongyi.TongyiProvider

::: dashscope_agenticx.providers.openai.OpenAIProvider

::: dashscope_agenticx.providers.anthropic.AnthropicProvider

## 档案与路由

::: dashscope_agenticx.providers.profile.ProviderProfile

::: dashscope_agenticx.providers.profile.ProviderChain

::: dashscope_agenticx.providers.profile.build_profiles_from_config

::: dashscope_agenticx.providers.profile.validate_key_for_profile

::: dashscope_agenticx.providers.profile.maas_gateway

::: dashscope_agenticx.providers.profile.warn_legacy_gateway

## 加固（Hardening） { #hardening }

::: dashscope_agenticx.providers.hardening.HardenedProvider

::: dashscope_agenticx.providers.hardening.RetriesExhausted

::: dashscope_agenticx.providers.hardening.is_retryable_error

::: dashscope_agenticx.providers.hardening.account_error_hint

## 协议适配器

::: dashscope_agenticx.providers.adapter.anthropic_to_openai_request

::: dashscope_agenticx.providers.adapter.openai_to_anthropic_response

::: dashscope_agenticx.providers.adapter.anthropic_to_openai_response