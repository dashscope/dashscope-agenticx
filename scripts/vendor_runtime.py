# -*- coding: utf-8 -*-
"""Copy Runtime sources and commit metadata into a downstream SDK tree."""
from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("destination", type=Path)
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(root / "src"))
    source = root / "src" / "dashscope_agenticx"
    destination = args.destination.resolve()
    if destination.exists():
        shutil.rmtree(destination)
    shutil.copytree(
        source,
        destination,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    commit = subprocess.check_output(
        ["git", "rev-parse", "HEAD"],
        cwd=root,
        text=True,
    ).strip()
    from dashscope_agenticx._version import __version__

    (destination / "VENDORED_RUNTIME.json").write_text(
        json.dumps(
            {"version": __version__, "source_commit": commit},
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
