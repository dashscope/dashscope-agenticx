# -*- coding: utf-8 -*-
"""Phase 3 App manifest and execution-trust regressions."""
from __future__ import annotations

import io
import tarfile
from pathlib import Path

import pytest

from dashscope_agenticx.apps import (
    AppPermissions,
    AppTrust,
    compute_app_checksum,
    load_app_manifest,
    permission_diff,
)
from dashscope_agenticx.hooks import HookBus
from dashscope_agenticx.skills import store
from dashscope_agenticx.skills.package import (
    discover_skill_packages,
    load_skill_package,
    register_skill_package,
)
from dashscope_agenticx.tools.registry import ToolRegistry


def _make_app(path: Path, permissions: str = "") -> Path:
    path.mkdir(parents=True)
    (path / "skill.toml").write_text(
        'name = "secure-app"\n'
        'version = "1.0.0"\n'
        'publisher = "example.publisher"\n'
        f"{permissions}",
        encoding="utf-8",
    )
    (path / "prompt.md").write_text("Use secure mode.", encoding="utf-8")
    return path


def test_manifest_checksum_is_stable_and_detects_tampering(tmp_path):
    app_dir = _make_app(tmp_path / "app")
    checksum = compute_app_checksum(app_dir)
    with (app_dir / "skill.toml").open("a", encoding="utf-8") as stream:
        stream.write(f'checksum = "{checksum}"\n')

    manifest = load_app_manifest(app_dir)
    assert manifest is not None
    assert manifest.runtime_requires == ">=1.0.0,<2"
    assert manifest.checksum == checksum
    assert manifest.declared_checksum == checksum

    (app_dir / "prompt.md").write_text("tampered", encoding="utf-8")
    with pytest.raises(ValueError, match="checksum mismatch"):
        load_app_manifest(app_dir)


def test_checksum_ignores_current_and_legacy_skill_metadata(tmp_path):
    app_dir = _make_app(tmp_path / "app")
    checksum = compute_app_checksum(app_dir)

    (app_dir / ".agenticx-skills.json").write_text('{"current": true}')
    (app_dir / ".agenticcli-skills.json").write_text('{"legacy": true}')

    assert compute_app_checksum(app_dir) == checksum


def test_official_app_requires_matching_external_checksum(tmp_path):
    app_dir = _make_app(
        tmp_path / "app",
        "[permissions]\npython_tools = true\n",
    )
    checksum = compute_app_checksum(app_dir)
    manifest = load_app_manifest(
        app_dir,
        trust=AppTrust.OFFICIAL,
        expected_checksum=checksum,
    )
    assert manifest is not None
    assert manifest.trust == AppTrust.OFFICIAL
    assert manifest.can_execute_python is True

    (app_dir / "prompt.md").write_text("changed", encoding="utf-8")
    with pytest.raises(ValueError, match="expected checksum mismatch"):
        load_app_manifest(
            app_dir,
            trust=AppTrust.OFFICIAL,
            expected_checksum=checksum,
        )


def test_manifest_rejects_symlinks(tmp_path):
    app_dir = _make_app(tmp_path / "app")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (app_dir / "linked.txt").symlink_to(outside)
    with pytest.raises(ValueError, match="symlink"):
        compute_app_checksum(app_dir)


def test_local_install_rejects_source_symlink(tmp_path):
    app_dir = _make_app(tmp_path / "app")
    outside = tmp_path / "outside.txt"
    outside.write_text("secret", encoding="utf-8")
    (app_dir / "linked.txt").symlink_to(outside)
    with pytest.raises(RuntimeError, match="contains a symlink"):
        store.install(str(app_dir), skills_dir=tmp_path / "installed")


def test_archive_install_rejects_internal_symlink(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        manifest = b'name = "secure-app"\nversion = "1"\n'
        info = tarfile.TarInfo("secure-app/skill.toml")
        info.size = len(manifest)
        archive.addfile(info, io.BytesIO(manifest))
        link = tarfile.TarInfo("secure-app/link")
        link.type = tarfile.SYMTYPE
        link.linkname = "skill.toml"
        archive.addfile(link)

    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: io.BytesIO(buffer.getvalue()),
    )
    with pytest.raises(RuntimeError, match="links are not allowed"):
        store.install(
            "https://example.com/secure-app.tar.gz",
            skills_dir=tmp_path / "installed",
        )


def test_archive_install_rejects_special_member(tmp_path, monkeypatch):
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
        directory = tarfile.TarInfo("secure-app")
        directory.type = tarfile.DIRTYPE
        archive.addfile(directory)
        fifo = tarfile.TarInfo("secure-app/pipe")
        fifo.type = tarfile.FIFOTYPE
        archive.addfile(fifo)

    import urllib.request

    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda url, timeout=None: io.BytesIO(buffer.getvalue()),
    )
    with pytest.raises(RuntimeError, match="type is not allowed"):
        store.install(
            "https://example.com/secure-app.tar.gz",
            skills_dir=tmp_path / "installed",
        )


def test_permission_diff_is_stable():
    before = AppPermissions(network=("api.old",))
    after = AppPermissions(
        python_tools=True,
        network=("api.new",),
    )
    assert permission_diff(before, after) == {
        "added": ["network:api.new", "python_tools"],
        "removed": ["network:api.old"],
    }


def test_untrusted_app_cannot_import_tools_py(tmp_path):
    app_dir = _make_app(
        tmp_path / "app",
        "[permissions]\npython_tools = true\n",
    )
    marker = tmp_path / "executed"
    (app_dir / "tools.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n",
        encoding="utf-8",
    )
    pkg = load_skill_package(app_dir, trust=AppTrust.COMMUNITY)
    assert pkg is not None

    with pytest.warns(UserWarning, match="tools.py ignored"):
        register_skill_package(
            pkg,
            ToolRegistry(),
            HookBus(hooks_file=tmp_path / "none.toml"),
        )
    assert not marker.exists()


def test_verified_app_requires_explicit_python_permission(tmp_path):
    app_dir = _make_app(tmp_path / "app")
    marker = tmp_path / "executed"
    (app_dir / "tools.py").write_text(
        f"from pathlib import Path\nPath({str(marker)!r}).write_text('yes')\n",
        encoding="utf-8",
    )
    pkg = load_skill_package(app_dir, trust=AppTrust.VERIFIED)
    assert pkg is not None
    with pytest.warns(UserWarning, match="tools.py ignored"):
        register_skill_package(
            pkg,
            ToolRegistry(),
            HookBus(hooks_file=tmp_path / "none.toml"),
        )
    assert not marker.exists()


def test_verified_app_with_permission_can_register_tools(tmp_path):
    app_dir = _make_app(
        tmp_path / "app",
        "[permissions]\npython_tools = true\n",
    )
    (app_dir / "tools.py").write_text(
        "def register(registry):\n"
        "    registry.register_mcp_tool(\n"
        "        'hello', 'hello', {'type': 'object', 'properties': {}},\n"
        "        lambda: 'ok',\n"
        "    )\n",
        encoding="utf-8",
    )
    pkg = load_skill_package(app_dir, trust=AppTrust.VERIFIED)
    assert pkg is not None
    registry = ToolRegistry()
    register_skill_package(
        pkg,
        registry,
        HookBus(hooks_file=tmp_path / "none.toml"),
    )
    assert registry.get("secure-app_hello") is not None


def test_shell_hook_requires_trust_and_explicit_permission(tmp_path):
    app_dir = _make_app(tmp_path / "app")
    (app_dir / "hooks.toml").write_text(
        '[[hooks.on_message]]\naction = "run"\nrun = "echo unsafe"\n',
        encoding="utf-8",
    )
    pkg = load_skill_package(app_dir, trust=AppTrust.VERIFIED)
    assert pkg is not None
    bus = HookBus(hooks_file=tmp_path / "none.toml")
    with pytest.warns(UserWarning, match="shell hook ignored"):
        register_skill_package(pkg, ToolRegistry(), bus)
    assert bus.hooks == []


def test_registry_checksum_grants_verified_install_metadata(tmp_path):
    source = _make_app(
        tmp_path / "source",
        "[permissions]\npython_tools = true\n",
    )
    (source / "tools.py").write_text(
        "def register(registry):\n"
        "    registry.register_mcp_tool(\n"
        "        'hello', 'hello', {'type': 'object', 'properties': {}},\n"
        "        lambda: 'ok',\n"
        "    )\n",
        encoding="utf-8",
    )
    checksum = compute_app_checksum(source)
    index = tmp_path / "registry.toml"
    index.write_text(
        "[[skills]]\n"
        'name = "secure-app"\n'
        'version = "1.0.0"\n'
        f'source = "{source}"\n'
        f'checksum = "{checksum}"\n',
        encoding="utf-8",
    )
    target = tmp_path / "installed"

    store.install(
        "secure-app",
        skills_dir=target,
        registry_url=str(index),
    )
    entry = store.load_manifest(target)["secure-app"]
    assert entry["checksum"] == checksum
    assert entry["trust"] == AppTrust.VERIFIED.value
    assert entry["permission_diff"]["added"] == ["python_tools"]

    packages = discover_skill_packages(extra_dirs=[target])
    installed = next(pkg for pkg in packages if pkg.name == "secure-app")
    assert installed.app_manifest is not None
    assert installed.app_manifest.trust == AppTrust.VERIFIED
    registry = ToolRegistry()
    register_skill_package(
        installed,
        registry,
        HookBus(hooks_file=tmp_path / "none.toml"),
    )
    assert registry.get("secure-app_hello") is not None
