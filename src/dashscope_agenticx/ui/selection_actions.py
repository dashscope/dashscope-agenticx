# -*- coding: utf-8 -*-
"""Explicit selection actions that do not rely on terminal key forwarding."""

from rich.style import Style
from rich.text import Text
from textual import events
from textual.message import Message
from textual.widgets import Static


class SelectionActions(Static):
    can_focus = False

    class Requested(Message):
        def __init__(self, action: str, text: str):
            super().__init__()
            self.action = action
            self.text = text

    def __init__(self, **kwargs):
        super().__init__(**kwargs)
        self.selection_text = ""
        self.display = False

    def show_selection(self, text: str, shortcut: str) -> None:
        self.selection_text = text
        label = Text(f"Selected {len(text.splitlines())} lines · ")
        label.append(
            "Copy",
            style=Style(
                bold=True,
                underline=True,
                meta={"selection_action": "copy"},
            ),
        )
        label.append(" · ")
        label.append(
            "Quote",
            style=Style(
                bold=True,
                underline=True,
                meta={"selection_action": "quote"},
            ),
        )
        label.append(f" · {shortcut}")
        self.update(label)
        self.display = True

    def clear(self) -> None:
        self.selection_text = ""
        self.display = False

    def on_click(self, event: events.Click) -> None:
        action = (
            event.style.meta.get("selection_action") if event.style else None
        )
        if action and self.selection_text:
            event.prevent_default()
            event.stop()
            self.post_message(self.Requested(action, self.selection_text))
