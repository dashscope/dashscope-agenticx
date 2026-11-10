# -*- coding: utf-8 -*-
"""Measure import/startup and artifact-size baselines for release reports."""
from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path


def _timed(command: list[str], cwd: Path) -> float:
    start = time.perf_counter()
    subprocess.run(command, cwd=cwd, check=True, capture_output=True)
    return time.perf_counter() - start


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    result = {
        "python": sys.version.split()[0],
        "import_dashscope_agenticx_ms": round(
            _timed(
                [sys.executable, "-c", "import dashscope_agenticx"],
                root,
            )
            * 1000,
            2,
        ),
        "cli_version_ms": round(
            _timed(
                [sys.executable, "-m", "dashscope_agenticx", "--version"],
                root,
            )
            * 1000,
            2,
        ),
    }
    wheels = sorted((root / "dist").glob("dashscope_agenticx-*.whl"))
    result["wheel_bytes"] = wheels[-1].stat().st_size if wheels else None
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
