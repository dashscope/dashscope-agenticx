# -*- coding: utf-8 -*-
"""Versioned App manifest, checksum, trust, and permission primitives."""
from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Iterable

from dashscope_agenticx.utils import load_toml


class AppTrust(str, Enum):
    """Runtime trust assigned externally, never self-declared by an App."""

    UNTRUSTED = "untrusted"
    COMMUNITY = "community"
    VERIFIED = "verified"
    OFFICIAL = "official"

    @property
    def allows_code_execution(self) -> bool:
        return self in {AppTrust.VERIFIED, AppTrust.OFFICIAL}

    @classmethod
    def parse(cls, value: object) -> "AppTrust":
        if isinstance(value, cls):
            return value
        try:
            return cls(str(value))
        except ValueError:
            return cls.UNTRUSTED


@dataclass(frozen=True)
class AppPermissions:
    """Capabilities an App asks the host to grant."""

    python_tools: bool = False
    shell_hooks: bool = False
    shell: bool = False
    network: tuple[str, ...] = ()
    filesystem: tuple[str, ...] = ()

    @classmethod
    def from_mapping(cls, value: object) -> "AppPermissions":
        data = value if isinstance(value, dict) else {}
        for name in ("python_tools", "shell_hooks", "shell"):
            if name in data and not isinstance(data[name], bool):
                raise ValueError(f"App permission {name} must be a boolean")
        return cls(
            python_tools=bool(data.get("python_tools", False)),
            shell_hooks=bool(data.get("shell_hooks", False)),
            shell=bool(data.get("shell", False)),
            network=_string_tuple(data.get("network", ())),
            filesystem=_string_tuple(data.get("filesystem", ())),
        )

    def names(self) -> tuple[str, ...]:
        values: list[str] = []
        if self.python_tools:
            values.append("python_tools")
        if self.shell_hooks:
            values.append("shell_hooks")
        if self.shell:
            values.append("shell")
        values.extend(f"network:{item}" for item in self.network)
        values.extend(f"filesystem:{item}" for item in self.filesystem)
        return tuple(values)

    def as_dict(self) -> dict[str, object]:
        return {
            "python_tools": self.python_tools,
            "shell_hooks": self.shell_hooks,
            "shell": self.shell,
            "network": list(self.network),
            "filesystem": list(self.filesystem),
        }


@dataclass(frozen=True)
class AppManifest:
    """Declarative App identity and requested host capabilities."""

    name: str
    version: str
    description: str = ""
    publisher: str = ""
    schema_version: str = "1"
    permissions: AppPermissions = field(default_factory=AppPermissions)
    declared_checksum: str = ""
    checksum: str = ""
    trust: AppTrust = AppTrust.UNTRUSTED
    runtime_requires: str = ">=1.0.0,<2"
    profile: str = ".agenticx"
    eval_path: str = ""
    acceptance_path: str = ""

    @property
    def can_execute_python(self) -> bool:
        return (
            self.trust.allows_code_execution and self.permissions.python_tools
        )

    @property
    def can_execute_shell_hooks(self) -> bool:
        return (
            self.trust.allows_code_execution and self.permissions.shell_hooks
        )


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return (value,)
    if not isinstance(value, Iterable) or isinstance(value, dict):
        return ()
    return tuple(str(item) for item in value if str(item))


def _manifest_data(path: Path) -> dict[str, Any] | None:
    app_toml = path / "app.toml"
    if app_toml.is_file():
        return load_toml(app_toml)
    skill_toml = path / "skill.toml"
    if skill_toml.is_file():
        return load_toml(skill_toml)
    return None


def compute_app_checksum(path: Path) -> str:
    """Return a deterministic SHA-256 over regular App files.

    Symlinks are rejected because checksumming the link text while later
    importing its target would verify different bytes from those executed.
    Generated caches and install metadata are excluded from the identity.
    """
    root = path.resolve()
    if not root.is_dir():
        raise ValueError(f"App path is not a directory: {path}")
    digest = hashlib.sha256()
    excluded = {
        ".agenticx-skills.json",
        ".agenticcli-skills.json",
        "__pycache__",
        ".git",
    }
    for item in sorted(path.rglob("*"), key=lambda value: value.as_posix()):
        relative = item.relative_to(path)
        if any(part in excluded for part in relative.parts):
            continue
        if item.is_symlink():
            raise ValueError(f"App contains a symlink: {relative.as_posix()}")
        if not item.is_file():
            continue
        relative_text = relative.as_posix().encode("utf-8")
        payload = item.read_bytes()
        if relative.as_posix() in {"app.toml", "skill.toml"}:
            parsed = load_toml(item)
            if isinstance(parsed, dict):
                parsed.pop("checksum", None)
                app = parsed.get("app")
                if isinstance(app, dict):
                    app.pop("checksum", None)
                payload = json.dumps(
                    parsed,
                    ensure_ascii=False,
                    sort_keys=True,
                    separators=(",", ":"),
                ).encode("utf-8")
        digest.update(len(relative_text).to_bytes(8, "big"))
        digest.update(relative_text)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return f"sha256:{digest.hexdigest()}"


def load_app_manifest(
    path: Path,
    *,
    trust: AppTrust | str = AppTrust.UNTRUSTED,
    expected_checksum: str = "",
) -> AppManifest | None:
    """Load and verify an App manifest from a package directory."""
    data = _manifest_data(path)
    if not data:
        return None
    app = data.get("app") if isinstance(data.get("app"), dict) else data
    schema_version = str(app.get("schema_version", "1"))
    if schema_version != "1":
        raise ValueError(f"Unsupported App schema version: {schema_version}")
    permissions = AppPermissions.from_mapping(data.get("permissions"))
    checksum = compute_app_checksum(path)
    declared = str(app.get("checksum", data.get("checksum", ""))).strip()
    expected = expected_checksum.strip()
    for label, value in (("declared", declared), ("expected", expected)):
        if value and value != checksum:
            raise ValueError(
                f"App {label} checksum mismatch: expected {value}, "
                f"computed {checksum}",
            )
    name = str(app.get("name", path.name)).strip()
    if not name:
        raise ValueError("App manifest name must not be empty")
    return AppManifest(
        name=name,
        version=str(app.get("version", "0.0.1")),
        description=str(app.get("description", "")),
        publisher=str(app.get("publisher", "")),
        schema_version=schema_version,
        permissions=permissions,
        declared_checksum=declared,
        checksum=checksum,
        trust=AppTrust.parse(trust),
        runtime_requires=str(
            (data.get("runtime") or {}).get("requires", ">=1.0.0,<2")
            if isinstance(data.get("runtime"), dict)
            else ">=1.0.0,<2",
        ),
        profile=str(app.get("profile", ".agenticx")),
        eval_path=str(app.get("eval", "")),
        acceptance_path=str(app.get("acceptance", "")),
    )


def permission_diff(
    before: AppPermissions | None,
    after: AppPermissions,
) -> dict[str, list[str]]:
    """Return stable added/removed permission names for install prompts."""
    old = set(before.names() if before else ())
    new = set(after.names())
    return {
        "added": sorted(new - old),
        "removed": sorted(old - new),
    }


def manifest_fingerprint(manifest: AppManifest) -> str:
    """Stable identity for audit and registry metadata."""
    payload = {
        "name": manifest.name,
        "version": manifest.version,
        "publisher": manifest.publisher,
        "checksum": manifest.checksum,
        "permissions": manifest.permissions.as_dict(),
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True).encode("utf-8"),
    ).hexdigest()
