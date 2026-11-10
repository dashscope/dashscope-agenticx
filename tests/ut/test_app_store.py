# -*- coding: utf-8 -*-
"""Phase 5 App Bundle store and CLI regressions."""
from __future__ import annotations

from pathlib import Path

import pytest

from dashscope_agenticx.apps import AppTrust, store
from dashscope_agenticx.cli.handlers_app import run_app_command


def _bundle(path: Path, *, shell: bool = False) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    (path / ".agenticx").mkdir(exist_ok=True)
    (path / ".agenticx" / "system-prompt.md").write_text(
        "You are an expert.",
        encoding="utf-8",
    )
    (path / "eval.json").write_text("{}", encoding="utf-8")
    (path / "acceptance.toml").write_text(
        'command = "python -m pytest"\n',
        encoding="utf-8",
    )
    (path / "app.toml").write_text(
        "[app]\n"
        'name = "sdk-expert"\n'
        'version = "1.0.0"\n'
        'publisher = "alibaba-cloud"\n'
        'profile = ".agenticx"\n'
        'eval = "eval.json"\n'
        'acceptance = "acceptance.toml"\n'
        "[runtime]\n"
        'requires = ">=1.0.0a1,<2"\n'
        "[permissions]\n"
        f"shell = {'true' if shell else 'false'}\n",
        encoding="utf-8",
    )
    return path


def test_install_list_info_verify_and_permission_diff(tmp_path):
    source = _bundle(tmp_path / "source")
    root = tmp_path / "apps"
    result = store.install(source.as_posix(), root=root)
    assert result.manifest.name == "sdk-expert"
    assert result.manifest.trust == AppTrust.COMMUNITY
    assert result.permission_changes["added"] == []
    assert store.list_apps(root)[0]["name"] == "sdk-expert"
    assert store.info("sdk-expert", root)["publisher"] == "alibaba-cloud"
    assert store.verify("sdk-expert", root)[0] is True

    _bundle(source, shell=True)
    updated = store.update("sdk-expert", root)
    assert updated.permission_changes["added"] == ["shell"]


def test_verify_detects_installed_bundle_tampering(tmp_path):
    source = _bundle(tmp_path / "source")
    root = tmp_path / "apps"
    store.install(source.as_posix(), root=root)
    (root / "sdk-expert" / ".agenticx" / "system-prompt.md").write_text(
        "tampered",
        encoding="utf-8",
    )
    valid, detail = store.verify("sdk-expert", root)
    assert valid is False
    assert "checksum mismatch" in detail


def test_install_rejects_incompatible_runtime(tmp_path):
    source = _bundle(tmp_path / "source")
    app_toml = source / "app.toml"
    app_toml.write_text(
        app_toml.read_text(encoding="utf-8").replace(
            ">=1.0.0a1,<2",
            ">=2",
        ),
        encoding="utf-8",
    )
    try:
        store.install(source.as_posix(), root=tmp_path / "apps")
    except RuntimeError as exc:
        assert "requires AgenticX" in str(exc)
    else:  # pragma: no cover
        raise AssertionError("incompatible App unexpectedly installed")


def test_app_cli_commands(tmp_path, capsys):
    source = _bundle(tmp_path / "source")
    root = tmp_path / "apps"
    assert run_app_command(["install", str(source)], root=root) == 0
    assert run_app_command(["list"], root=root) == 0
    assert run_app_command(["info", "sdk-expert"], root=root) == 0
    assert run_app_command(["verify", "sdk-expert"], root=root) == 0
    output = capsys.readouterr().out
    assert "sdk-expert" in output
    assert "Permission diff" in output


def test_bundled_official_app_installs_offline(tmp_path):
    result = store.install(
        "bundled:dashscope-sdk-expert",
        root=tmp_path / "apps",
    )
    assert result.manifest.trust == AppTrust.OFFICIAL
    assert result.manifest.publisher == "alibaba-cloud"
    assert store.verify("dashscope-sdk-expert", tmp_path / "apps")[0] is True


@pytest.mark.parametrize(
    "name",
    ["../untrusted", "/tmp/untrusted", "", "..\\untrusted", "C:untrusted"],
)
def test_bundled_prefix_cannot_elevate_external_app_trust(tmp_path, name):
    with pytest.raises(RuntimeError, match="Invalid App name"):
        store.install(f"bundled:{name}", root=tmp_path / "installed")
    assert not (tmp_path / "installed").exists()


def test_bundled_symlink_cannot_elevate_external_app_trust(
    tmp_path,
    monkeypatch,
):
    shipped = tmp_path / "shipped"
    shipped.mkdir()
    source = _bundle(tmp_path / "untrusted")
    (shipped / "untrusted").symlink_to(source, target_is_directory=True)
    monkeypatch.setattr(store, "bundled_apps_dir", lambda: shipped)
    with pytest.raises(RuntimeError, match="shipped catalog"):
        store.install("bundled:untrusted", root=tmp_path / "installed")


@pytest.mark.parametrize("field", ["profile", "eval", "acceptance"])
@pytest.mark.parametrize(
    "relative",
    ["../outside", "/tmp/outside", "..\\outside", "C:outside"],
)
def test_bundle_manifest_paths_cannot_escape(tmp_path, field, relative):
    source = _bundle(tmp_path / "source")
    manifest = source / "app.toml"
    original = {
        "profile": ".agenticx",
        "eval": "eval.json",
        "acceptance": "acceptance.toml",
    }[field]
    manifest.write_text(
        manifest.read_text().replace(
            f'{field} = "{original}"',
            f"{field} = '{relative}'",
        ),
        encoding="utf-8",
    )
    with pytest.raises(RuntimeError, match="escapes bundle"):
        store.install(str(source), root=tmp_path / "installed")
    assert not (tmp_path / "installed").exists()


def test_unknown_app_schema_is_rejected_before_install(tmp_path):
    source = _bundle(tmp_path / "source")
    manifest = source / "app.toml"
    manifest.write_text(
        manifest.read_text().replace("[app]", '[app]\nschema_version = "2"'),
    )
    with pytest.raises(ValueError, match="Unsupported App schema"):
        store.install(str(source), root=tmp_path / "installed")


@pytest.mark.parametrize("name", ["shell", "python_tools", "shell_hooks"])
@pytest.mark.parametrize("value", ['"false"', '"true"', "1", "[]"])
def test_non_boolean_app_permissions_are_rejected(tmp_path, name, value):
    source = _bundle(tmp_path / "source")
    manifest = source / "app.toml"
    manifest.write_text(
        manifest.read_text().replace("shell = false", f"{name} = {value}"),
    )
    destination = tmp_path / "apps"
    with pytest.raises(ValueError, match="must be a boolean"):
        store.install(str(source), root=destination)
    assert not destination.exists()


def test_permission_preview_can_cancel_before_any_files_are_written(tmp_path):
    source = _bundle(tmp_path / "source", shell=True)
    root = tmp_path / "installed"
    previews = []

    def reject(manifest, changes):
        previews.append((manifest.name, changes))
        assert not root.exists()
        return False

    with pytest.raises(RuntimeError, match="cancelled before writing"):
        store.install(str(source), root=root, confirm_permissions=reject)
    assert previews[0][1]["added"] == ["shell"]
    assert not root.exists()


def test_cli_permissions_default_to_refusing_install(
    tmp_path,
    monkeypatch,
    capsys,
):
    source = _bundle(tmp_path / "source", shell=True)
    monkeypatch.setattr("builtins.input", lambda prompt: "")
    assert (
        run_app_command(["install", str(source)], root=tmp_path / "installed")
        == 1
    )
    assert "Permission diff" in capsys.readouterr().out
    assert not (tmp_path / "installed").exists()
