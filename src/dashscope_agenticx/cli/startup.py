# -*- coding: utf-8 -*-
"""Startup functions: banner, system prompt loading, provider debug."""

from __future__ import annotations

from pathlib import Path

from rich.console import Console

from dashscope_agenticx import __version__
from dashscope_agenticx.cli.constants import ALL_CAPABILITY_KEYS, STARTUP_LOGO
from dashscope_agenticx.config import DEFAULT_MODEL, Config
from dashscope_agenticx.tools.registry import registry
from dashscope_agenticx.utils import mask_secret

console = Console()


def _needs_workspace_setup(
    config: Config,
    config_file: Path | None = None,
) -> bool:
    from dashscope_agenticx.config import WORKSPACE_CONFIG_FILE

    if (config_file or WORKSPACE_CONFIG_FILE).exists():
        return False
    using_defaults = (
        config.provider == "tongyi" and config.model == DEFAULT_MODEL
    )
    return not config.api_key or using_defaults


def _load_system_prompt() -> str | None:
    """Load system prompt from .agenticx/system-prompt.md.

    Workspace takes precedence over global configuration.

    Project-specific instructions (.agenticx/rules.jsonl, .cursorrules, etc.)
    are discovered separately by Agent.__init__ and passed to the prompt
    pipeline for proper stable/ephemeral separation.
    """
    # Imported at call time so tests can patch the config workspace path.
    from dashscope_agenticx.config import CONFIG_DIR, WORKSPACE_DIR

    base: str | None = None
    for d in (WORKSPACE_DIR, CONFIG_DIR):
        path = d / "system-prompt.md"
        if path.is_file():
            try:
                base = path.read_text(encoding="utf-8").strip() or None
            except OSError:
                pass
            break

    return base


def _strip_frontmatter(text: str) -> str:
    if text.startswith("---"):
        end = text.find("---", 3)
        if end > 0:
            return text[end + 3 :].strip()
    return text.strip()


def _load_references() -> str | None:
    """Load .agenticx/references/*.md as always-on context.

    Global references load before workspace references.

    Unlike skills (invoked on demand), references are knowledge docs that must
    always be in the system prompt — e.g. generated SDK API indexes.
    """
    from dashscope_agenticx.config import CONFIG_DIR, WORKSPACE_DIR

    parts: dict[str, str] = {}
    for d in (CONFIG_DIR, WORKSPACE_DIR):
        ref_dir = d / "references"
        if not ref_dir.is_dir():
            continue
        for path in sorted(ref_dir.glob("*.md")):
            try:
                body = _strip_frontmatter(path.read_text(encoding="utf-8"))
            except OSError:
                continue
            if body:
                parts[
                    path.name
                ] = body  # workspace overrides global by file name
    return "\n\n---\n\n".join(parts.values()) if parts else None


def _compose_system_prompt(base: str | None) -> str | None:
    """Append .agenticx/references/ content to the resolved base prompt.

    Applied at every consumption site so embedded prompts (which bypass
    _load_system_prompt) still get references.
    """
    refs = _load_references()
    if not refs:
        return base
    section = f"## References (auto-loaded)\n\n{refs}"
    return f"{base}\n\n---\n\n{section}" if base else section


def _print_banner(config: Config | None = None) -> None:
    from dashscope_agenticx.config import WORKSPACE_DIR

    console.print(f"[bold cyan]{STARTUP_LOGO}[/bold cyan]", highlight=False)
    console.print(
        f"  [bold]AgenticX[/bold] [dim]v{__version__}[/dim] "
        f"— Drive everything with natural language",
    )
    console.print(f"  [dim]Workspace: {WORKSPACE_DIR}[/dim]\n")

    # ── Startup info: provider / model / user / capabilities / tools ──
    if config is not None:
        from dashscope_agenticx.cli.experts import _selected_expert

        user_display = config.user_name or "(not set)"
        console.print(
            f"  [bold]Provider:[/bold] [cyan]{config.provider}[/cyan]  "
            f"[bold]Model:[/bold] [cyan]{config.model}[/cyan]  "
            f"[bold]User:[/bold] [cyan]{user_display}[/cyan]",
        )
        console.print(
            "  [bold]Expert:[/bold] "
            f"[cyan]{_selected_expert() or '(not recorded)'}[/cyan]",
        )

        # API Key status (current provider)
        from dashscope_agenticx.cli.handlers_key import all_key_targets

        targets = all_key_targets(config)
        key_info = targets.get(config.provider)
        if key_info:
            key_val = getattr(config, key_info["field"], "")
            if key_val:
                console.print(
                    f"  [bold]API Key:[/bold] "
                    f"[green]✓ {mask_secret(key_val)}[/green] "
                    f"[dim]({key_info['env']})[/dim]",
                )
            else:
                console.print(
                    f"  [bold]API Key:[/bold] [red]✗ not set[/red] "
                    f"[dim]({key_info['env']}, set via /provider)[/dim]",
                )

        # Enabled capabilities
        caps_display = (
            ALL_CAPABILITY_KEYS
            if config.enabled_capabilities is None
            else config.enabled_capabilities
        )
        caps_str = ", ".join(caps_display) if caps_display else "none"
        console.print(f"  [bold]Caps:[/bold]   [dim]{caps_str}[/dim]")

        # Registered tool count
        tool_count = (
            len(registry.list_tools())
            if hasattr(registry, "list_tools")
            else "?"
        )
        console.print(
            f"  [bold]Tools:[/bold]   [dim]{tool_count} registered[/dim]",
        )

        # SDK knowledge base (embedded mode only)
        sdk_index = getattr(config, "_embedded_sdk_index", None)
        if sdk_index:
            console.print(
                f"  [bold]SDK:[/bold]    [green]✓[/green] "
                f"[dim]{', '.join(sdk_index)}[/dim]",
            )

        # Scenario doc link (embedded mode only)
        guide_url = getattr(config, "_embedded_guide_url", "")
        if guide_url:
            console.print(f"  [bold]Guide:[/bold]  [dim]{guide_url}[/dim]")

        console.print()

    console.print("  [dim]Session: /help /clear /exit[/dim]")
    console.print(
        "  [dim]Config: /setup /capability /subagents /provider /trust "
        "/rule[/dim]",
    )
    console.print("  [dim]Capabilities: /profile /mcp /skill[/dim]")
    console.print(
        "  [dim]Dev: /dev model|provider|capability (runtime) "
        "/dev platform|tool|skill (extension guides)[/dim]",
    )
    console.print(
        "  [dim]Input: Enter to submit / Ctrl+J for newline / "
        "multi-line paste preserved / @file expands content / "
        "/v voice input[/dim]",
    )
    console.print()


def _print_provider_debug(provider):
    """Print provider endpoint and key info for debugging."""
    if hasattr(provider, "client") and hasattr(provider.client, "base_url"):
        # OpenAIProvider
        base_url = str(provider.client.base_url)
        key = provider.client.api_key or ""
        console.print(f"[dim]  endpoint: {base_url}[/dim]")
        console.print(f"[dim]  api_key: {mask_secret(key)}[/dim]")
    elif hasattr(provider, "api_key"):
        # TongyiProvider
        key = provider.api_key or ""
        endpoint = getattr(provider, "base_url", "dashscope SDK")
        console.print(f"[dim]  endpoint: {endpoint}[/dim]")
        console.print(f"[dim]  api_key: {mask_secret(key)}[/dim]")
