# -*- coding: utf-8 -*-
"""Full-process smoke tests: drive the real `agenticx` entry point in a
subprocess (isolated HOME + workspace) — no mocks, no fake provider.

These complement the in-process e2e suite by covering startup wiring that
fixtures bypass: Config.load against a fresh HOME, first-run setup skip,
banner rendering, prompt loop, and clean /exit.
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import dashscope_agenticx

_SRC = str(Path(dashscope_agenticx.__file__).resolve().parent.parent)


def _run_agenticx(
    args: list[str],
    stdin: str,
    home: Path,
    cwd: Path,
    extra_env: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ)
    env["HOME"] = str(home)
    env["AGENTICX_HOME"] = str(home / "config")
    env["PYTHONPATH"] = _SRC + os.pathsep + env.get("PYTHONPATH", "")
    env["DASHSCOPE_API_KEY"] = "sk-fake-smoke"
    if extra_env:
        env.update(extra_env)
    code = (
        "import sys; sys.argv = ['agenticx'] + "
        + repr(args)
        + "; from dashscope_agenticx.cli import main; main()"
    )
    return subprocess.run(
        [sys.executable, "-c", code],
        input=stdin,
        capture_output=True,
        text=True,
        env=env,
        cwd=cwd,
        timeout=60,
    )


class TestReplSmoke:
    def test_fresh_workspace_accepts_default_model_and_separate_expert(
        self,
        tmp_path,
    ):
        home = tmp_path / "home"
        home.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()
        proc = _run_agenticx(
            ["--cli"],
            "\n\n\n/exit\n",
            home,
            workspace,
            extra_env={
                "DASHSCOPE_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "AGENTICX_PROVIDER": "",
                "AGENTICX_MODEL": "",
            },
        )
        assert proc.returncode == 0, proc.stderr[-1000:]
        assert "Default configuration" in proc.stdout
        assert "Model: qwen3.8-flash" in proc.stdout
        assert "Setup complete" in proc.stdout
        assert (
            "Key source" not in proc.stdout
            and "Choose [1/2/3]" not in proc.stdout
        )
        assert (
            "Available models" not in proc.stdout
            and "Downloading expert" not in proc.stdout
        )
        assert (
            "Experts (optional)" in proc.stdout
            and "Expert: skipped" in proc.stdout
        )
        assert proc.stdout.index("Default configuration") < proc.stdout.index(
            "Experts (optional)",
        )
        assert proc.stdout.index("Experts (optional)") < proc.stdout.index(
            "Setup complete",
        )
        assert (
            "basic-chat" in proc.stdout
            and "dashscope-sdk-expert" in proc.stdout
            and "bailian-expert" in proc.stdout
        )
        assert (workspace / ".agenticx" / "config.toml").is_file()

    def test_fresh_workspace_runs_setup_without_downloading_expert(
        self,
        tmp_path,
    ):
        home = tmp_path / "home"
        home.mkdir()
        workspace = tmp_path / "workspace"
        workspace.mkdir()

        proc = _run_agenticx(
            ["--cli"],
            "smoke-user\n2\n\n\nn\n0\n0\n0\n/exit\n",
            home,
            workspace,
            extra_env={
                "DASHSCOPE_API_KEY": "",
                "ANTHROPIC_API_KEY": "",
                "OPENAI_API_KEY": "",
                "AGENTICX_PROVIDER": "",
                "AGENTICX_MODEL": "",
            },
        )

        assert proc.returncode == 0, proc.stderr[-1000:]
        assert "Workspace setup" in proc.stdout
        assert "Setup complete" in proc.stdout
        assert "Experts (optional)" in proc.stdout
        assert proc.stdout.index("Username") < proc.stdout.index(
            "Experts (optional)",
        )
        assert proc.stdout.index("Experts (optional)") < proc.stdout.index(
            "Setup complete",
        )
        assert "Choose [1/2/3]" not in proc.stdout
        assert "/expert download" not in proc.stdout
        assert "Downloading expert" not in proc.stdout
        config_file = workspace / ".agenticx" / "config.toml"
        assert 'user_name = "smoke-user"' in config_file.read_text()
        assert "being phased out" not in proc.stdout
        assert "China general" not in proc.stdout
        assert not (workspace / ".agenticx" / "app.toml").exists()

    def test_repl_starts_and_exits_cleanly(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        workspace = tmp_path / "ws"
        # Pre-seed the workspace config marker so first-run /setup is skipped.
        (workspace / ".agenticx").mkdir(parents=True)
        (workspace / ".agenticx" / "config.toml").touch()

        proc = _run_agenticx(["--cli"], "/exit\n", home, workspace)
        assert proc.returncode == 0, proc.stderr[-500:]
        assert "Bye!" in proc.stdout

    def test_help_flag_prints_usage(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        proc = _run_agenticx(["--help"], "", home, tmp_path)
        assert proc.returncode == 0
        assert "Usage: agenticx" in proc.stdout
        assert "agenticcli" not in proc.stdout
        assert "agcli" not in proc.stdout
        assert "agtx" not in proc.stdout

    def test_version_flag_prints_agenticx_release(self, tmp_path):
        home = tmp_path / "home"
        home.mkdir()
        proc = _run_agenticx(["--version"], "", home, tmp_path)
        assert proc.returncode == 0
        assert proc.stdout.strip() == "agenticx 1.0.1"
