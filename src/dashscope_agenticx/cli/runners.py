# -*- coding: utf-8 -*-
"""Runner functions for different execution modes."""
# pylint: disable=protected-access,too-many-branches,too-many-statements

from __future__ import annotations

import asyncio
import json
import os
import sys
import threading
from collections.abc import Callable
from pathlib import Path

from rich.console import Console

from dashscope_agenticx.cli.handlers_key import ensure_provider_key
from dashscope_agenticx.cli.handlers_setup import _handle_setup
from dashscope_agenticx.cli.multimodal import (
    _expand_at_references,
    _to_multimodal_content,
)
from dashscope_agenticx.cli.startup import (
    _compose_system_prompt,
    _load_system_prompt,
    _needs_workspace_setup,
)
from dashscope_agenticx.config import (
    PROVIDER_MODELS,
    Config,
    is_audio_model,
    is_vision_model,
)
from dashscope_agenticx.dev import _apply_custom_models
from dashscope_agenticx.executor import Executor

console = Console()

# How often a harness-observed run re-persists its usage totals.
_USAGE_FLUSH_SEC = 15.0


def _write_usage_file(path: str, executor: Executor) -> None:
    """Persist this run's token totals to ``path`` for an external harness.

    A file rather than stdout because the harness that sets
    ``AGENTICX_USAGE_FILE`` (Terminal-Bench) drives agenticx through
    tmux, and a
    30-minute run scrolls its final lines out of the capturable pane.

    Flushed periodically rather than once at the end for the same audience:
    the runs that most need measuring are the ones killed by an in-container
    ``timeout``, which never reach a final write.
    """
    stats = executor.get_stats()
    usage = stats["token_usage"]
    payload = {
        "input_tokens": usage["input_tokens"],
        "output_tokens": usage["output_tokens"],
        "total_tokens": usage["total_tokens"],
        "cached_tokens": usage["cached_tokens"],
        "api_calls": stats["api_calls"],
        "tool_calls": stats["total_tool_calls"],
        "duration_sec": round(stats["session_duration"], 3),
    }
    target = Path(path)
    try:
        # Replaced rather than written in place: the harness reads this from
        # the host while agenticx is still running, and keeps reading it
        # after the
        # trial has been abandoned, so a torn write costs the whole trial's
        # numbers rather than one tick's.
        tmp = target.with_name(target.name + ".tmp")
        tmp.write_text(json.dumps(payload), encoding="utf-8")
        os.replace(tmp, target)
    except OSError:
        # Observability must never be the reason a run fails.
        pass


def _start_usage_flusher(path: str, executor: Executor) -> Callable[[], None]:
    """Re-persist usage from a daemon thread; return its stop callable.

    A thread, not a write keyed off the output stream or an asyncio timer:
    the runs worth measuring are wedged inside ``run_stream``, where no chunk
    arrives to trigger a write and a blocked event loop starves a timer just
    as thoroughly. The last flush before the wedge is what survives a later
    SIGKILL, so ``duration_sec`` also records how long the process stayed
    alive after it went quiet.
    """
    stop = threading.Event()

    def _flush_loop() -> None:
        while not stop.wait(_USAGE_FLUSH_SEC):
            try:
                _write_usage_file(path, executor)
            except Exception:  # pylint: disable=broad-except
                # get_stats() copies dicts the main thread is mutating, so a
                # tick can race. Losing it is fine; killing the run is not.
                pass

    thread = threading.Thread(target=_flush_loop, daemon=True)
    thread.start()

    def _stop() -> None:
        stop.set()
        # Joined, not just signalled: the caller writes once more afterwards,
        # and two writers sharing one temp path would clobber each other.
        thread.join(timeout=5.0)

    return _stop


async def _run_oneshot(config: Config, prompt: str):
    """Non-interactive single-shot mode: run one prompt and exit."""
    _apply_custom_models(config)
    # Re-arm audit redaction from persisted config (restart must not drop it).
    from dashscope_agenticx.audit import configure_audit_logger

    configure_audit_logger(config)
    from dashscope_agenticx.debuglog import configure_debug_log

    configure_debug_log(config)
    from dashscope_agenticx.permission import configure_permission_policy

    configure_permission_policy()
    from dashscope_agenticx.extensions import apply_extensions

    apply_extensions(PROVIDER_MODELS)

    if not config.api_key:
        from dashscope_agenticx.cli.handlers_key import all_key_targets

        targets = all_key_targets(config)
        key_info = targets.get(config.provider)
        env_hint = (
            key_info.get("env")
            if key_info
            else f"{config.provider.upper()}_API_KEY"
        )
        print(f"Error: no API Key found for {config.provider}")
        print(f"  1) Set env var:       export {env_hint}=sk-xxx")
        print("  2) Set after launch:  /provider")
        print("  3) Interactive setup: /setup")
        sys.exit(1)

    from dashscope_agenticx.runtime import RuntimeBuilder

    runtime = RuntimeBuilder(
        config,
        extensions_prepared=True,
    ).build()
    try:
        with runtime.activate():
            await _run_oneshot_session(config, prompt, runtime)
    finally:
        await runtime.aclose()


async def _run_oneshot_session(config: Config, prompt: str, runtime):
    executor = runtime.executor
    agent = runtime.create_agent(
        system_prompt=_compose_system_prompt(_load_system_prompt()),
        oneshot=True,
    )

    expanded, images, audio_clips = _expand_at_references(prompt)
    if images and not is_vision_model(config.model):
        console.print(
            f"[yellow]Model {config.model} does not support images; "
            f"{len(images)} image(s) ignored.[/yellow]",
        )
        images = []
    if audio_clips and not is_audio_model(config.model):
        console.print(
            f"[yellow]Model {config.model} does not support audio; "
            f"{len(audio_clips)} audio clip(s) ignored.[/yellow]",
        )
        audio_clips = []
    agent_input = _to_multimodal_content(expanded, images, audio_clips)

    usage_path = os.environ.get("AGENTICX_USAGE_FILE", "")
    stop_flusher = None
    if usage_path:
        # Written before the first chunk as well: a run that wedges immediately
        # still has to leave proof that AGENTICX_USAGE_FILE reached it.
        _write_usage_file(usage_path, executor)
        stop_flusher = _start_usage_flusher(usage_path, executor)
    failure: Exception | None = None
    try:
        async for chunk in agent.run_stream(agent_input):
            sys.stdout.write(chunk)
            sys.stdout.flush()
    except Exception as e:  # pylint: disable=broad-except
        # Anything the retry layers did not absorb. Reported and signalled
        # through the exit code rather than left to escape asyncio.run as a
        # traceback: the caller cannot tell a stack dump from output, and a
        # harness scoring this run needs the two separated. KeyboardInterrupt
        # and SystemExit stay BaseException, so neither is caught here.
        failure = e
    finally:
        # Stopped before the trailing write so the two cannot interleave.
        if stop_flusher is not None:
            stop_flusher()
    sys.stdout.write("\n")
    if usage_path:
        _write_usage_file(usage_path, executor)
    if failure is not None:
        from dashscope_agenticx.providers.hardening import account_error_hint

        print(f"Error: {failure}", file=sys.stderr)
        hint = account_error_hint(failure)
        if hint:
            print(hint, file=sys.stderr)
        sys.exit(1)


def _run_dry_run(config: Config):
    """Preview configuration without starting the agent.

    Shows what would be loaded: provider, model, skills, MCP services,
    tools, capabilities, and overall readiness status.
    """
    from rich.table import Table

    console.print(
        "\n[bold cyan]agenticx --dry-run[/bold cyan]  Config preview\n",
    )

    # 1. Provider & Model
    provider_table = Table(
        title="Provider config",
        show_header=False,
        box=None,
    )
    provider_table.add_column("Key", style="cyan")
    provider_table.add_column("Value", style="green")
    provider_table.add_row("Provider", config.provider)
    provider_table.add_row("Model", config.model)
    provider_table.add_row("Protocol", config.protocol or "openai")
    provider_table.add_row(
        "API Key",
        "✓ configured" if config.api_key else "✗ not configured",
    )
    if config.base_url:
        provider_table.add_row("Base URL", config.base_url)
    console.print(provider_table)
    console.print()

    # 2. Skills
    from dashscope_agenticx.skills.base import BUILTIN_SKILLS, load_skill_files

    load_skill_files()
    skills = list(BUILTIN_SKILLS.values())
    if skills:
        skill_table = Table(
            title=f"Loaded Skills ({len(skills)})",
            show_header=True,
        )
        skill_table.add_column("Name", style="cyan")
        skill_table.add_column("Description", style="dim")
        for skill in skills:
            skill_table.add_row(skill.name, skill.description or "")
        console.print(skill_table)
    else:
        console.print("[yellow]Skills:[/yellow] none\n")
    console.print()

    # 3. MCP Services
    mcp_servers = getattr(config, "mcp_servers", None) or []
    if mcp_servers:
        mcp_table = Table(
            title=f"MCP Services ({len(mcp_servers)})",
            show_header=True,
        )
        mcp_table.add_column("Name", style="cyan")
        mcp_table.add_column("URL", style="dim")
        mcp_table.add_column("Status", style="green")
        for svc in mcp_servers:
            if isinstance(svc, dict):
                name, url = svc.get("service", "?"), svc.get("url", "?")
            else:
                name, url = svc.service, svc.url
            mcp_table.add_row(name or "?", url or "?", "pending")
        console.print(mcp_table)
    else:
        console.print("[yellow]MCP Services:[/yellow] none\n")
    console.print()

    # 4. Tools
    from dashscope_agenticx.tools.registry import registry

    tools = registry.list_tools()
    tool_table = Table(
        title=f"Available Tools ({len(tools)})",
        show_header=True,
    )
    tool_table.add_column("Name", style="cyan")
    tool_table.add_column("Description", style="dim")
    for tool in tools[:20]:  # Show first 20 tools
        tool_table.add_row(tool.name, (tool.description or "")[:60])
    if len(tools) > 20:
        tool_table.add_row("...", f"({len(tools) - 20} more tools)")
    console.print(tool_table)
    console.print()

    # 5. Capabilities
    caps_table = Table(title="Capability status", show_header=False, box=None)
    caps_table.add_column("Capability", style="cyan")
    caps_table.add_column("Status", style="green")
    caps_table.add_row(
        "Memory",
        (
            "✓ enabled"
            if getattr(config, "memory_enabled", False)
            else "✗ disabled"
        ),
    )
    caps_table.add_row(
        "Session Persist",
        "✓ enabled" if config.session_persist else "✗ disabled",
    )
    caps_table.add_row(
        "Auto Approve",
        "✓ enabled" if config.auto_approve else "✗ disabled",
    )
    console.print(caps_table)
    console.print()

    # 6. Readiness assessment
    issues = []
    if not config.api_key:
        issues.append("API Key not configured")
    if not tools:
        issues.append("No tools available")

    if issues:
        console.print("[bold red]⚠ Readiness: Warning[/bold red]")
        for issue in issues:
            console.print(f"  • {issue}")
    else:
        console.print("[bold green]✓ Readiness: Ready[/bold green]")

    console.print(
        "\n[dim]This is a config preview; the agent was not started. "
        "Remove --dry-run to launch normally.[/dim]\n",
    )


def _run_tui_mode(config: Config):
    """Run AgenticX with Textual TUI (fixed input at bottom)."""
    _apply_custom_models(config)
    # Re-arm audit redaction from persisted config (restart must not drop it).
    from dashscope_agenticx.audit import configure_audit_logger

    configure_audit_logger(config)
    from dashscope_agenticx.debuglog import configure_debug_log

    configure_debug_log(config)
    from dashscope_agenticx.permission import configure_permission_policy

    configure_permission_policy()
    from dashscope_agenticx.extensions import apply_extensions

    _ext = apply_extensions(PROVIDER_MODELS)
    from dashscope_agenticx.cli.handlers_capability import (
        sync_extensions_into_catalog,
    )

    sync_extensions_into_catalog(_ext)

    from dashscope_agenticx.runtime import RuntimeBuilder

    runtime = RuntimeBuilder(
        config,
        extensions_prepared=True,
    ).build()
    try:
        with runtime.activate():
            _run_tui_session(config, runtime)
    finally:
        asyncio.run(runtime.aclose())


def _run_tui_session(config: Config, runtime):
    from dashscope_agenticx.session import set_session_manager

    session_manager = runtime.context.session_manager
    # Legacy slash-command handlers still resolve the process facade. Keep it
    # pointed at the Runtime-owned manager until those handlers accept context.
    set_session_manager(session_manager)

    agent = runtime.create_agent(
        system_prompt=_compose_system_prompt(
            getattr(config, "_embedded_system_prompt", None)
            or _load_system_prompt(),
        ),
        session_id=(
            session_manager.get_current_topic()
            if config.session_persist
            else None
        ),
    )

    setup_result = None
    if _needs_workspace_setup(config):
        setup_result = asyncio.run(_handle_setup(config, agent))
        if setup_result is not None and not setup_result.handled:
            return
    if setup_result is None:
        ensure_provider_key(config, agent)

    try:
        from dashscope_agenticx.ui.tui import run_tui
    except ImportError:
        console.print(
            "[yellow]TUI requires the textual dependency: "
            "pip install textual (or use CLI mode: --cli)[/yellow]",
        )
        return

    # Run Textual TUI
    input_history_path = (
        session_manager.get_input_history_path()
        if config.session_persist
        else None
    )
    run_tui(config, agent, input_history_path=input_history_path)
