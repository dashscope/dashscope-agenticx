# -*- coding: utf-8 -*-
"""Install, inspect, update, and verify versioned AgenticX Apps."""
from __future__ import annotations

import json
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from packaging.specifiers import InvalidSpecifier, SpecifierSet
from packaging.version import InvalidVersion, Version

from dashscope_agenticx import __version__
from dashscope_agenticx.apps.manifest import (
    AppManifest,
    AppPermissions,
    AppTrust,
    compute_app_checksum,
    load_app_manifest,
    permission_diff,
)
from dashscope_agenticx.config import WORKSPACE_DIR
from dashscope_agenticx.utils.paths import atomic_write_text

REGISTRY_FILE = ".agenticx-apps.json"
LEGACY_REGISTRY_FILE = ".agenticcli-apps.json"


@dataclass(frozen=True)
class AppInstallResult:
    manifest: AppManifest
    permission_changes: dict[str, list[str]]
    path: Path


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def apps_dir() -> Path:
    return WORKSPACE_DIR / "apps"


def bundled_apps_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "bundled_apps"


def _registry_path(root: Path) -> Path:
    return root / REGISTRY_FILE


def load_registry(root: Path | None = None) -> dict[str, Any]:
    root = root or apps_dir()
    path = _registry_path(root)
    if not path.is_file():
        legacy_path = root / LEGACY_REGISTRY_FILE
        if not legacy_path.is_file():
            return {}
        path = legacy_path
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, json.JSONDecodeError):
        return {}


def save_registry(root: Path, registry: dict[str, Any]) -> None:
    root.mkdir(parents=True, exist_ok=True)
    atomic_write_text(
        _registry_path(root),
        json.dumps(registry, ensure_ascii=False, indent=2) + "\n",
    )


def runtime_compatible(
    manifest: AppManifest,
    version: str = __version__,
) -> bool:
    try:
        return Version(version) in SpecifierSet(manifest.runtime_requires)
    except (InvalidSpecifier, InvalidVersion):
        return False


def _safe_name(name: str) -> str:
    if (
        not name
        or any(separator in name for separator in ("/", "\\", ":"))
        or ".." in name
        or Path(name).is_absolute()
    ):
        raise RuntimeError(f"Invalid App name: {name}")
    return name


def _copy_bundle(source: Path, destination: Path) -> None:
    for item in source.rglob("*"):
        if item.is_symlink():
            raise RuntimeError(
                f"App bundle contains a symlink: "
                f"{item.relative_to(source).as_posix()}",
            )
    if destination.exists() or destination.is_symlink():
        if destination.is_symlink() or destination.is_file():
            destination.unlink()
        else:
            shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns(".git", "__pycache__", "*.pyc"),
    )


def _validate_bundle(path: Path, manifest: AppManifest) -> None:
    root = path.resolve()
    for relative in (
        manifest.profile,
        manifest.eval_path,
        manifest.acceptance_path,
    ):
        if not relative:
            continue
        target = (root / relative).resolve()
        if (
            Path(relative).is_absolute()
            or "\\" in relative
            or ":" in relative
            or ".." in Path(relative).parts
            or root not in target.parents
        ):
            raise RuntimeError(f"App path escapes bundle: {relative}")
    profile = path / manifest.profile
    if not profile.is_dir():
        raise RuntimeError(
            f"App profile directory does not exist: {manifest.profile}",
        )
    for label, relative in (
        ("eval", manifest.eval_path),
        ("acceptance", manifest.acceptance_path),
    ):
        if relative and not (path / relative).is_file():
            raise RuntimeError(f"App {label} file does not exist: {relative}")


def install(
    source: str,
    *,
    root: Path | None = None,
    trust: AppTrust | str = AppTrust.COMMUNITY,
    expected_checksum: str = "",
    confirm_permissions: Callable[[AppManifest, dict], bool] | None = None,
) -> AppInstallResult:
    if source.startswith("bundled:"):
        name = _safe_name(source.split(":", 1)[1])
        bundled_root = bundled_apps_dir().resolve()
        source_path = (bundled_root / name).resolve()
        if source_path.parent != bundled_root:
            raise RuntimeError(
                "Bundled App must be inside the shipped catalog",
            )
        trust = AppTrust.OFFICIAL
    else:
        source_path = Path(source).expanduser().resolve()
    manifest = load_app_manifest(
        source_path,
        trust=trust,
        expected_checksum=expected_checksum,
    )
    if manifest is None or not (source_path / "app.toml").is_file():
        raise RuntimeError(f"App bundle is missing app.toml: {source_path}")
    _validate_bundle(source_path, manifest)
    if not runtime_compatible(manifest):
        raise RuntimeError(
            f"App {manifest.name} requires AgenticX "
            f"{manifest.runtime_requires}; current version is {__version__}",
        )
    root = root or apps_dir()
    name = _safe_name(manifest.name)
    registry = load_registry(root)
    previous = registry.get(name, {})
    before = AppPermissions.from_mapping(
        previous.get("permissions") if isinstance(previous, dict) else None,
    )
    changes = permission_diff(
        before if previous else None,
        manifest.permissions,
    )
    if confirm_permissions is not None and not confirm_permissions(
        manifest,
        changes,
    ):
        raise RuntimeError("App installation cancelled before writing files")
    destination = root / name
    root.mkdir(parents=True, exist_ok=True)
    _copy_bundle(source_path, destination)
    installed = load_app_manifest(
        destination,
        trust=trust,
        expected_checksum=manifest.checksum,
    )
    assert installed is not None
    _validate_bundle(destination, installed)
    registry[name] = {
        "name": name,
        "version": installed.version,
        "source": str(source_path),
        "publisher": installed.publisher,
        "checksum": installed.checksum,
        "trust": installed.trust.value,
        "permissions": installed.permissions.as_dict(),
        "runtime_requires": installed.runtime_requires,
        "permission_diff": changes,
        "installed_at": (
            previous.get("installed_at", _now())
            if isinstance(previous, dict)
            else _now()
        ),
        "updated_at": _now(),
    }
    save_registry(root, registry)
    return AppInstallResult(installed, changes, destination)


def list_apps(root: Path | None = None) -> list[dict[str, Any]]:
    registry = load_registry(root or apps_dir())
    return [registry[name] for name in sorted(registry)]


def info(name: str, root: Path | None = None) -> dict[str, Any] | None:
    return load_registry(root or apps_dir()).get(_safe_name(name))


def verify(name: str, root: Path | None = None) -> tuple[bool, str]:
    root = root or apps_dir()
    entry = info(name, root)
    if not isinstance(entry, dict):
        return False, f"App not installed: {name}"
    path = root / _safe_name(name)
    try:
        checksum = compute_app_checksum(path)
    except (OSError, ValueError) as exc:
        return False, str(exc)
    expected = str(entry.get("checksum", ""))
    if checksum != expected:
        return False, f"checksum mismatch: expected {expected}, got {checksum}"
    manifest = load_app_manifest(
        path,
        trust=entry.get("trust", AppTrust.UNTRUSTED.value),
        expected_checksum=expected,
    )
    if manifest is None or not runtime_compatible(manifest):
        return False, "App manifest or runtime compatibility check failed"
    return True, checksum


def update(
    name: str,
    root: Path | None = None,
    *,
    confirm_permissions: Callable[[AppManifest, dict], bool] | None = None,
) -> AppInstallResult:
    root = root or apps_dir()
    entry = info(name, root)
    if not isinstance(entry, dict):
        raise RuntimeError(f"App not installed: {name}")
    source = str(entry.get("source", ""))
    if not source:
        raise RuntimeError(f"App source is unavailable: {name}")
    return install(
        source,
        root=root,
        # Mutable source updates require a fresh trust decision.
        trust=AppTrust.COMMUNITY,
        confirm_permissions=confirm_permissions,
    )
