# -*- coding: utf-8 -*-
"""Small explicit registry for slash-command dispatch."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable


@dataclass(frozen=True)
class CommandContext:
    command: str
    agent: object
    config: object


CommandHandler = Callable[[CommandContext], bool | str]


class CommandRegistry:
    def __init__(self):
        self._handlers: dict[str, CommandHandler] = {}

    def register(self, name: str, handler: CommandHandler) -> None:
        if not name.startswith("/"):
            raise ValueError("slash command names must start with '/'")
        self._handlers[name] = handler

    def handles(self, command: str) -> bool:
        return command.split(maxsplit=1)[0] in self._handlers

    def dispatch(self, context: CommandContext) -> bool | str:
        name = context.command.split(maxsplit=1)[0]
        return self._handlers[name](context)


registry = CommandRegistry()
