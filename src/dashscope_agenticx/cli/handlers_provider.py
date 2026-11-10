# -*- coding: utf-8 -*-
"""Provider configuration wizard (/provider).

Shared with setup: provider → model → optional connection settings → key.
New providers collect connection, model and credentials during registration.
"""
# pylint: disable=too-many-branches,too-many-statements

from __future__ import annotations

from rich.console import Console

from dashscope_agenticx.agent import Agent
from dashscope_agenticx.cli.startup import _print_provider_debug
from dashscope_agenticx.config import (
    PROVIDER_MODELS,
    PROVIDERS,
    Config,
    normalize_model_name,
    register_custom_model,
)
from dashscope_agenticx.extensions import find_provider
from dashscope_agenticx.providers import get_provider_chain

console = Console()


def _read_choice(prompt: str, default: str = "") -> str:
    """Read a menu choice; EOF/Ctrl-C cancels the flow."""
    from dashscope_agenticx.cli.wizard import read_choice

    return read_choice(prompt, default)


def _read_secret(prompt: str) -> str:
    """Read a secret without echo; EOF/Ctrl-C cancels the flow."""
    from dashscope_agenticx.cli.wizard import read_secret

    return read_secret(prompt)


def _numbered_pick(
    label: str,
    options: list[str],
    default: str,
    custom_hint: str = "",
    option_labels: list[str] | None = None,
) -> str:
    """Show a numbered list and read a choice (number or name).

    Empty input keeps `default`. Free-form names are returned as-is so
    callers can accept unlisted values (e.g. custom model names).
    """
    if options:
        console.print(f"[bold]{label}:[/bold]")
        for i, name in enumerate(options, 1):
            marker = "[green]✓[/green]" if name == default else " "
            suffix = f" ({option_labels[i - 1]})" if option_labels is not None else ""
            console.print(f"  [{i}] {marker} {name}{suffix}")
        if custom_hint:
            console.print(f"[dim]{custom_hint}[/dim]")
    hint = f" [{default}]" if default else ""
    raw = _read_choice(f"Choose (number or name){hint}: ", default=default)
    if raw.isdigit() and options and 1 <= int(raw) <= len(options):
        raw = options[int(raw) - 1]
    return raw


def _model_modality(provider: str, model: str) -> str:
    from dashscope_agenticx.config import is_audio_model, is_vision_model

    model = normalize_model_name(model)
    vision = is_vision_model(model)
    audio = is_audio_model(model)
    if provider == "tongyi":
        vision = vision or model.startswith(("qwen-vl", "qwen3-vl", "qwen-omni"))
        audio = audio or model.startswith("qwen-omni")
    elif provider == "openai":
        vision = vision or model.startswith(("gpt-4o", "gpt-4-turbo"))
    elif provider == "anthropic":
        vision = vision or model.startswith("claude-")
    if vision and audio:
        return "multimodal"
    if vision:
        return "vision"
    if audio:
        return "audio"
    return "text"


def _shortlist_models(provider: str, models: list[str], default: str) -> list[str]:
    models = list(dict.fromkeys(normalize_model_name(model) for model in models))
    default = normalize_model_name(default)
    preferred = [default] if default in models else []
    groups: dict[str, list[str]] = {}
    for model in [*preferred, *models]:
        group = groups.setdefault(_model_modality(provider, model), [])
        if model not in group and len(group) < 2:
            group.append(model)
    return [model for group in groups.values() for model in group]


def _rebuild_provider(agent: Agent, config: Config) -> bool:
    """Rebuild the provider chain after a config change; sync agent names."""
    try:
        agent.provider = get_provider_chain(config)
    except Exception as e:
        console.print(f"[red]Switch failed: {e}[/red]")
        console.print(
            "[dim]If an API Key is missing, run /provider again[/dim]",
        )
        return False
    agent.provider_name = config.provider
    agent.model_name = config.model
    _print_provider_debug(agent.provider)
    return True


def _warn_missing_ext_key(config: Config) -> None:
    """Warn when the active extension provider has no resolvable key —
    otherwise the chain would silently fall back on auth failure."""
    ext = find_provider(config.provider)
    if (
        ext is None
        or config.provider in PROVIDERS
        or ext.resolve_api_key(config)
    ):
        return
    env_hint = ext.api_key_env or "(none)"
    console.print(
        f"[yellow]⚠️  {config.provider} is missing an API Key "
        f"(env {env_hint} unset; no encrypted key in toml either)"
        f"[/yellow]",
    )


def _wizard_key_step(config: Config, *, context=None, new_provider=None):
    """Collect credentials once for both registration and configuration."""
    import os
    import re
    from pathlib import Path
    from dashscope_agenticx.cli.handlers_key import _set_extension_provider_token, all_key_targets
    from dashscope_agenticx.cli.wizard import WizardContext, WizardResult
    from dashscope_agenticx.extensions import GLOBAL_EXTENSIONS_FILE, encrypt_for_toml, set_provider_secret
    from dashscope_agenticx.providers.profile import build_profiles_from_config, maas_gateway

    context = context or WizardContext()
    extension = new_provider or find_provider(config.provider)
    name = extension.name if new_provider is not None else config.provider
    field = f"{name}_api_key"
    info = all_key_targets(config).get(name, {})
    env_name = extension.api_key_env if extension is not None else info.get("env", "")
    if extension is not None and not extension.auth:
        return WizardResult("completed", name)
    profiles = build_profiles_from_config(config) if new_provider is None else []
    existing = profiles[0].api_key if profiles else ""
    if name == "tongyi" and profiles and not config.base_url:
        maas = maas_gateway(config)
        if maas and profiles[0].base_url == maas[0]:
            field = "maas_api_key"
            env_name = ""
    if field in context.prompted_credentials:
        return WizardResult("deferred" if field in context.deferred_credentials else "completed", name)
    context.prompted_credentials.add(field)
    if env_name:
        context.prompted_credentials.add(env_name)
    if existing and (context.setup or not context.prompt_missing_credentials):
        return WizardResult("completed", name)
    if existing:
        value = _read_secret("API Key [configured, Enter to keep]: ")
        if not value:
            return WizardResult("completed", name)
        source = "1"
    elif not context.prompt_missing_credentials:
        source = "0"
        value = ""
    else:
        console.print("[bold]API Key[/bold]")
        console.print("  [1] Enter key (OS keyring)")
        console.print(f"  [2] Environment variable ({env_name})" if env_name else "  [2] Environment variable")
        console.print("  [0] No authentication" if new_provider is not None else "  [0] Configure later")
        while True:
            source = _read_choice("Key source [1]: ", "1")
            if source in ("0", "1", "2"):
                break
            console.print("[yellow]Choose 0, 1 or 2.[/yellow]")
        if source == "0" and new_provider is not None:
            extension.auth = False
            return WizardResult("completed", name)
        value = _read_secret("API Key [Enter to skip]: ") if source == "1" else ""
    if source == "2":
        if extension is not None:
            default_env = env_name or re.sub(r"[^A-Z0-9_]", "_", name.upper()) + "_API_KEY"
            while True:
                env_name = _read_choice(f"Environment variable [{default_env}]: ", default_env)
                if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", env_name):
                    break
                console.print("[yellow]Enter a valid environment variable name.[/yellow]")
            if new_provider is not None:
                extension.api_key_env = env_name
            else:
                target = Path(extension.source) if extension.source else GLOBAL_EXTENSIONS_FILE
                if not set_provider_secret(target, name, api_key_env=env_name):
                    return WizardResult("failed", name)
                extension.api_key_env = env_name
                extension.api_key_enc = ""
        if env_name:
            context.prompted_credentials.add(env_name)
        if os.environ.get(env_name):
            if extension is None:
                setattr(config, field, os.environ[env_name])
            return WizardResult("completed", name)
    elif value:
        if new_provider is not None:
            extension.api_key_enc = encrypt_for_toml(value)
            extension.api_key_env = ""
        elif extension is not None and name not in PROVIDERS:
            if not _set_extension_provider_token(
                extension, config, direct_value=value,
                persist=not context.setup, announce=not context.setup,
            ):
                return WizardResult("failed", name)
        else:
            setattr(config, field, value)
        return WizardResult("completed", name)
    if new_provider is not None and not extension.api_key_env:
        extension.api_key_env = re.sub(r"[^A-Z0-9_]", "_", name.upper()) + "_API_KEY"
    if new_provider is not None:
        env_name = extension.api_key_env
    context.deferred_credentials.add(field)
    if env_name:
        context.prompted_credentials.add(env_name)
        context.deferred_credentials.add(env_name)
    if context.prompt_missing_credentials:
        console.print(f"[dim]API Key deferred; environment source: {env_name}.[/dim]" if env_name
                      else "[dim]API Key deferred.[/dim]")
    return WizardResult("deferred", name)


def _configure_provider(config: Config, *, context=None, use_defaults=False):
    """Shared provider selection, model and credential configuration."""
    from dashscope_agenticx.cli.wizard import WizardContext, WizardResult, valid_api_url
    from dashscope_agenticx.extensions import current

    context = context or WizardContext()
    added = False
    if not use_defaults:
        while True:
            loaded = current()
            for error in loaded.errors:
                console.print(f"[yellow]{error}[/yellow]")
            names = list(PROVIDER_MODELS) + [item.name for item in loaded.providers if item.name not in PROVIDER_MODELS]
            default = config.provider if config.provider in names else ""
            if config.provider and not default:
                console.print(f"[yellow]Configured provider '{config.provider}' is not available here. "
                              "Enter cannot keep it; choose a provider below.[/yellow]")
            provider = _numbered_pick("Available providers", names, default,
                                     custom_hint="Not listed? Enter a new provider name to add it.")
            if not provider:
                console.print("[dim]No provider chosen; cancelled[/dim]")
                return WizardResult("cancelled")
            if provider in names:
                break
            console.print(f"[yellow]Provider '{provider}' is not registered.[/yellow]")
            if _read_choice("Add this provider now? [y/N]: ", "n").lower() in ("y", "yes"):
                from dashscope_agenticx.dev import _provider_add

                result = _provider_add(config, name=provider, show_usage=False, context=context)
                if result is not None and result.handled:
                    added = True
                    break
            console.print("[dim]Choose a listed provider or press Enter to keep the current provider.[/dim]")
        extension = find_provider(provider)
        if provider != config.provider or added:
            config.provider = provider
            config.base_url = ""
            config.protocol = (extension.resolved_protocol() if extension is not None
                               else "anthropic" if provider == "anthropic" else "openai")
            models = extension.resolved_models() if extension is not None else PROVIDER_MODELS.get(provider, [])
            config.model = (extension.default_model if extension is not None and extension.default_model
                            else models[0] if models else "")
        if not added:
            models = PROVIDER_MODELS.get(provider, [])
            if not models and extension is not None:
                models = extension.resolved_models()
            models = list(dict.fromkeys(normalize_model_name(model) for model in models))
            default_model = config.model if config.model in models or not models else models[0]
            shortlist = _shortlist_models(provider, models, default_model)
            model = _numbered_pick(
                "Available models", shortlist, default_model,
                custom_hint="Up to 2 models per modality. Enter any other model name.",
                option_labels=[_model_modality(provider, name) for name in shortlist],
            )
            if not model:
                return WizardResult("cancelled", provider)
            normalized = normalize_model_name(model)
            if normalized not in models and provider in PROVIDERS:
                register_custom_model(config, provider, normalized, persist=not context.setup)
            config.model = normalized
            if _read_choice("Advanced connection settings? [y/N]: ", "n").lower() in ("y", "yes"):
                while True:
                    base_url = _read_choice(f"API base URL [{config.base_url or 'provider default'}]: ", config.base_url)
                    if not base_url or valid_api_url(base_url):
                        config.base_url = base_url
                        break
                    console.print("[yellow]Enter an absolute HTTP(S) API URL without credentials or query parameters.[/yellow]")
                while True:
                    protocol = _read_choice(f"Protocol [{config.protocol}]: ", config.protocol)
                    if protocol in ("openai", "anthropic", "dashscope"):
                        config.protocol = protocol
                        break
                    console.print("[yellow]Choose openai, anthropic or dashscope.[/yellow]")
    if added:
        deferred = f"{config.provider}_api_key" in context.deferred_credentials
        return WizardResult("deferred" if deferred else "completed", config.provider)
    return _wizard_key_step(config, context=context) or WizardResult("completed", config.provider)


def _provider_wizard(agent: Agent, config: Config, *, context=None, use_defaults=False):
    from copy import deepcopy

    from dashscope_agenticx.cli.wizard import WizardCancelled, WizardContext, WizardResult

    context = context or WizardContext()
    previous = deepcopy(config.__dict__)
    try:
        result = _configure_provider(config, context=context, use_defaults=use_defaults)
    except WizardCancelled:
        console.print("[dim]Provider configuration cancelled.[/dim]")
        result = WizardResult("cancelled")
    if context.setup:
        return result
    if not result.handled:
        config.__dict__.clear()
        config.__dict__.update(previous)
    if result.handled:
        config.save_global()
        config.save_workspace()
        if _rebuild_provider(agent, config):
            console.print(f"[green]✓ Provider: {config.provider}/{config.model}[/green]")
    return True


def handle_provider_command(cmd: str, agent: Agent, config: Config, *, context=None, use_defaults=False):
    """Open the shared provider wizard, optionally as a setup step."""
    return _provider_wizard(agent, config, context=context, use_defaults=use_defaults)


def handle_model_command(cmd: str, agent: Agent, config: Config) -> bool:
    """/model [name] — switch the model inside the current provider.

    Users coming from other CLIs type /model reflexively; making them
    rediscover it under the /provider wizard costs a turn. Provider, key
    and protocol stay with /provider.
    """
    parts = cmd.split(maxsplit=1)
    if len(parts) == 1 or not parts[1].strip():
        console.print(
            f"[cyan]{config.provider}/{config.model}[/cyan]  "
            f"protocol={config.protocol}",
        )
        console.print(
            "[dim]Usage: /model <name> switches the model; "
            "/provider covers provider/key/protocol.[/dim]",
        )
        return True

    model = parts[1].strip()
    normalized = normalize_model_name(model)
    if normalized != model:
        console.print(
            f"[dim]Model name normalized to {normalized} "
            f"(API model IDs are case-sensitive)[/dim]",
        )

    # Same rule as the wizard's model step: an unlisted name under a
    # built-in provider is registered as a custom model; under an
    # extension provider it is used as-is.
    models = PROVIDER_MODELS.get(config.provider, [])
    if not models:
        ext = find_provider(config.provider)
        if ext is not None:
            models = ext.resolved_models()
    if (
        models
        and normalized not in models
        and config.provider in PROVIDER_MODELS
    ):
        register_custom_model(config, config.provider, normalized)
        console.print(
            f"[green]✓ Registered {config.provider}/{normalized}[/green]",
        )
    config.model = normalized
    config.save_global()
    config.save_workspace()
    if _rebuild_provider(agent, config):
        console.print(
            f"[green]✓ Switched: {config.provider}/{config.model}[/green]",
        )
    return True
