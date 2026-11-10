# -*- coding: utf-8 -*-
"""Enforce 1.0 coverage floors for critical Runtime subsystems."""
from __future__ import annotations

import json
import sys
from pathlib import Path

GROUPS = {
    "engine": (
        90.0,
        [
            "src/dashscope_agenticx/agent.py",
            "src/dashscope_agenticx/engine.py",
            "src/dashscope_agenticx/events.py",
            "src/dashscope_agenticx/executor.py",
        ],
    ),
    "security": (
        90.0,
        [
            "src/dashscope_agenticx/apps/manifest.py",
            "src/dashscope_agenticx/apps/store.py",
            "src/dashscope_agenticx/permission.py",
            "src/dashscope_agenticx/sandbox.py",
            "src/dashscope_agenticx/utils/secret_store.py",
        ],
    ),
    "session": (
        90.0,
        [
            "src/dashscope_agenticx/session.py",
            "src/dashscope_agenticx/session_events.py",
            "src/dashscope_agenticx/memory/plan.py",
            "src/dashscope_agenticx/memory/experience.py",
        ],
    ),
}


def main(path: str = "coverage.json") -> int:
    report = json.loads(Path(path).read_text(encoding="utf-8"))
    failed = False
    for name, (minimum, files) in GROUPS.items():
        statements = 0
        missing = 0
        for filename in files:
            summary = report["files"][filename]["summary"]
            statements += summary["num_statements"]
            missing += summary["missing_lines"]
        coverage = 100.0 * (statements - missing) / statements
        print(f"{name}: {coverage:.2f}% (required {minimum:.0f}%)")
        failed = failed or coverage < minimum
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(
        main(sys.argv[1] if len(sys.argv) > 1 else "coverage.json"),
    )
