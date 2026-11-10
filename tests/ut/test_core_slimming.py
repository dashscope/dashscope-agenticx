# -*- coding: utf-8 -*-
"""Phase 6 core dependency and import-boundary regressions."""
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from dashscope_agenticx.utils.toml import tomllib


def test_heavy_features_are_not_core_dependencies():
    root = Path(__file__).resolve().parents[2]
    data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
    dependencies = "\n".join(data["project"]["dependencies"]).lower()
    for package in ("textual", "anthropic", "openai", "ddgs", "playwright"):
        assert package not in dependencies
    optional = data["project"]["optional-dependencies"]
    assert optional["tui"]
    assert optional["provider-openai"]
    assert optional["provider-anthropic"]
    assert optional["web"]
    assert optional["browser"]


def test_importing_cli_does_not_import_optional_heavy_modules():
    root = Path(__file__).resolve().parents[2]
    script = (
        "import sys; import dashscope_agenticx.cli; "
        "mods=('textual','cv2','playwright','anthropic','openai','ddgs'); "
        "print(','.join(m for m in mods if m in sys.modules))"
    )
    completed = subprocess.run(
        [sys.executable, "-c", script],
        cwd=root,
        capture_output=True,
        text=True,
        check=True,
    )
    assert completed.stdout.strip() == ""
