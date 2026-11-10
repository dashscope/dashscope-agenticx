# -*- coding: utf-8 -*-
"""App Bundle commands shared by the standalone CLI and interactive REPL."""
from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console

from dashscope_agenticx.apps import AppTrust, store
from dashscope_agenticx.cli.command_registry import (
    registry as _command_registry,
)

console = Console()


def _print_entry(entry: dict) -> None:
    console.print(
        f"[bold]{entry.get('name')}[/bold] v{entry.get('version')} "
        f"[dim]({entry.get('trust', 'untrusted')})[/dim]",
    )
    console.print(f"  Publisher: {entry.get('publisher') or '(unknown)'}")
    console.print(
        f"  Runtime:   {entry.get('runtime_requires', '(unspecified)')}",
    )
    permissions = entry.get("permissions", {})
    requested = []
    if isinstance(permissions, dict):
        requested = [
            name
            for name, value in permissions.items()
            if value not in (False, [], "", None)
        ]
    console.print(
        f"  Permissions: {', '.join(requested) if requested else '(none)'}",
    )
    console.print(f"  Checksum:  {entry.get('checksum', '')}")


def _confirm_permissions(manifest, changes):
    console.print(f"App: {manifest.name} v{manifest.version}")
    console.print(
        "  Permission diff: " + json.dumps(changes, ensure_ascii=False),
    )
    if not changes["added"]:
        return True
    from dashscope_agenticx.cli.wizard import read_choice

    return read_choice(
        "Install with these permissions? [y/N]: ",
        "n",
    ).lower() in {"y", "yes"}


# pylint: disable=too-many-return-statements,too-many-branches
def run_app_command(args: list[str], *, root: Path | None = None) -> int:
    """Run ``agenticx app ...`` and return a process-style status code."""
    sub = args[0] if args else "list"
    if sub == "list":
        entries = store.list_apps(root)
        if not entries:
            console.print("[dim]No Apps installed[/dim]")
            return 0
        for entry in entries:
            _print_entry(entry)
        return 0
    if sub == "info" and len(args) >= 2:
        entry = store.info(args[1], root)
        if entry is None:
            console.print(f"[red]App not installed: {args[1]}[/red]")
            return 1
        _print_entry(entry)
        return 0
    if sub == "install" and len(args) >= 2:
        trust = AppTrust.COMMUNITY
        checksum = ""
        if "--verified" in args:
            trust = AppTrust.VERIFIED
        if "--official" in args:
            trust = AppTrust.OFFICIAL
        if "--checksum" in args:
            index = args.index("--checksum")
            if index + 1 >= len(args):
                console.print("[red]--checksum requires a value[/red]")
                return 2
            checksum = args[index + 1]
        try:
            result = store.install(
                args[1],
                root=root,
                trust=trust,
                expected_checksum=checksum,
                confirm_permissions=_confirm_permissions,
            )
        except Exception as exc:
            console.print(f"[red]App install failed: {exc}[/red]")
            return 1
        console.print(
            f"[green]✓ Installed App {result.manifest.name} "
            f"v{result.manifest.version}[/green]",
        )
        return 0
    if sub == "update" and len(args) >= 2:
        try:
            result = store.update(
                args[1],
                root,
                confirm_permissions=_confirm_permissions,
            )
        except Exception as exc:
            console.print(f"[red]App update failed: {exc}[/red]")
            return 1
        console.print(
            f"[green]✓ Updated App {result.manifest.name}[/green]",
        )
        return 0
    if sub == "verify" and len(args) >= 2:
        valid, detail = store.verify(args[1], root)
        color = "green" if valid else "red"
        marker = "✓" if valid else "✗"
        console.print(f"[{color}]{marker} {detail}[/{color}]")
        return 0 if valid else 1
    console.print(
        "Usage: agenticx app list|info <name>|install <path> "
        "[--verified|--official] [--checksum SHA256]|update <name>|"
        "verify <name>",
    )
    return 2


def handle_app_slash(cmd: str) -> bool:
    parts = cmd.strip().split()
    run_app_command(parts[1:])
    return True


def _registered_handler(context) -> bool:
    return handle_app_slash(context.command)


_command_registry.register("/app", _registered_handler)
