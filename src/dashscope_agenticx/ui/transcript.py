# -*- coding: utf-8 -*-
"""Structured transcript blocks shared by streamed replies and details."""

from __future__ import annotations

from dataclasses import dataclass, field

from rich.markdown import Markdown
from rich.rule import Rule
from rich.style import Style
from rich.table import Table
from rich.text import Text


@dataclass
class OutputBlock:
    key: int = field(default=0, init=False)
    start_line: int = field(default=0, init=False)
    line_count: int = field(default=0, init=False)


@dataclass
class MarkdownOutput(OutputBlock):
    text: str
    syntax_theme: str = "ansi_dark"

    def __str__(self) -> str:
        return self.text

    def __rich_console__(self, _console, _options):
        yield Text("Assistant", style="bold")
        yield Markdown(
            self.text,
            code_theme=self.syntax_theme,
            inline_code_theme=self.syntax_theme,
        )


@dataclass
class TurnHeader(OutputBlock):
    number: int
    user_input: str
    model: str
    status: str = "Running"
    style: str = "dim"
    prompt: str = "agenticx> "

    def __rich_console__(self, _console, _options):
        title = f"Turn {self.number} · {self.status}"
        if self.model:
            title += " · " + self.model
        yield Rule(title, align="left", style=self.style)
        if self.user_input:
            yield Text(self.prompt, style="bold green") + Text(self.user_input)


@dataclass
class TurnStatistics(OutputBlock):
    number: int
    summary: str
    rows: list[tuple[str, str, str]]
    expanded: bool = False
    style: str = "dim"
    notes: list[str] = field(default_factory=list)

    def __rich_console__(self, _console, _options):
        header = Text(
            ("▼ " if self.expanded else "▶ ") + self.summary,
            style=self.style,
            no_wrap=True,
            overflow="ellipsis",
        )
        header.stylize(Style(meta={"statistics_output": self.key}))
        yield header
        if not self.expanded:
            return
        yield Text(f"Turn {self.number} statistics", style="bold")
        table = Table(
            box=None,
            show_edge=False,
            padding=(0, 1),
            expand=True,
            header_style="bold",
        )
        table.add_column("Metric", ratio=2)
        table.add_column("This turn", ratio=1, justify="right")
        table.add_column("Session", ratio=1, justify="right")
        for metric, turn, session in self.rows:
            table.add_row(Text(metric), Text(turn), Text(session))
        yield table
        for note in self.notes:
            yield Text(note, style=self.style)
