# -*- coding: utf-8 -*-
"""Workspace setup command handlers."""

from __future__ import annotations

from rich.console import Console

from dashscope_agenticx.cli.constants import (
    ALL_CAPABILITY_KEYS,
)
from dashscope_agenticx.config import DEFAULT_MODEL, Config
from dashscope_agenticx.utils.ids import stable_memory_user_id

console = Console()


def _setup_user_name(config: Config) -> None:
    from dashscope_agenticx.cli.wizard import read_choice

    current = config.user_name or "anonymous"
    config.user_name = read_choice(f"Username [{current}]: ", current)


def _ensure_provider_key(config: Config):
    """Compatibility entry point for the shared credential step."""
    from dashscope_agenticx.cli.handlers_provider import _wizard_key_step
    from dashscope_agenticx.cli.wizard import WizardContext

    return _wizard_key_step(config, context=WizardContext(setup=True))


def _warn_unsatisfiable_capabilities(config: Config):
    """Use the capability handler for preset credential checks."""
    from dashscope_agenticx.cli.handlers_capability import (
        _handle_capability_command,
    )
    from dashscope_agenticx.cli.wizard import WizardContext

    return _handle_capability_command(
        "/capability",
        config,
        context=WizardContext(setup=True),
        use_defaults=True,
    )


def _setup_finalize(config: Config, agent, *, context=None) -> None:
    """Common tail: derive memory_user_id, persist, rebuild agent provider,
    re-register tools based on new capabilities, print summary."""
    if not config.memory_user_id:
        config.memory_user_id = stable_memory_user_id()

    from dashscope_agenticx.providers import get_provider_chain

    agent.provider = get_provider_chain(config)
    agent.provider_name = config.provider
    agent.model_name = config.model
    agent.user_name = config.user_name
    from dashscope_agenticx.runtime import Runtime

    runtime = getattr(agent, "runtime", None)
    if isinstance(runtime, Runtime):
        runtime.provider = agent.provider
        runtime.config = config
        agent.runtime_config = config

    # Re-register platform tools based on new enabled_capabilities
    from dashscope_agenticx.tools.platform import (
        current_capability_tools,
        register_platform_tools,
        unregister_capability_tools,
    )

    # Clear all existing capability tools first
    for cap_key in list(current_capability_tools()):
        unregister_capability_tools(cap_key)
    # Then register based on new config (same wiring as boot: repl.py passes
    # connect_mcp_fn so the mcp_connect tool gets registered).
    from dashscope_agenticx.cli.mcp import _connect_mcp

    selected = config.enabled_capabilities
    pending = context.pending_capabilities if context is not None else set()
    if selected is not None:
        config.enabled_capabilities = [
            key for key in selected if key not in pending
        ]
    try:
        register_platform_tools(config, connect_mcp_fn=_connect_mcp)
    finally:
        config.enabled_capabilities = selected
    from dashscope_agenticx.skills.manager import SkillManager

    manager = getattr(agent, "skill_manager", None)
    if isinstance(manager, SkillManager):
        manager.load(hook_bus=getattr(agent, "hook_bus", None))
    from dashscope_agenticx.cli.startup import (
        _compose_system_prompt,
        _load_system_prompt,
    )

    prompt = _compose_system_prompt(
        getattr(config, "_embedded_system_prompt", None)
        or _load_system_prompt(),
    )
    if hasattr(agent, "update_system_prompt"):
        agent.update_system_prompt(prompt)
    config.save_global()
    config.save_workspace()
    console.print(
        "\n[green]✓ Setup complete — config saved to "
        ".agenticx/config.toml[/green]",
    )
    caps_display = [
        key
        for key in (
            ALL_CAPABILITY_KEYS
            if config.enabled_capabilities is None
            else config.enabled_capabilities
        )
        if key not in pending
    ]
    from dashscope_agenticx.tools.registry import registry

    console.print(f"[dim]  User: {config.user_name}[/dim]")
    console.print(f"[dim]  Model: {config.provider}/{config.model}[/dim]")
    console.print(
        f"[dim]  Capabilities: "
        f"{', '.join(caps_display) if caps_display else '(none)'}[/dim]",
    )
    from dashscope_agenticx.providers.profile import build_profiles_from_config

    profiles = build_profiles_from_config(config)
    from dashscope_agenticx.extensions import find_provider

    extension = find_provider(config.provider)
    key_status = (
        "not required"
        if extension is not None and not extension.auth
        else "configured"
        if profiles and profiles[0].api_key
        else "deferred"
    )
    console.print(f"[dim]  API Key: {key_status}[/dim]")
    if pending:
        console.print(
            f"[yellow]  Pending capabilities: "
            f"{', '.join(sorted(pending))}[/yellow]",
        )
    _print_setup_expert(context)
    console.print(
        f"[dim]  Tools: {len(registry.list_tools())} registered[/dim]",
    )


def _print_setup_expert(context):
    if context is not None:
        from dashscope_agenticx.cli.examples import _KNOWN_EXAMPLES

        examples = [
            result
            for result in context.results
            if result.name in (*_KNOWN_EXAMPLES, "examples")
        ]
        if examples:
            example = examples[-1]
            console.print(
                "[dim]  Expert: skipped[/dim]"
                if example.status == "skipped"
                else f"[dim]  Expert: {example.name} ({example.status})[/dim]",
            )


def _setup_examples(*, context=None):
    from dashscope_agenticx.cli.examples import (
        _KNOWN_EXAMPLES,
        _handle_example_command,
    )
    from dashscope_agenticx.cli.wizard import (
        WizardContext,
        WizardResult,
        read_choice,
    )

    context = context or WizardContext(setup=True)
    while True:
        console.print("\n[bold]Experts (optional)[/bold]")
        console.print(
            "[dim]Keeps your connection settings and "
            "existing provider definitions.[/dim]",
        )
        console.print("  [0] Skip (default)")
        for index, name in enumerate(_KNOWN_EXAMPLES, 1):
            console.print(f"  [{index}] {name}")
        choice = read_choice("Expert [0]: ", "0")
        if choice == "0":
            result = WizardResult("skipped", "examples")
            context.results.append(result)
            return result
        if choice in _KNOWN_EXAMPLES:
            name = choice
        elif choice.isdigit() and 1 <= int(choice) <= len(_KNOWN_EXAMPLES):
            name = _KNOWN_EXAMPLES[int(choice) - 1]
        else:
            console.print(
                "[yellow]Choose a listed Expert or 0 to skip.[/yellow]",
            )
            continue
        copied = _handle_example_command(
            ["download", name],
            show_usage=False,
            preserve_configuration=True,
        )
        result = WizardResult("completed" if copied else "failed", name)
        context.results.append(result)
        if copied or read_choice(
            "Retry Expert selection? [y/N]: ",
            "n",
        ).lower() not in ("y", "yes"):
            return result


def _apply_preset_bailian(config: Config) -> None:
    config.provider = "tongyi"
    config.base_url = ""
    config.protocol = "openai"
    config.model = DEFAULT_MODEL
    config.enabled_capabilities = ["bailian.mcp", "bailian.cli"]


def _show_default_configuration(config: Config) -> None:
    from copy import deepcopy
    from urllib.parse import urlsplit

    from dashscope_agenticx.providers.profile import build_profiles_from_config
    from dashscope_agenticx.utils.sanitizer import sanitize_text

    defaults = deepcopy(config)
    _apply_preset_bailian(defaults)
    profiles = build_profiles_from_config(defaults)
    profile = profiles[0] if profiles else None
    console.print("\n[bold]Default configuration[/bold]")
    console.print(
        f"  Username: {defaults.user_name or 'anonymous'}",
        markup=False,
    )
    console.print(f"  Provider: {defaults.provider}", markup=False)
    console.print(f"  Model: {defaults.model}", markup=False)
    console.print(f"  Protocol: {defaults.protocol}", markup=False)
    endpoint = profile.base_url if profile else "(provider default)"
    try:
        parsed = urlsplit(endpoint or "")
        endpoint = parsed._replace(
            netloc=parsed.netloc.rsplit("@", 1)[-1],
            query="",
            fragment="",
        ).geturl()
    except ValueError:
        endpoint = "(invalid endpoint)"
    console.print(
        f"  API base URL: {sanitize_text(endpoint or '(provider default)')}",
        markup=False,
    )
    key_status = (
        "configured (value hidden)"
        if profile and profile.api_key
        else "configure later"
    )
    console.print(f"  API Key: {key_status}")
    console.print(
        f"  Capabilities: {', '.join(defaults.enabled_capabilities)}",
    )
    console.print(
        "[dim]Press Enter to use default model and capabilities. "
        "Missing credentials are deferred; model requests require an API Key "
        "and dependent capabilities remain pending.[/dim]",
    )
    console.print(
        "[dim]Experts are selected separately afterwards "
        "(optional; Enter to skip).[/dim]",
    )


async def _setup_preset_custom(config: Config, agent=None, *, context=None):
    from dashscope_agenticx.cli.handlers_capability import (
        _handle_capability_command,
    )
    from dashscope_agenticx.cli.handlers_provider import (
        handle_provider_command,
    )
    from dashscope_agenticx.cli.wizard import WizardContext

    context = context or WizardContext(setup=True)
    result = handle_provider_command(
        "/provider",
        agent,
        config,
        context=context,
    )
    context.results.append(result)
    if not result.handled:
        return result
    return _handle_capability_command("/capability", config, context=context)


async def _handle_setup(config: Config, agent):
    """Compose the existing configuration commands into one ordered flow."""
    from contextlib import ExitStack

    from dashscope_agenticx.agent import Agent
    from dashscope_agenticx.agents.delegate import bind_delegate_context
    from dashscope_agenticx.agents.subagent import bind_subagent_context
    from dashscope_agenticx.tools.platform import bind_capability_tools
    from dashscope_agenticx.tools.registry import (
        ToolRegistry,
        bind_tool_registry,
    )

    with ExitStack() as stack:
        target = getattr(agent, "tool_registry", None)
        if isinstance(target, ToolRegistry):
            stack.enter_context(bind_tool_registry(target))
        tracker = getattr(agent, "capability_tools", None)
        if isinstance(tracker, dict):
            stack.enter_context(bind_capability_tools(tracker))
        if isinstance(agent, Agent):
            stack.enter_context(bind_subagent_context(agent, config))
            stack.enter_context(bind_delegate_context(agent, config))
        return await _run_setup(config, agent)


async def _run_setup(config: Config, agent):
    from copy import deepcopy

    from dashscope_agenticx.cli.handlers_capability import (
        _handle_capability_command,
    )
    from dashscope_agenticx.cli.handlers_provider import (
        handle_provider_command,
    )
    from dashscope_agenticx.cli.wizard import (
        WizardCancelled,
        WizardContext,
        WizardResult,
        read_choice,
    )
    from dashscope_agenticx.dev import _hot_reload

    context = WizardContext(setup=True)
    previous = deepcopy(config.__dict__)
    try:
        console.print("[bold]Workspace setup[/bold]\n")
        _setup_user_name(config)
        console.print("\n[bold]Configuration mode[/bold]")
        console.print("  [1] Default — Alibaba/Bailian")
        console.print("  [2] Custom — provider, model and capabilities")
        _show_default_configuration(config)
        while True:
            choice = read_choice("Choice [1]: ", "1")
            if choice in ("1", "2"):
                break
            console.print("[yellow]Choose 1 or 2.[/yellow]")
        if choice == "1":
            _apply_preset_bailian(config)
            context.prompt_missing_credentials = False
            result = handle_provider_command(
                "/provider",
                agent,
                config,
                context=context,
                use_defaults=True,
            )
            context.results.append(result)
            if result.handled:
                _handle_capability_command(
                    "/capability",
                    config,
                    context=context,
                    use_defaults=True,
                )
        else:
            result = await _setup_preset_custom(config, agent, context=context)
        if not result.handled:
            config.__dict__.clear()
            config.__dict__.update(previous)
            return result
        _setup_examples(context=context)
        _hot_reload()
        _setup_finalize(config, agent, context=context)
        deferred = (
            context.deferred_credentials
            or context.pending_capabilities
            or any(step.status == "failed" for step in context.results)
        )
        return WizardResult("deferred" if deferred else "completed", "setup")
    except WizardCancelled:
        config.__dict__.clear()
        config.__dict__.update(previous)
        console.print(
            "[dim]Setup cancelled; "
            "completed registration steps are kept.[/dim]",
        )
        return WizardResult("cancelled", "setup")
    except (OSError, RuntimeError, ValueError) as error:
        config.__dict__.clear()
        config.__dict__.update(previous)
        console.print(f"[red]Setup could not finish: {error}[/red]")
        return WizardResult("failed", "setup")
