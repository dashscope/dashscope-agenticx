# -*- coding: utf-8 -*-
"""Capability management command handlers."""
# pylint: disable=too-many-return-statements,too-many-branches
# pylint: disable=too-many-statements

from __future__ import annotations

from rich.console import Console

from dashscope_agenticx.cli.constants import (
    ALL_CAPABILITY_KEYS,
    CAPABILITY_CATALOG,
)
from dashscope_agenticx.config import PROVIDER_MODELS, Config

console = Console()


def sync_extensions_into_catalog(ext) -> None:
    """Fold extension capabilities from custom-extensions.toml into
    CAPABILITY_CATALOG / ALL_CAPABILITY_KEYS so /capability list / enable
    / disable see them as first-class entries alongside built-ins.

    Called from _run_loop at startup AND from dev._hot_reload after any
    /dev capability add|remove — so newly added caps are immediately
    eligible for /capability enable without restarting.

    Idempotent + removal-aware: caps not in `ext.capabilities` but
    currently in the catalog as extension entries get dropped (covers
    /dev capability remove).
    """
    from dashscope_agenticx.tools.platform import (
        _CAPABILITY_KEYS as _BUILTIN_KEYS,
    )
    from dashscope_agenticx.tools.platform import unregister_capability_tools

    ext_keys = {c.key for c in ext.capabilities}

    # Remove extension entries that no longer exist
    for i in range(len(CAPABILITY_CATALOG) - 1, -1, -1):
        cap = CAPABILITY_CATALOG[i]
        if cap["key"] in _BUILTIN_KEYS:
            continue  # built-ins stay
        if cap["key"] not in ext_keys:
            removed = CAPABILITY_CATALOG.pop(i)
            if removed["key"] in ALL_CAPABILITY_KEYS:
                ALL_CAPABILITY_KEYS.remove(removed["key"])
            # Cap vanished from toml (/dev capability rm): drop its tools too,
            # otherwise they stay registered until restart or manual disable.
            unregister_capability_tools(removed["key"])

    # Add new ones
    for cap in ext.capabilities:
        if cap.key in ALL_CAPABILITY_KEYS:
            continue
        platform_part, _, cap_part = cap.key.partition(".")
        CAPABILITY_CATALOG.append(
            {
                "key": cap.key,
                "name": cap.display or cap.key,
                "platform": platform_part,
                "cap": cap_part or cap.key,
                # creds checked at HTTP call time, not registration
                "requires": [],
            },
        )
        ALL_CAPABILITY_KEYS.append(cap.key)


def _cap_enabled(config: Config, cap_key: str) -> bool:
    """Check if a platform capability is enabled.

    None = all enabled (not configured).
    When privacy_mode is True, all cloud capabilities are disabled."""
    if config.privacy_mode:
        return False
    if config.enabled_capabilities is None:
        return True
    return cap_key in config.enabled_capabilities


def _require_capability(config: Config, cap_key: str) -> bool:
    """Gate a slash command on a platform capability.

    Returns True when the capability is enabled (or all enabled). Otherwise
    prints a hint with the available ways to enable it and returns False.
    """
    if _cap_enabled(config, cap_key):
        return True
    cap_name = next(
        (c["name"] for c in CAPABILITY_CATALOG if c["key"] == cap_key),
        cap_key,
    )
    console.print(
        f"[yellow]Capability not enabled: {cap_key} "
        f"({cap_name})[/yellow]\n"
        f"[dim]Ways to enable:[/dim]\n"
        f"[dim]  /capability enable {cap_key}    — enable now, "
        f"prompting for credentials as needed[/dim]\n"
        f"[dim]  /setup                          — re-select "
        f"capabilities[/dim]\n"
        f"[dim]  /capability list                  — view all "
        f"capability states[/dim]",
    )
    return False


def _prompt_missing_config(config: Config, requires: list[str], *, context=None):
    """Collect required fields once, sharing the setup credential state."""
    from dashscope_agenticx.cli.wizard import WizardContext, read_choice, read_secret
    from dashscope_agenticx.utils.sanitizer import is_secret_field

    context = context or WizardContext()
    ready = True
    for field_name in requires:
        if getattr(config, field_name, ""):
            continue
        if field_name in context.prompted_credentials:
            ready = False
            continue
        context.prompted_credentials.add(field_name)
        if not context.prompt_missing_credentials:
            context.deferred_credentials.add(field_name)
            ready = False
            continue
        prompt = f"{field_name} [Enter to skip]: "
        value = read_secret(prompt) if is_secret_field(field_name) else read_choice(prompt)
        if value:
            setattr(config, field_name, value)
        else:
            context.deferred_credentials.add(field_name)
            ready = False
    return ready


def _enable_capability(config: Config, cap_key: str, *, context=None):
    """Shared credential checks and activation for a selected capability."""
    from dashscope_agenticx.cli.handlers_key import _maybe_prompt_extension_token
    from dashscope_agenticx.cli.mcp import _connect_mcp
    from dashscope_agenticx.cli.wizard import WizardContext, WizardResult
    from dashscope_agenticx.tools.platform import register_one_capability, unregister_capability_tools

    context = context or WizardContext()
    if cap_key not in ALL_CAPABILITY_KEYS:
        console.print(f"[red]Unknown capability: {cap_key}[/red]")
        return WizardResult("failed", cap_key)
    if config.enabled_capabilities is None:
        if not context.setup:
            console.print("[dim]All capabilities currently enabled[/dim]")
            return WizardResult("completed", cap_key)
        config.enabled_capabilities = []
    already = cap_key in config.enabled_capabilities
    info = next(cap for cap in CAPABILITY_CATALOG if cap["key"] == cap_key)
    missing = [field for field in info["requires"] if not getattr(config, field, "")]
    ready = True
    if missing:
        if context.prompt_missing_credentials:
            console.print(f"[yellow]{cap_key}: configuration needed.[/yellow]")
        ready = (_prompt_missing_config(config, info["requires"], context=context)
                 if context.setup else _prompt_missing_config(config, info["requires"]))
    if ready:
        token_ready = (_maybe_prompt_extension_token(cap_key, config, context=context)
                       if context.setup else _maybe_prompt_extension_token(cap_key, config))
        ready = token_ready is not False
    if not ready:
        context.pending_capabilities.add(cap_key)
        if context.setup:
            if not already:
                config.enabled_capabilities.append(cap_key)
        else:
            console.print("[yellow]Config incomplete; not enabled[/yellow]")
        return WizardResult("deferred", cap_key)
    if not already:
        config.enabled_capabilities.append(cap_key)
    if context.setup:
        return WizardResult("completed", cap_key)
    config.save_workspace()
    unregister_capability_tools(cap_key)
    added = register_one_capability(config, cap_key, connect_mcp_fn=_connect_mcp)
    suffix = f" ({added} tools registered)" if added else " (no tools registered)"
    console.print(f"[green]✓ Enabled: {cap_key}[/green]{suffix}")
    return WizardResult("completed", cap_key)


def _configure_capabilities(config: Config, *, context, use_defaults=False):
    from dashscope_agenticx.cli.wizard import WizardResult, read_choice

    if use_defaults:
        selected = list(config.enabled_capabilities or [])
    else:
        current = config.enabled_capabilities or []
        options = [cap["key"] for cap in CAPABILITY_CATALOG]
        default = ",".join(str(options.index(key) + 1) for key in current if key in options) or "0"
        console.print("\n[bold]Capabilities (optional)[/bold]")
        console.print("  [0] None")
        for index, cap in enumerate(CAPABILITY_CATALOG, 1):
            console.print(f"  [{index}] {cap['key']} — {cap['name']}")
        while True:
            choice = read_choice(f"Capabilities [{default}]: ", default)
            if choice == "0":
                selected = []
                break
            selected = []
            valid = True
            for value in choice.replace(",", " ").split():
                key = options[int(value) - 1] if value.isdigit() and 1 <= int(value) <= len(options) else value
                if key not in options:
                    valid = False
                    break
                if key not in selected:
                    selected.append(key)
            if valid:
                break
            console.print("[yellow]Choose listed capability numbers or names; 0 selects none.[/yellow]")
    config.enabled_capabilities = []
    for key in selected:
        result = _enable_capability(config, key, context=context)
        context.results.append(result)
    return WizardResult("deferred" if context.pending_capabilities else "completed", "capabilities")


def _handle_capability_command(cmd: str, config: Config, *, context=None, use_defaults=False):
    """Handle /capability command — list/enable/disable capabilities."""
    if context is not None and context.setup:
        return _configure_capabilities(config, context=context, use_defaults=use_defaults)
    parts = cmd.strip().split()
    sub = parts[1] if len(parts) > 1 else ""

    if not sub or sub == "list":
        console.print("[bold]Capability status:[/bold]")
        for cap in CAPABILITY_CATALOG:
            enabled = _cap_enabled(config, cap["key"])
            status = "[green]✓[/green]" if enabled else "[dim]✗[/dim]"
            console.print(f"  {status} {cap['key']:20s} — {cap['name']}")
        return

    if sub == "enable" and len(parts) > 2:
        _enable_capability(config, parts[2])
        return

    if sub == "disable" and len(parts) > 2:
        cap_key = parts[2]
        if cap_key not in ALL_CAPABILITY_KEYS:
            console.print(f"[red]Unknown capability: {cap_key}[/red]")
            console.print(
                f"[dim]Options: {', '.join(ALL_CAPABILITY_KEYS)}[/dim]",
            )
            return
        if config.enabled_capabilities is None:
            config.enabled_capabilities = [
                k for k in ALL_CAPABILITY_KEYS if k != cap_key
            ]
        elif cap_key in config.enabled_capabilities:
            config.enabled_capabilities.remove(cap_key)
        else:
            console.print(f"[dim]{cap_key} not enabled[/dim]")
            return
        config.save_workspace()
        from dashscope_agenticx.tools.platform import (
            unregister_capability_tools,
        )

        removed = unregister_capability_tools(cap_key)
        console.print(
            f"[yellow]✗ Disabled: {cap_key}[/yellow] "
            f"({removed} tools unregistered)",
        )
        return

    if sub in ("reload", "refresh"):
        from dashscope_agenticx.extensions import apply_extensions
        from dashscope_agenticx.tools.platform import (
            refresh_extension_capability_tools,
        )

        ext = apply_extensions(PROVIDER_MODELS)
        sync_extensions_into_catalog(ext)
        # Re-register enabled extension caps so tool closures rebind to the
        # freshly loaded toml (endpoint/auth/token edits take effect now).
        refreshed = refresh_extension_capability_tools(config)
        console.print(
            f"[green]✓ Capability registry reloaded[/green] "
            f"({refreshed} extension tools refreshed)",
        )
        return

    if sub == "config":
        args = parts[2:]
        if not args:
            console.print("[bold]Capability config:[/bold]")
            console.print(
                "[dim]Use /subagents config <name> <key> <value>[/dim]",
            )
            console.print(
                "[dim]Supported: " + ", ".join(ALL_CAPABILITY_KEYS) + "[/dim]",
            )
            return
        # Delegate to subagents config for subagent-type capabilities
        from dashscope_agenticx.agents.subagents import (
            SUBAGENT_CAPABILITY_KEYS,
            handle_subagents_command,
        )

        cap_key = args[0]
        if cap_key in SUBAGENT_CAPABILITY_KEYS:
            # Rewrite as /subagents config <rest...>
            handle_subagents_command(
                f"/subagents config {' '.join(args)}",
                config,
            )
        else:
            console.print(
                f"[dim]{cap_key} is not a configurable subagent; "
                f"use /subagents config[/dim]",
            )
        return

    # User typed a known cap without an action — suggest enable/disable for it
    if sub in ALL_CAPABILITY_KEYS:
        currently_enabled = _cap_enabled(config, sub)
        action_hint = "disable" if currently_enabled else "enable"
        console.print(
            f"[yellow]Missing action: enable or disable[/yellow]\n"
            f"[dim]Current {sub}: "
            f"{'enabled' if currently_enabled else 'disabled'}[/dim]\n"
            f"[dim]e.g.: /capability {action_hint} {sub}[/dim]",
        )
        return

    console.print(
        r"[dim]Usage: /capability \[list|enable <cap>|disable <cap>|"
        r"reload|config <cap>][/dim]" + "\n"
        f"[dim]Available caps: {', '.join(ALL_CAPABILITY_KEYS)}[/dim]",
    )
