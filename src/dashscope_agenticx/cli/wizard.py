"""Shared state for interactive configuration commands and setup."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal


class WizardCancelled(Exception):
    """The user stopped the interactive flow."""


@dataclass(frozen=True)
class WizardResult:
    status: Literal["completed", "skipped", "deferred", "cancelled", "failed"]
    name: str = ""

    @property
    def handled(self) -> bool:
        return self.status in ("completed", "skipped", "deferred")


@dataclass
class WizardContext:
    setup: bool = False
    prompted_credentials: set[str] = field(default_factory=set)
    deferred_credentials: set[str] = field(default_factory=set)
    credential_references: dict[str, str] = field(default_factory=dict)
    pending_capabilities: set[str] = field(default_factory=set)
    results: list[WizardResult] = field(default_factory=list)
    prompt_missing_credentials: bool = True


def read_choice(prompt: str, default: str = "") -> str:
    try:
        return input(prompt).strip() or default
    except (EOFError, KeyboardInterrupt) as error:
        raise WizardCancelled from error


def read_secret(prompt: str) -> str:
    import getpass

    try:
        return getpass.getpass(prompt).strip()
    except (EOFError, KeyboardInterrupt) as error:
        raise WizardCancelled from error


def valid_api_url(value: str) -> bool:
    from urllib.parse import urlsplit

    try:
        parsed = urlsplit(value)
        parsed.port
        return bool(
            parsed.scheme in ("http", "https") and parsed.hostname
            and not parsed.username and not parsed.password and not parsed.query
            and not parsed.fragment and not any(character.isspace() for character in value)
        )
    except ValueError:
        return False
