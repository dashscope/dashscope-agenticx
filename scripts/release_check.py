# -*- coding: utf-8 -*-
"""Offline release-contract smoke checks."""
# pylint: disable=wrong-import-position
from __future__ import annotations

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))

from dashscope_agenticx import __version__  # noqa: E402
from dashscope_agenticx.apps.store import bundled_apps_dir  # noqa: E402


def main() -> int:
    from dashscope_agenticx.utils.toml import load_toml

    pyproject = load_toml(ROOT / "pyproject.toml")
    assert pyproject is not None
    assert pyproject["project"]["name"] == "dashscope-agenticx"
    assert "version" in pyproject["project"]["dynamic"]
    assert pyproject["project"]["scripts"] == {
        "agenticx": "dashscope_agenticx.cli:main",
    }
    assert __version__ == "1.0.1"
    assert (SRC / "dashscope_agenticx" / "py.typed").is_file()
    for legacy in ("agenticx", "agenticcli", "acli"):
        assert not (SRC / legacy).exists()
    for name in ("dashscope-sdk-expert", "bailian-expert"):
        bundle = bundled_apps_dir() / name
        assert (bundle / "app.toml").is_file()
        manifest = load_toml(bundle / "app.toml")
        assert manifest is not None
        assert manifest["runtime"]["requires"] == ">=1.0.0,<2"
        assert (bundle / ".agenticx").is_dir()
    output = subprocess.check_output(
        [sys.executable, "-m", "dashscope_agenticx", "--version"],
        cwd=ROOT,
        text=True,
    )
    assert __version__ in output
    import dashscope_agenticx
    import dashscope_agenticx.sdk

    assert dashscope_agenticx.__version__ == __version__
    print(f"release contract ok: {__version__}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
