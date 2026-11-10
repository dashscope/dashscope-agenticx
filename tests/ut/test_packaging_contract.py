# -*- coding: utf-8 -*-
"""Phase 8 distribution, namespace, typing, and version contracts."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from packaging.requirements import Requirement
from packaging.version import Version

from dashscope_agenticx.utils.toml import tomllib


def test_security_dependency_floors_and_python_compatibility():
    root = Path(__file__).resolve().parents[2]
    project = tomllib.loads((root / "pyproject.toml").read_text())["project"]
    requirements = {
        Requirement(value).name.lower(): Requirement(value)
        for value in project["dependencies"]
    }
    assert Version("2026.7.22") not in requirements["certifi"].specifier
    extras = project["optional-dependencies"]
    for extra in ("voice", "media", "all"):
        optional = {
            Requirement(value).name.lower(): Requirement(value)
            for value in extras[extra]
        }
        for name, affected in (
            ("aiohttp", "3.14.3"),
            ("multidict", "6.7.1"),
            ("urllib3", "2.7.0"),
        ):
            assert Version(affected) not in optional[name].specifier
    pillow = next(
        Requirement(value)
        for value in extras["all"]
        if Requirement(value).name.lower() == "pillow"
    )
    assert Version("12.3.0") not in pillow.specifier
    assert not pillow.marker.evaluate({"python_version": "3.10"})
    assert pillow.marker.evaluate({"python_version": "3.11"})


def test_security_job_installs_built_runtime_before_auditing():
    import yaml

    root = Path(__file__).resolve().parents[2]
    workflow = yaml.safe_load(
        (root / ".github/workflows/test.yml").read_text(),
    )
    job = workflow["jobs"]["package-and-security"]
    assert job["strategy"]["matrix"]["scope"] == ["base", "all"]
    commands = [step.get("run", "") for step in job["steps"]]
    installation = next(
        index
        for index, command in enumerate(commands)
        if '"${wheel}${extra}"' in command
    )
    audit = next(
        index
        for index, command in enumerate(commands)
        if 'pip-audit --path "$packages"' in command
    )
    assert installation < audit
    assert "audit-env/bin/python" in commands[installation]
    assert 'install --upgrade "pip>=26.2"' in commands[installation]
    assert "pip-audit" not in commands[installation]
    assert 'pip-audit --path "$packages"' in commands[audit]


def test_distribution_namespace_scripts_and_version_are_aligned():
    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    assert data["project"]["name"] == "dashscope-agenticx"
    assert data["project"]["dynamic"] == ["version"]
    assert data["tool"]["setuptools"]["dynamic"]["version"] == {
        "attr": "dashscope_agenticx._version.__version__",
    }
    assert data["project"]["scripts"] == {
        "agenticx": "dashscope_agenticx.cli:main",
    }
    assert (root / "src" / "dashscope_agenticx" / "py.typed").is_file()
    assert not (root / "src" / "agenticcli").exists()
    assert not list((root / "src" / "acli").glob("*.py"))


def test_bundled_agenticx_profiles_are_package_data():
    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    patterns = data["tool"]["setuptools"]["package-data"]["dashscope_agenticx"]
    assert "bundled_apps/*/.agenticx/*.md" in patterns
    assert not any(".agenticcli" in pattern for pattern in patterns)

    bundled = root / "src" / "dashscope_agenticx" / "bundled_apps"
    assert list(bundled.glob("*/.agenticx/*.md"))
    assert not list(bundled.glob("*/.agenticcli/*"))


def test_canonical_namespace_exposes_stable_sdk_contract():
    import dashscope_agenticx
    from dashscope_agenticx import sdk

    assert dashscope_agenticx.__version__ == "1.0.0"
    assert sdk.RuntimeBuilder is not None
    assert sdk.AgentEventEnvelope is not None


def test_source_manifest_includes_public_docs_and_excludes_internal_md():
    root = Path(__file__).resolve().parents[2]
    directives = (
        (root / "MANIFEST.in").read_text(encoding="utf-8").splitlines()
    )
    for name in (
        "README.md",
        "README_zh.md",
        "logo.svg",
        "user-guide.md",
        "user-guide_zh.md",
        "migration-to-1.0.md",
        "CHANGELOG.md",
    ):
        assert (root / name).is_file()
        assert f"include {name}" in directives
    assert "prune md" in directives
    assert "prune bench" in directives
    assert not any(
        line.startswith("recursive-include md ") for line in directives
    )
    assert not any(
        line.startswith("recursive-include bench ") for line in directives
    )


def test_offline_release_check_passes():
    root = Path(__file__).resolve().parents[2]
    completed = subprocess.run(
        [sys.executable, "scripts/release_check.py"],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "release contract ok: 1.0.0" in completed.stdout
