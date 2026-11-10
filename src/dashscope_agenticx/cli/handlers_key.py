# -*- coding: utf-8 -*-
"""API key management command handlers."""
# pylint: disable=too-many-branches,unused-argument

from __future__ import annotations

import sys

from rich.console import Console

from dashscope_agenticx.cli.constants import KEY_TARGETS
from dashscope_agenticx.config import PROVIDER_MODELS, Config
from dashscope_agenticx.extensions import find_provider
from dashscope_agenticx.providers import get_provider_chain
from dashscope_agenticx.providers.profile import build_profiles_from_config

console = Console()

# Model Studio doc links for the no-key startup prompt; locale segment
# comes from _doc_locale().
_GET_API_KEY_DOC = "https://help.aliyun.com/{}/model-studio/get-api-key"
_GUIDE_DOC = "https://help.aliyun.com/{}/model-studio/dashscope-sdk-expert"


def _doc_locale() -> str:
    """Pick the help-center locale from the process locale env vars."""
    import os

    for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
        if "zh" in (os.environ.get(var) or "").lower():
            return "zh"
    return "en"


def all_key_targets(config: Config | None = None) -> dict[str, dict]:
    """Merge KEY_TARGETS (built-in) with extension providers into one dict.

    Extension provider entries are synthesized from CustomProvider fields so
    they look identical to built-in targets downstream.
    """
    merged: dict[str, dict] = dict(KEY_TARGETS)
    try:
        from dashscope_agenticx.extensions import current
    except Exception:
        return merged

    for prov in current().providers:
        if prov.name in merged:
            continue
        if not prov.auth:
            merged[prov.name] = {
                "field": f"{prov.name}_api_key",
                "env": "",
                "scope": "global",
                "desc": f"{prov.name} (no auth)",
                "no_auth": True,
            }
            continue
        merged[prov.name] = {
            "field": f"{prov.name}_api_key",
            "env": prov.api_key_env or "",
            "scope": "global",
            "desc": prov.name,
            "extension": True,
        }
    return merged


def _prompt_input(prompt: str, secret: bool = False) -> str:
    """Read user input. When secret=True, characters are not echoed."""
    try:
        if secret:
            import getpass

            return getpass.getpass(prompt).strip()
        return input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return ""


def _print_no_key_prompt(config: Config, env_name: str) -> None:
    """Present the missing-key notice, doc links, and setup menu."""
    console.print(
        f"\n[yellow]No API Key detected for " f"{config.provider}[/yellow]",
    )
    if config.provider.lower() == "tongyi":
        lang = _doc_locale()
        console.print(
            f"[dim]Get an API Key: {_GET_API_KEY_DOC.format(lang)}[/dim]",
        )
        console.print(f"[dim]Guide: {_GUIDE_DOC.format(lang)}[/dim]")
    console.print("Choose how to set it up:")
    if env_name:
        console.print(f"  [1] Set env var {env_name} (exit and set)")
    else:
        console.print("  [1] Set corresponding env var (exit and set)")
    console.print("  [2] Enter API Key now")
    console.print("  [3] Set up later with /provider after startup")


def ensure_provider_key(config: Config, agent) -> bool:
    """If the active provider has no resolvable key, prompt the user.

    Built-in providers persist to the global config slot; extension providers
    persist a keyring reference into custom-extensions.toml. Returns True when
    the key is available (or the user opts to set it later), False should not
    happen because missing/empty input exits the process.
    """
    if getattr(config, "_embedded_mode", False):
        return True

    profiles = build_profiles_from_config(config)
    if profiles and profiles[0].api_key:
        return True

    ext = find_provider(config.provider)
    targets = all_key_targets(config)
    key_info = targets.get(config.provider)
    if ext is None and key_info is None:
        # Neither a built-in nor a loaded extension: this directory cannot
        # build that provider, so collecting a key is a dead end — and the
        # "<PROVIDER>_API_KEY" env var we would suggest is read by nothing.
        console.print(
            f"\n[yellow]Configured provider '{config.provider}' is not "
            "available here (no built-in or loaded extension by that "
            "name), so an API key alone will not make it work.[/yellow]",
        )
        console.print(
            "[dim]Starting anyway; run /provider to pick an available "
            "provider.[/dim]",
        )
        return True

    if key_info:
        env_name = key_info.get("env") or ""
    else:
        env_name = ext.api_key_env or ""

    _print_no_key_prompt(config, env_name)
    choice = input("\nChoose [1/2/3]: ").strip()

    if choice == "1":
        console.print("[dim]Set the env var, then restart:[/dim]")
        if env_name:
            console.print(f"[dim]  export {env_name}=sk-xxx[/dim]")
        sys.exit(0)
    elif choice == "2":
        api_key = _prompt_input("API Key: ", secret=True)
        if not api_key:
            console.print("[red]No API Key entered; exiting[/red]")
            sys.exit(0)
        if ext is not None:
            if _set_extension_provider_token(
                ext,
                config,
                direct_value=api_key,
            ):
                agent.provider = get_provider_chain(config)
                return True
            sys.exit(1)
        key_field = f"{config.provider}_api_key"
        if hasattr(config, key_field):
            setattr(config, key_field, api_key)
        else:
            config.api_key = api_key
        config.save_global()
        console.print("[green]API Key saved[/green]")
        agent.provider = get_provider_chain(config)
        return True
    elif choice == "3":
        console.print(
            "[dim]Hint: run /provider after startup to set API Key[/dim]\n",
        )
        return True
    else:
        console.print("[red]API Key not set; exiting[/red]")
        sys.exit(0)


def _set_extension_cap_token(
    cap_key: str,
    direct_value: str = "",
    *,
    announce: bool = True,
) -> bool:
    """Encrypt + persist a token into the [[capabilities]] block of
    custom-extensions.toml. If `direct_value` is empty, prompts (no echo).
    Picks the toml file the capability came from; defaults to global."""
    from pathlib import Path

    from dashscope_agenticx.extensions import (
        GLOBAL_EXTENSIONS_FILE,
        apply_extensions,
        auth_env_name,
        encrypt_for_toml,
        find_capability,
        set_capability_secret,
    )

    cap = find_capability(cap_key)
    if cap is None:
        console.print(
            f"[red]{cap_key} is not a registered "
            f"extension capability[/red]",
        )
        return False
    if (
        cap.auth
        and cap.auth.startswith("apikey-header:")
        or cap.auth.startswith("bearer:$")
        or cap.api_key_env
    ):
        env_hint = cap.api_key_env or auth_env_name(cap.auth)
    else:
        console.print(
            f"[yellow]{cap_key} current auth config "
            f"({cap.auth or 'none'}) does not need a token[/yellow]",
        )
        return False

    secret = direct_value or _prompt_input(
        f"  {cap_key} credential (value of env {env_hint}, "
        f"hidden input): ",
        secret=True,
    )
    if not secret:
        console.print("[dim]Cancelled[/dim]")
        return False

    enc = encrypt_for_toml(secret)
    target = Path(cap.source) if cap.source else GLOBAL_EXTENSIONS_FILE
    if not set_capability_secret(target, cap_key, api_key_enc=enc):
        console.print(
            f"[red]Write failed: {cap_key} block "
            f"not found in {target}[/red]",
        )
        return False
    apply_extensions(PROVIDER_MODELS)
    if announce:
        console.print("[green]✓ Credential saved in OS keyring[/green]")
    return True


def _set_extension_provider_token(
    ext_prov,
    config: Config,
    direct_value: str = "",
    *,
    persist: bool = True,
    announce: bool = True,
) -> bool:
    """Persist an extension provider API key as <name>_api_key in
    ~/.agenticx/config.toml, just like the built-in providers. If
    `direct_value`
    is empty, prompts (no echo).

    Returns True on success, False on cancellation or write failure
    (an error message is already printed on failure).
    """
    secret = direct_value or _prompt_input(
        f"  {ext_prov.name} API Key (hidden input, saved in OS keyring): ",
        secret=True,
    )
    if not secret:
        console.print("[dim]Cancelled[/dim]")
        return False

    # Save to the provider's dynamic slot, i.e. <name>_api_key.
    old_provider = config.provider
    try:
        config.provider = ext_prov.name
        config.api_key = secret
    finally:
        config.provider = old_provider
    if persist:
        config.save_global()
    if announce:
        console.print(
            f"[green]✓ {ext_prov.name}_api_key "
            "saved in the OS keyring[/green]",
        )
    return True


def _maybe_prompt_extension_token(
    cap_key: str,
    config=None,
    *,
    context=None,
) -> bool:
    """Resolve or collect a capability credential without repeating prompts."""
    import os

    from dashscope_agenticx.cli.wizard import WizardContext, read_secret
    from pathlib import Path

    from dashscope_agenticx.extensions import (
        GLOBAL_EXTENSIONS_FILE,
        apply_extensions,
        auth_env_name,
        find_capability,
        provider_key_for_env,
        set_capability_secret,
    )
    from dashscope_agenticx.utils.secret_store import resolve_secret

    context = context or WizardContext()
    cap = find_capability(cap_key)
    if cap is None:
        return True
    env_name = cap.api_key_env or auth_env_name(cap.auth)
    if not env_name:
        return True
    if os.environ.get(env_name) or (
        cap.api_key_enc and resolve_secret(cap.api_key_enc)
    ):
        return True
    if provider_key_for_env(config, env_name, KEY_TARGETS):
        return True
    reference = context.credential_references.get(env_name)
    if reference:
        target = Path(cap.source) if cap.source else GLOBAL_EXTENSIONS_FILE
        if not set_capability_secret(target, cap_key, api_key_enc=reference):
            console.print(
                f"[red]Write failed: credential for {cap_key} "
                "was not saved.[/red]",
            )
            return False
        apply_extensions(PROVIDER_MODELS)
        return True
    if env_name in context.prompted_credentials:
        return False
    context.prompted_credentials.add(env_name)
    if not context.prompt_missing_credentials:
        context.deferred_credentials.add(env_name)
        return False
    console.print(
        f"[yellow]{cap_key}: credential needed ({env_name}).[/yellow]",
    )
    if context.setup:
        secret = read_secret(f"{env_name} [Enter to skip]: ")
        ready = bool(secret) and _set_extension_cap_token(
            cap_key,
            direct_value=secret,
            announce=False,
        )
    else:
        choice = (
            input("Enter it now? (saved in OS keyring) [Y/n]: ")
            .strip()
            .lower()
        )
        ready = choice in ("", "y", "yes") and _set_extension_cap_token(
            cap_key,
        )
    if not ready:
        context.deferred_credentials.add(env_name)
    else:
        updated = find_capability(cap_key)
        if updated is not None and updated.api_key_enc:
            context.credential_references[env_name] = updated.api_key_enc
    return bool(ready)
