# -*- coding: utf-8 -*-
"""Structured, collapsible tool output rendered inside the transcript."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from rich.style import Style
from rich.syntax import Syntax
from rich.text import Text

from dashscope_agenticx.ui.transcript import OutputBlock


@dataclass
class ToolOutput(OutputBlock):
    name: str
    result: str
    arguments: dict[str, Any] = field(default_factory=dict)
    success: bool | None = None
    duration_ms: int | None = None
    style: str = "dim cyan"
    syntax_theme: str = "ansi_dark"
    expanded: bool = False
    result_truncated: bool = False
    arguments_provided: bool = False

    def header(self) -> Text:
        header = Text(style=self.style, no_wrap=True, overflow="ellipsis")
        header.append("▼ " if self.expanded else "▶ ", style="bold")
        if self.success is not None:
            header.append(
                "✓ " if self.success else "✗ ",
                style="green" if self.success else "red",
            )
        header.append(self.name, style="bold")
        target = next(
            (
                self.arguments[key]
                for key in ("path", "file_path", "command", "query", "url")
                if self.arguments.get(key)
            ),
            "",
        )
        preview = next(
            (
                line.strip()
                for line in self.result.splitlines()
                if line.strip()
            ),
            "No output",
        )
        if target:
            header.append(" · " + " ".join(str(target).split()))
        if not target or self.success is False:
            header.append(" · " + preview)
        if self.duration_ms is not None:
            header.append(f" · {self.duration_ms / 1000:.1f}s")
        if self.result_truncated:
            header.append(" · preview")
        header.stylize(Style(meta={"tool_output": self.key}))
        return header

    def __rich_console__(self, _console, _options):
        yield self.header()
        if not self.expanded:
            return
        if self.arguments or self.arguments_provided:
            yield Text("  Arguments", style=self.style)
            yield Syntax(
                json.dumps(self.arguments, ensure_ascii=False, indent=2),
                "json",
                theme=self.syntax_theme,
                background_color="default",
                word_wrap=True,
            )
        label = "  Output"
        if self.result_truncated:
            label += " (truncated preview)"
        yield Text(label, style=self.style)
        if "--- diff ---" in self.result:
            head, _, diff = self.result.partition("--- diff ---")
            yield Text(head.rstrip(), style=self.style)
            yield Syntax(
                diff.strip("\n"),
                "diff",
                theme=self.syntax_theme,
                background_color="default",
                word_wrap=True,
            )
        elif "diff --git" in self.result or self.result.startswith("@@"):
            yield Syntax(
                self.result,
                "diff",
                theme=self.syntax_theme,
                background_color="default",
                word_wrap=True,
            )
        else:
            yield Text(self.result or "(empty)", style=self.style)
