# -*- coding: utf-8 -*-
"""M7 interface edge tests — cli/ handlers, dispatch, @file expansion,
completer, and the REPL slash-dispatch guard."""

from __future__ import annotations

import subprocess
import sys
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

import dashscope_agenticx.cli as cli_pkg
from dashscope_agenticx.cli import dispatch as dp
from dashscope_agenticx.cli import handlers_capability as hc
from dashscope_agenticx.cli import handlers_device as hd
from dashscope_agenticx.cli import handlers_misc as hm
from dashscope_agenticx.cli import handlers_skill as hs
from dashscope_agenticx.cli import multimodal as mm
from dashscope_agenticx.cli.completer import (
    AcliCompleter,
    SafeFileHistory,
    _get_arg_hint,
)
from dashscope_agenticx.cli.constants import (
    ALL_CAPABILITY_KEYS,
    CAPABILITY_CATALOG,
    EXPERT_COMMAND_ALIASES,
)
from dashscope_agenticx.config import Config
from dashscope_agenticx.executor import Executor


class TestReleaseContractCompatibility:
    def test_release_check_uses_the_supported_toml_loader(self):
        import runpy

        script = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "release_check.py"
        )
        with patch.dict(sys.modules, {"tomllib": None}):
            namespace = runpy.run_path(str(script))
        assert namespace["main"]() == 0


class TestDiagnosticLogSecurity:
    def test_asr_log_uses_a_private_unique_file(self, tmp_path, monkeypatch):
        import os
        import runpy
        import tempfile

        monkeypatch.setattr(tempfile, "tempdir", str(tmp_path))
        script = (
            Path(__file__).resolve().parents[2]
            / "scripts"
            / "diagnostic_asr_tool.py"
        )
        write_log = runpy.run_path(str(script))["_write_diagnostic_log"]
        events = dict.fromkeys(
            [
                "on_open",
                "on_close",
                "on_complete",
                "on_error",
                "on_event_count",
                "on_event_texts",
            ],
            0,
        )
        events["raw_results"] = ["fixture result"]
        first = Path(write_log("fixture model", events, 1, 0))
        second = Path(write_log("fixture model", events, 1, 0))

        assert first.parent == tmp_path
        assert first != second
        assert first.name.startswith("agenticx-asr-")
        assert "fixture result" in first.read_text()
        if os.name != "nt":
            assert first.stat().st_mode & 0o077 == 0


class _Out:
    """Console mock capturing print() args as joined text (Markdown-aware)."""

    def __init__(self):
        self.lines: list[str] = []

    def print(self, *args, **kwargs):
        self.lines.append(" ".join(self._txt(a) for a in args))

    @staticmethod
    def _txt(a):
        return getattr(a, "markup", None) or str(a)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


@pytest.fixture
def out():
    return _Out()


def _config(**kw):
    base = dict(
        asr_model="paraformer-realtime-v2",
        voice_silence_duration=2.0,
        voice_max_seconds=60,
        voice_silence_threshold=500,
        tts_enabled=False,
        tts_model="cosyvoice-v2",
        tts_voice="longxiaochun_v2",
        tts_speed=1.0,
        tongyi_api_key="sk-test",
        privacy_mode=False,
        enabled_capabilities=None,
        custom_models=[],
        subagents={},
        save_workspace=lambda: None,
        save_global=lambda: None,
    )
    base.update(kw)
    return types.SimpleNamespace(**base)


# ---------------------------------------------------------------------------
# handlers_device — /voice
# ---------------------------------------------------------------------------


class TestRulesEditorSecurity:
    def test_editor_receives_path_as_one_argument(self, tmp_path, monkeypatch):
        from types import SimpleNamespace

        from dashscope_agenticx.cli import handlers_config

        workspace = tmp_path / "workspace ; injected" / ".agenticx"
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            workspace,
        )
        monkeypatch.setenv("EDITOR", '"editor with spaces" --wait')
        calls = []

        def run(arguments, **keywords):
            calls.append((arguments, keywords))
            return SimpleNamespace(returncode=0)

        monkeypatch.setattr(handlers_config.subprocess, "run", run)
        handlers_config._handle_rule_command("/rule edit", _config())
        assert calls == [
            (
                [
                    "editor with spaces",
                    "--wait",
                    str(workspace / "rules.jsonl"),
                ],
                {"check": False},
            ),
        ]

    @pytest.mark.parametrize("editor", ["", '"unterminated'])
    def test_invalid_editor_does_not_execute(
        self,
        editor,
        tmp_path,
        monkeypatch,
        out,
    ):
        from dashscope_agenticx.cli import handlers_config

        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path / ".agenticx",
        )
        monkeypatch.setenv("EDITOR", editor)
        monkeypatch.setattr(handlers_config, "console", out)
        monkeypatch.setattr(
            handlers_config.subprocess,
            "run",
            lambda *arguments, **keywords: pytest.fail("must not execute"),
        )
        handlers_config._handle_rule_command("/rule edit", _config())
        assert "Could not open rules editor" in out.text


class TestVoiceCommand:
    def test_status_default(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        assert hd._handle_voice_command("/voice", _config()) is True
        assert "Voice Input" in out.text
        assert "paraformer-realtime-v2" in out.text

    def test_on_returns_voice(self):
        assert hd._handle_voice_command("/voice on", _config()) == "voice"
        assert hd._handle_voice_command("/voice start", _config()) == "voice"

    def test_off_cancels_or_reports(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.ui.voice.try_cancel_voice_input",
            lambda: True,
        )
        hd._handle_voice_command("/voice off", _config())
        assert "Recording cancelled" in out.text
        monkeypatch.setattr(
            "dashscope_agenticx.ui.voice.try_cancel_voice_input",
            lambda: False,
        )
        hd._handle_voice_command("/voice off", _config())
        assert "No recording in progress" in out.text

    def test_model_shows_current_without_arg(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        hd._handle_voice_command("/voice model", _config())
        assert "Current ASR model" in out.text

    def test_model_rejects_unknown(self, out, monkeypatch):
        """Regression: /voice model accepted any string; a typo persisted and
        only failed at recording time. Must reject names outside ASR_MODELS."""
        monkeypatch.setattr(hd, "console", out)
        cfg = _config()
        hd._handle_voice_command("/voice model bogus-model", cfg)
        assert "Unknown ASR model" in out.text
        assert cfg.asr_model == "paraformer-realtime-v2"

    @pytest.mark.parametrize("model", ["sensevoice-v1", "SENSEVOICE-V1"])
    def test_model_accepts_known(self, out, monkeypatch, model):
        monkeypatch.setattr(hd, "console", out)
        cfg = _config()
        hd._handle_voice_command(f"/voice model {model}", cfg)
        assert cfg.asr_model == "sensevoice-v1"
        assert "sensevoice-v1" in out.text

    @pytest.mark.parametrize(
        "cmd,attr,bad",
        [
            (
                "/voice silence 3.5",
                "voice_silence_duration",
                "/voice silence -1",
            ),
            ("/voice max 30", "voice_max_seconds", "/voice max 0"),
            (
                "/voice threshold 800",
                "voice_silence_threshold",
                "/voice threshold -5",
            ),
        ],
    )
    def test_numeric_settings(self, out, monkeypatch, cmd, attr, bad):
        monkeypatch.setattr(hd, "console", out)
        cfg = _config()
        hd._handle_voice_command(cmd, cfg)
        assert getattr(cfg, attr) == float(cmd.split()[-1])
        before = getattr(cfg, attr)
        hd._handle_voice_command(bad, cfg)
        assert getattr(cfg, attr) == before  # invalid rejected, unchanged


# ---------------------------------------------------------------------------
# handlers_device — /tts and /camera
# ---------------------------------------------------------------------------


class TestTtsCommand:
    def test_model_normalized_before_validation(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        config = _config()
        hd._handle_tts_command("/tts model COSYVOICE-V1", config)
        assert config.tts_model == "cosyvoice-v1"
        assert "Unknown model" not in out.text

    def test_on_unavailable(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.ui.tts.is_available",
            lambda: (False, "缺依赖"),
        )
        hd._handle_tts_command("/tts on", _config())
        assert "缺依赖" in out.text

    def test_on_off_roundtrip(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.ui.tts.is_available",
            lambda: (True, ""),
        )
        cfg = _config()
        hd._handle_tts_command("/tts on", cfg)
        assert cfg.tts_enabled is True
        hd._handle_tts_command("/tts off", cfg)
        assert cfg.tts_enabled is False

    def test_model_rejects_unknown(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        cfg = _config()
        hd._handle_tts_command("/tts model nope", cfg)
        assert "Unknown model" in out.text
        assert cfg.tts_model == "cosyvoice-v2"

    def test_speed_range_enforced(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        cfg = _config()
        hd._handle_tts_command("/tts speed 9.9", cfg)
        assert cfg.tts_speed == 1.0
        assert "Speed must be between 0.5 and 2.0" in out.text
        hd._handle_tts_command("/tts speed abc", cfg)
        assert "Please enter a valid number" in out.text

    def test_say_speaks(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.ui.tts.is_available",
            lambda: (True, ""),
        )
        spoken = []
        monkeypatch.setattr(
            "dashscope_agenticx.ui.tts.speak_text",
            lambda *a, **k: spoken.append(a),
        )
        hd._handle_tts_command("/tts say 你好", _config())
        assert spoken and spoken[0][1] == "你好"

    def test_last_without_output(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        agent = types.SimpleNamespace(last_output="")
        hd._handle_tts_command("/tts last", _config(), agent)
        assert "No previous reply to speak" in out.text

    def test_unknown_sub_prints_usage(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        hd._handle_tts_command("/tts frobnicate", _config())
        assert "Usage" in out.text


class TestCameraCommand:
    def test_status(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.ui.camera.is_available",
            lambda: (True, "opencv"),
        )
        hd._handle_camera_command("/camera")
        assert "available (opencv)" in out.text

    def test_capture_success(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.ui.camera.capture",
            lambda f: f"已保存: {f}",
        )
        hd._handle_camera_command("/camera capture x.jpg")
        assert "已保存: x.jpg" in out.text

    def test_capture_error_in_red(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.ui.camera.capture",
            lambda f: "错误: 无摄像头",
        )
        hd._handle_camera_command("/camera capture")
        assert "错误: 无摄像头" in out.text

    def test_record_duration_and_filename(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        calls = []
        monkeypatch.setattr(
            "dashscope_agenticx.ui.camera.record",
            lambda f, d: calls.append((f, d)) or "ok",
        )
        hd._handle_camera_command("/camera record 3 out.mp4")
        assert calls == [("out.mp4", 3.0)]

    def test_record_nonnumeric_first_arg_is_filename(self, out, monkeypatch):
        monkeypatch.setattr(hd, "console", out)
        calls = []
        monkeypatch.setattr(
            "dashscope_agenticx.ui.camera.record",
            lambda f, d: calls.append((f, d)) or "ok",
        )
        hd._handle_camera_command("/camera record clip.mp4")
        assert calls == [("clip.mp4", 5.0)]


# ---------------------------------------------------------------------------
# handlers_misc — /trust /history /report
# ---------------------------------------------------------------------------


def _agent_with_executor():
    # A real Executor, not a stub. The stub carried only the two private sets
    # the handler used to reach into, so it could not notice the handler being
    # rewritten against the trust API -- nor that run_command grants now live
    # in a third set the stub did not have at all.
    return types.SimpleNamespace(executor=Executor())


class TestTrustCommand:
    def test_empty_list(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        hm._handle_trust_command("/trust", _agent_with_executor())
        assert "No trust/deny cache" in out.text

    def test_allow_deny_list_clear(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        agent = _agent_with_executor()
        hm._handle_trust_command("/trust allow run_command", agent)
        assert "run_command" in agent.executor.trust_snapshot()[0]
        hm._handle_trust_command("/trust deny run_command", agent)
        allow, _, deny = agent.executor.trust_snapshot()
        assert "run_command" in deny
        assert "run_command" not in allow
        hm._handle_trust_command("/trust list", agent)
        assert "Denied tools this turn" in out.text
        hm._handle_trust_command("/trust clear", agent)
        assert agent.executor.trust_snapshot() == (set(), set(), set())
        assert "Cleared 1 cache entries" in out.text

    def test_blanket_allow_warns_it_is_not_command_scoped(
        self,
        out,
        monkeypatch,
    ):
        # /trust allow run_command really is a blanket shell grant, unlike
        # [a]lways at the prompt. Say so at the moment it is made.
        monkeypatch.setattr(hm, "console", out)
        agent = _agent_with_executor()
        hm._handle_trust_command("/trust allow run_command", agent)
        assert "pre-approves every shell command" in out.text
        out.lines.clear()
        hm._handle_trust_command("/trust allow write_file", agent)
        assert "pre-approves every shell command" not in out.text

    def test_list_and_clear_cover_command_grants(self, out, monkeypatch):
        # Seeded the way a prompt seeds it: exact command, not tool name.
        monkeypatch.setattr(hm, "console", out)
        agent = _agent_with_executor()
        agent.executor._grant_always(
            types.SimpleNamespace(name="run_command"),
            {"command": "make build"},
        )
        hm._handle_trust_command("/trust list", agent)
        assert "Trusted commands this turn" in out.text
        assert "make build" in out.text
        assert "Trusted tools this turn" not in out.text
        hm._handle_trust_command("/trust clear", agent)
        assert agent.executor.trust_snapshot() == (set(), set(), set())
        assert "Cleared 1 cache entries" in out.text

    def test_listed_commands_are_escaped_not_rendered(self, out, monkeypatch):
        # A command is model-controlled text; printed raw, a bracket in it
        # would be parsed as markup and could rewrite the listing.
        monkeypatch.setattr(hm, "console", out)
        agent = _agent_with_executor()
        agent.executor._grant_always(
            types.SimpleNamespace(name="run_command"),
            {"command": "echo [bold red]fake"},
        )
        hm._handle_trust_command("/trust list", agent)
        assert r"\[bold red]fake" in out.text

    def test_unknown_sub_usage(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        hm._handle_trust_command("/trust bogus", _agent_with_executor())
        assert "Usage" in out.text


class TestHistoryCommand:
    def _patch_history(self, monkeypatch, **over):
        mod = types.SimpleNamespace(
            stats=lambda: {"count": 0, "total_turns": 0},
            list_history=lambda limit=20: [],
            clear_history=lambda: 3,
            export_history=lambda p, fmt=None: f"/tmp/out.{fmt}",
        )
        for k, v in over.items():
            setattr(mod, k, v)
        monkeypatch.setattr(
            "dashscope_agenticx.platforms.local.history.stats",
            mod.stats,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.platforms.local.history.list_history",
            mod.list_history,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.platforms.local.history.clear_history",
            mod.clear_history,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.platforms.local.history.export_history",
            mod.export_history,
        )
        return mod

    def test_stats_empty(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        self._patch_history(monkeypatch)
        hm._handle_history_command("/history")
        assert "No conversation history yet" in out.text

    def test_list_non_numeric_crashed_repl(self, out, monkeypatch):
        """Regression: /history list abc raised ValueError out of the handler;
        the REPL dispatch had no guard, so the whole session died."""
        monkeypatch.setattr(hm, "console", out)
        self._patch_history(monkeypatch)
        hm._handle_history_command("/history list abc")  # must not raise
        assert "Usage" in out.text

    def test_list_numeric(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        seen = {}

        def _list(limit=20):
            seen["limit"] = limit
            return [{"summary": "s", "created_at": "t", "turns": 2}]

        self._patch_history(monkeypatch, list_history=_list)
        hm._handle_history_command("/history list 5")
        assert seen["limit"] == 5
        assert "s" in out.text

    def test_export_format_inference(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        seen = {}

        def _export(path, fmt=None):
            seen["fmt"] = fmt
            return path

        self._patch_history(monkeypatch, export_history=_export)
        hm._handle_history_command("/history export a.json")
        assert seen["fmt"] == "json"
        hm._handle_history_command("/history export a.txt --format md")
        assert seen["fmt"] == "markdown"
        hm._handle_history_command("/history export a.bin")
        assert seen["fmt"] == "html"

    def test_export_failure_reported(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)

        def _boom(path, fmt=None):
            raise OSError("disk full")

        self._patch_history(monkeypatch, export_history=_boom)
        hm._handle_history_command("/history export x.json")
        assert "Export failed" in out.text

    def test_clear(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        self._patch_history(monkeypatch)
        hm._handle_history_command("/history clear")
        assert "Cleared 3 history records" in out.text


class TestReportCommand:
    def test_no_data(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.memory.trace.generate_report",
            lambda t: None,
        )
        hm._handle_report_command(types.SimpleNamespace(trace_logger=None))
        assert "Not enough trace data" in out.text

    def test_full_report(self, out, monkeypatch):
        monkeypatch.setattr(hm, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.memory.trace.generate_report",
            lambda t: {
                "total_llm_calls": 4,
                "total_tool_calls": 9,
                "tool_success_rate": 0.75,
                "avg_response_time": 1.5,
                "top_tools": [("run_command", 5)],
            },
        )
        hm._handle_report_command(types.SimpleNamespace(trace_logger=None))
        assert "Total LLM calls: 4" in out.text
        assert "run_command: 5" in out.text


# ---------------------------------------------------------------------------
# handlers_skill — /skill and /cron
# ---------------------------------------------------------------------------


def _skill(name, arguments=(), mcp_service="", template="{text}"):
    from dashscope_agenticx.skills.base import Skill

    return Skill(
        name=name,
        description="d",
        prompt_template=template,
        mcp_service=mcp_service,
        arguments=list(arguments),
    )


@pytest.fixture
def builtin_skills():
    from dashscope_agenticx.skills.base import BUILTIN_SKILLS

    saved = dict(BUILTIN_SKILLS)
    yield BUILTIN_SKILLS
    BUILTIN_SKILLS.clear()
    BUILTIN_SKILLS.update(saved)


class TestSkillCommand:
    async def test_bare_lists(self, out, monkeypatch):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.skills.base.load_skill_files",
            lambda: None,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.skills.list_skills",
            lambda c: "SKILL-LIST",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.skills.get_skill_manager",
            lambda: types.SimpleNamespace(list=lambda: []),
        )
        assert await hs._handle_skill_command("/skill", _config()) is None
        assert "SKILL-LIST" in out.text
        assert "No skill packages installed" in out.text

    async def test_unknown_skill(self, out, monkeypatch, builtin_skills):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.skills.base.load_skill_files",
            lambda: None,
        )
        assert (
            await hs._handle_skill_command("/skill nosuch x", _config())
            is None
        )
        assert "Unknown skill: nosuch" in out.text

    async def test_render_happy_path(self, monkeypatch, builtin_skills):
        monkeypatch.setattr(
            "dashscope_agenticx.skills.base.load_skill_files",
            lambda: None,
        )
        builtin_skills["echo"] = _skill(
            "echo",
            ["text"],
            template="say: {text}",
        )
        rendered = await hs._handle_skill_command(
            "/skill echo hello world",
            _config(),
        )
        assert rendered == "say: hello world"

    async def test_missing_args_usage(self, out, monkeypatch, builtin_skills):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.skills.base.load_skill_files",
            lambda: None,
        )
        builtin_skills["echo"] = _skill("echo", ["text"])
        assert await hs._handle_skill_command("/skill echo", _config()) is None
        assert "Usage: /skill echo <text>" in out.text

    async def test_partial_args_render_failure(
        self,
        out,
        monkeypatch,
        builtin_skills,
    ):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.skills.base.load_skill_files",
            lambda: None,
        )
        builtin_skills["two"] = _skill("two", ["a", "b"], template="{a}-{b}")
        assert (
            await hs._handle_skill_command("/skill two onlyone", _config())
            is None
        )
        assert "Missing arguments" in out.text

    async def test_mcp_autoconnect_failure(
        self,
        out,
        monkeypatch,
        builtin_skills,
    ):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.skills.base.load_skill_files",
            lambda: None,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.cli.mcp._connect_mcp",
            AsyncMock(return_value="连不上"),
        )
        builtin_skills["mcpsk"] = _skill("mcpsk", ["text"], mcp_service="svc")
        assert (
            await hs._handle_skill_command("/skill mcpsk x", _config()) is None
        )
        assert "Connection failed: 连不上" in out.text

    async def test_package_subcommand_routing(self, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.skills.base.load_skill_files",
            lambda: None,
        )
        seen = []

        async def fake(sub, args):
            seen.append((sub, args))
            return None

        monkeypatch.setattr(hs, "_handle_skill_package_command", fake)
        await hs._handle_skill_command("/skill add some-dir", _config())
        await hs._handle_skill_command("/skill remove pkg", _config())
        assert seen == [("install", ["some-dir"]), ("uninstall", ["pkg"])]


class TestSkillPackageCommand:
    def _manager(self, **over):
        mgr = types.SimpleNamespace(
            reload=lambda: over.get("reload"),
            enable=lambda n: n == "pkg",
            disable=lambda n: n == "pkg",
            install=lambda s: "installed-pkg",
            link=lambda s: "linked-pkg",
            update=lambda n: None,
            update_all=lambda: [("a", None), ("b", "网络错误")],
            uninstall=lambda n: n == "pkg",
            search=lambda q: (
                [{"name": "x", "version": "1", "installed": False}]
                if q
                else []
            ),
            publish=lambda s: "https://example/pkg",
            list=lambda: [],
        )
        for k, v in over.items():
            setattr(mgr, k, v)
        return mgr

    async def test_enable_disable(self, out, monkeypatch):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.skills.get_skill_manager",
            lambda: self._manager(),
        )
        await hs._handle_skill_package_command("enable", ["pkg"])
        assert "Enabled skill package: pkg" in out.text
        await hs._handle_skill_package_command("enable", ["nope"])
        assert "Skill package not found: nope" in out.text
        await hs._handle_skill_package_command("disable", [])
        assert "Usage" in out.text

    async def test_install_failure_caught(self, out, monkeypatch):
        monkeypatch.setattr(hs, "console", out)

        def _boom(s):
            raise RuntimeError("git 404")

        monkeypatch.setattr(
            "dashscope_agenticx.skills.get_skill_manager",
            lambda: self._manager(install=_boom),
        )
        await hs._handle_skill_package_command("install", ["bad-dir"])
        assert "Install failed: git 404" in out.text

    async def test_update_all_mixed(self, out, monkeypatch):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.skills.get_skill_manager",
            lambda: self._manager(),
        )
        await hs._handle_skill_package_command("update", ["--all"])
        assert "Updated 1 skill packages" in out.text
        assert "b: 网络错误" in out.text

    async def test_at_prefix_stripped(self, out, monkeypatch):
        monkeypatch.setattr(hs, "console", out)
        seen = []
        monkeypatch.setattr(
            "dashscope_agenticx.skills.get_skill_manager",
            lambda: self._manager(install=lambda s: seen.append(s) or "p"),
        )
        await hs._handle_skill_package_command("install", ["@some/dir"])
        assert seen == ["some/dir"]

    async def test_unknown_sub(self, out, monkeypatch):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.skills.get_skill_manager",
            lambda: self._manager(),
        )
        await hs._handle_skill_package_command("frob", [])
        assert "Unknown /skill subcommand" in out.text


class TestCronCommand:
    @pytest.fixture
    def scheduler(self, monkeypatch):
        fake = types.SimpleNamespace(
            jobs={"j1": object()},
            print_jobs=lambda: None,
            add_job=lambda *a: types.SimpleNamespace(id="j2"),
            remove_job=lambda i: i == "j1",
            pause_job=lambda i: i == "j1",
            resume_job=lambda i: i == "j1",
        )
        monkeypatch.setattr(cli_pkg, "_scheduler", fake)
        yield fake
        monkeypatch.setattr(cli_pkg, "_scheduler", None)

    async def test_list(self, scheduler):
        called = []
        scheduler.print_jobs = lambda: called.append(1)
        await hs._handle_cron_command("/cron", _config(), None)
        assert called

    async def test_add_happy_path(self, out, monkeypatch, scheduler):
        monkeypatch.setattr(hs, "console", out)
        schedule = types.SimpleNamespace(kind="every")
        monkeypatch.setattr(
            "dashscope_agenticx.scheduler.parse_cron_add",
            lambda s: (
                schedule,
                [types.SimpleNamespace(name="weather")],
                None,
                False,
            ),
        )
        from dashscope_agenticx.skills.base import BUILTIN_SKILLS

        saved = dict(BUILTIN_SKILLS)
        BUILTIN_SKILLS["weather"] = _skill("weather")
        try:
            await hs._handle_cron_command(
                "/cron add every 5m skill weather 杭州",
                _config(),
                None,
            )
        finally:
            BUILTIN_SKILLS.clear()
            BUILTIN_SKILLS.update(saved)
        assert "Scheduled job created" in out.text
        assert "j2" in out.text

    async def test_add_unknown_skill_rejected(
        self,
        out,
        monkeypatch,
        scheduler,
    ):
        monkeypatch.setattr(hs, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.scheduler.parse_cron_add",
            lambda s: (
                types.SimpleNamespace(kind="every"),
                [types.SimpleNamespace(name="definitely-not-a-skill")],
                None,
                False,
            ),
        )
        await hs._handle_cron_command(
            "/cron add every 5m skill definitely-not-a-skill",
            _config(),
            None,
        )
        assert "Unknown skill" in out.text

    async def test_add_parse_error(self, out, monkeypatch, scheduler):
        monkeypatch.setattr(hs, "console", out)

        def _boom(s):
            raise ValueError("无法解析调度")

        monkeypatch.setattr(
            "dashscope_agenticx.scheduler.parse_cron_add",
            _boom,
        )
        await hs._handle_cron_command("/cron add garbage", _config(), None)
        assert "无法解析调度" in out.text

    async def test_remove_pause_resume(self, out, monkeypatch, scheduler):
        monkeypatch.setattr(hs, "console", out)
        await hs._handle_cron_command("/cron remove j1", _config(), None)
        assert "Removed scheduled job: j1" in out.text
        await hs._handle_cron_command("/cron remove j9", _config(), None)
        assert "Scheduled job not found: j9" in out.text
        await hs._handle_cron_command("/cron pause j1", _config(), None)
        assert "Paused scheduled job" in out.text
        await hs._handle_cron_command("/cron resume j1", _config(), None)
        assert "Resumed scheduled job" in out.text
        await hs._handle_cron_command("/cron frobnicate", _config(), None)
        assert "Unknown /cron subcommand" in out.text


# ---------------------------------------------------------------------------
# handlers_capability — /capability
# ---------------------------------------------------------------------------


@pytest.fixture
def catalog():
    saved_cat = list(CAPABILITY_CATALOG)
    saved_keys = list(ALL_CAPABILITY_KEYS)
    yield
    CAPABILITY_CATALOG[:] = saved_cat
    ALL_CAPABILITY_KEYS[:] = saved_keys


class TestCapabilityCommand:
    def test_list(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        hc._handle_capability_command("/capability list", _config())
        assert "bailian.mcp" in out.text
        assert "local.memory" in out.text

    def test_privacy_mode_all_disabled(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        hc._handle_capability_command(
            "/capability list",
            _config(privacy_mode=True),
        )
        assert "✓" not in out.text

    def test_enable_unknown(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        hc._handle_capability_command("/capability enable nope", _config())
        assert "Unknown capability: nope" in out.text

    def test_enable_when_all_enabled_is_noop(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        hc._handle_capability_command(
            "/capability enable bailian.mcp",
            _config(),
        )
        assert "All capabilities currently enabled" in out.text

    def test_enable_already_enabled_reregisters(
        self,
        out,
        monkeypatch,
        catalog,
    ):
        monkeypatch.setattr(hc, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.cli.handlers_key._maybe_prompt_extension_token",
            lambda k, c: None,
        )
        unregistered = []
        monkeypatch.setattr(
            "dashscope_agenticx.tools.platform.unregister_capability_tools",
            lambda k: unregistered.append(k) or 0,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.tools.platform.register_one_capability",
            lambda c, k, connect_mcp_fn=None: 2,
        )
        cfg = _config(enabled_capabilities=["bailian.cli"])
        hc._handle_capability_command("/capability enable bailian.cli", cfg)
        assert unregistered == ["bailian.cli"]
        assert "2 tools registered" in out.text

    def test_enable_missing_requires_aborts(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        monkeypatch.setattr(hc, "_prompt_missing_config", lambda c, r: False)
        cfg = _config(enabled_capabilities=[], tongyi_api_key="")
        hc._handle_capability_command("/capability enable bailian.mcp", cfg)
        assert "Config incomplete; not enabled" in out.text
        assert "bailian.mcp" not in cfg.enabled_capabilities

    def test_enable_new_capability(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        monkeypatch.setattr(hc, "_prompt_missing_config", lambda c, r: True)
        monkeypatch.setattr(
            "dashscope_agenticx.cli.handlers_key._maybe_prompt_extension_token",
            lambda k, c: None,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.tools.platform.register_one_capability",
            lambda c, k, connect_mcp_fn=None: 1,
        )
        cfg = _config(enabled_capabilities=[])
        hc._handle_capability_command("/capability enable bailian.mcp", cfg)
        assert cfg.enabled_capabilities == ["bailian.mcp"]
        assert "Enabled: bailian.mcp" in out.text

    def test_disable_from_none_builds_explicit_list(
        self,
        out,
        monkeypatch,
        catalog,
    ):
        monkeypatch.setattr(hc, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.tools.platform.unregister_capability_tools",
            lambda k: 3,
        )
        cfg = _config(enabled_capabilities=None)
        hc._handle_capability_command("/capability disable bailian.cli", cfg)
        assert cfg.enabled_capabilities is not None
        assert "bailian.cli" not in cfg.enabled_capabilities
        assert "bailian.mcp" in cfg.enabled_capabilities
        assert "3 tools unregistered" in out.text

    def test_disable_not_enabled(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        cfg = _config(enabled_capabilities=["bailian.mcp"])
        hc._handle_capability_command("/capability disable bailian.cli", cfg)
        assert "not enabled" in out.text

    def test_bare_cap_key_suggests_action(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        hc._handle_capability_command("/capability local.memory", _config())
        assert "Missing action" in out.text
        assert "/capability disable local.memory" in out.text

    def test_config_routes_subagent_caps(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        seen = []
        monkeypatch.setattr(
            "dashscope_agenticx.agents.subagents.handle_subagents_command",
            lambda cmd, c: seen.append(cmd),
        )
        from dashscope_agenticx.agents.subagents import (
            SUBAGENT_CAPABILITY_KEYS,
        )

        cap = sorted(SUBAGENT_CAPABILITY_KEYS)[0]
        hc._handle_capability_command(
            f"/capability config {cap} model m1",
            _config(),
        )
        assert seen == [f"/subagents config {cap} model m1"]

    def test_require_capability_hint(self, out, monkeypatch, catalog):
        monkeypatch.setattr(hc, "console", out)
        cfg = _config(enabled_capabilities=[])
        assert hc._require_capability(cfg, "bailian.mcp") is False
        assert "Capability not enabled" in out.text
        assert hc._require_capability(_config(), "bailian.mcp") is True


# ---------------------------------------------------------------------------
# dispatch — routing, unknown commands, /dev test provider bare
# ---------------------------------------------------------------------------


def _dispatch_agent():
    return types.SimpleNamespace(
        json_mode=False,
        last_output="",
        messages=[],
        session_path=None,
        provider_name="tongyi",
        model_name="qwen3.7-plus",
        experience_tracker=None,
        executor=types.SimpleNamespace(
            get_stats=lambda: {},
            _always_allow=set(),
            _always_deny=set(),
        ),
        reset=lambda: None,
        save_session=lambda: None,
    )


class TestSlashDispatch:
    def test_unknown_returns_false(self):
        assert (
            dp._handle_slash_command("/nosuchcmd", _dispatch_agent(), Config())
            is False
        )

    def test_json_toggle(self, out, monkeypatch):
        monkeypatch.setattr(dp, "console", out)
        agent = _dispatch_agent()
        assert dp._handle_slash_command("/json on", agent, Config()) is True
        assert agent.json_mode is True
        dp._handle_slash_command("/json off", agent, Config())
        assert agent.json_mode is False
        dp._handle_slash_command("/json bogus", agent, Config())
        assert "Usage" in out.text

    def test_exit_raises_systemexit(self, monkeypatch):
        monkeypatch.setattr(dp, "console", _Out())
        with pytest.raises(SystemExit):
            dp._handle_slash_command("/exit", _dispatch_agent(), Config())

    def test_copy_without_content(self, out, monkeypatch):
        monkeypatch.setattr(dp, "console", out)
        monkeypatch.setattr(cli_pkg, "console", out)
        dp._handle_slash_command("/copy", _dispatch_agent(), Config())
        assert "Nothing to copy" in out.text

    def test_copy_uses_available_clipboard(self, out, monkeypatch):
        monkeypatch.setattr(dp, "console", out)
        monkeypatch.setattr(cli_pkg, "console", out)
        monkeypatch.setattr(
            "shutil.which",
            lambda c: "/usr/bin/pbcopy" if c == "pbcopy" else None,
        )
        runs = []
        monkeypatch.setattr(
            "subprocess.run",
            lambda *a, **k: runs.append((a, k))
            or types.SimpleNamespace(returncode=0),
        )
        agent = _dispatch_agent()
        agent.last_output = "hello"
        dp._handle_slash_command("/copy", agent, Config())
        assert runs and runs[0][0][0] == ["pbcopy"]
        assert "Copied 5 chars" in out.text

    def test_save_default_filename(self, out, monkeypatch, tmp_path):
        monkeypatch.setattr(dp, "console", out)
        monkeypatch.chdir(tmp_path)
        agent = _dispatch_agent()
        agent.last_output = "content-here"
        dp._handle_slash_command("/save", agent, Config())
        written = list(tmp_path.glob("agenticx_output_*.md"))
        assert len(written) == 1
        assert written[0].read_text() == "content-here"

    def test_save_failure(self, out, monkeypatch, tmp_path):
        monkeypatch.setattr(dp, "console", out)
        agent = _dispatch_agent()
        agent.last_output = "x"
        dp._handle_slash_command(f"/save {tmp_path}", agent, Config())
        assert "Save failed" in out.text

    def test_clear_resets_and_persists(self, out, monkeypatch):
        monkeypatch.setattr(dp, "console", out)
        calls = []
        agent = _dispatch_agent()
        agent.session_path = "/tmp/sess"
        agent.reset = lambda: calls.append("reset")
        agent.save_session = lambda: calls.append("save")
        dp._handle_slash_command("/clear", agent, Config())
        assert calls == ["reset", "save"]

    def test_compress_threshold(self, out, monkeypatch):
        monkeypatch.setattr(dp, "console", out)
        agent = _dispatch_agent()
        assert dp._handle_slash_command("/compress", agent, Config()) is True
        assert "Conversation too short" in out.text
        agent.messages = [{"role": "user"}] * 5
        assert (
            dp._handle_slash_command("/compress", agent, Config())
            == "compress"
        )

    def test_routing_tokens(self):
        agent, cfg = _dispatch_agent(), Config()
        assert dp._handle_slash_command("/v", agent, cfg) == "voice"
        assert dp._handle_slash_command("/voice on", agent, cfg) == "voice"
        assert (
            dp._handle_slash_command("/summarize", agent, cfg) == "summarize"
        )
        assert dp._handle_slash_command("/skill", agent, cfg) == "skill"
        assert dp._handle_slash_command("/mcp list", agent, cfg) == "async"
        assert (
            dp._handle_slash_command("/dev test provider tongyi", agent, cfg)
            == "async"
        )
        assert (
            dp._handle_slash_command("/dev debug call t {}", agent, cfg)
            == "async"
        )
        assert (
            dp._handle_slash_command("/capability list", agent, cfg) == "async"
        )

    def test_model_command_routes_to_the_handler(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            "dashscope_agenticx.cli.handlers_provider.handle_model_command",
            lambda cmd, agent, config: seen.append(cmd) or True,
        )
        agent, cfg = _dispatch_agent(), Config()
        assert dp._handle_slash_command("/model", agent, cfg) is True
        assert dp._handle_slash_command("/model qwen-plus", agent, cfg) is True
        assert seen == ["/model", "/model qwen-plus"]

    def test_audit_bad_limit(self, out, monkeypatch):
        monkeypatch.setattr(dp, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.audit.get_audit_logger",
            lambda: types.SimpleNamespace(
                recent=lambda limit=20: [],
                query=lambda limit=50: [],
                clear=lambda: None,
            ),
        )
        dp._handle_slash_command(
            "/audit recent abc",
            _dispatch_agent(),
            Config(),
        )
        assert "Usage: /audit recent" in out.text

    def test_audit_renders_events(self, out, monkeypatch):
        monkeypatch.setattr(dp, "console", out)
        monkeypatch.setattr(
            "dashscope_agenticx.audit.get_audit_logger",
            lambda: types.SimpleNamespace(
                recent=lambda limit=20: [
                    {
                        "timestamp": "2026-08-07T10:00:00",
                        "source": "executor",
                        "action": "tool",
                        "subject": "run_command",
                        "decision": "allow",
                    },
                ],
                query=lambda limit=50: [],
                clear=lambda: None,
            ),
        )
        dp._handle_slash_command("/audit", _dispatch_agent(), Config())
        assert "run_command" in out.text
        assert "allow" in out.text


class TestAsyncDispatch:
    async def test_bare_test_provider_prints_usage(self, monkeypatch):
        """Regression: '/dev test provider' without a name hit the async
        branch, len(parts) < 4 skipped everything — a silent no-op."""
        seen = []
        monkeypatch.setattr(
            dp,
            "handle_dev_command",
            lambda cmd, cfg: seen.append(cmd),
        )
        await dp.dispatch_async_command(
            "/dev test provider",
            Config(),
            _dispatch_agent(),
        )
        assert seen == ["/dev test provider"]

    async def test_test_provider_with_name_awaits(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            "dashscope_agenticx.dev._test_provider",
            AsyncMock(side_effect=lambda n, c: called.append(n)),
        )
        await dp.dispatch_async_command(
            "/dev test provider tongyi",
            Config(),
            _dispatch_agent(),
        )
        assert called == ["tongyi"]

    async def test_debug_call_awaits(self, monkeypatch):
        called = []
        monkeypatch.setattr(
            "dashscope_agenticx.dev._debug_call",
            AsyncMock(side_effect=lambda parts: called.append(parts)),
        )
        await dp.dispatch_async_command(
            '/dev debug call my_tool {"a": 1}',
            Config(),
            _dispatch_agent(),
        )
        assert called == [["/dev", "debug", "call", "my_tool", '{"a":', "1}"]]

    async def test_capability_routes_sync(self, monkeypatch):
        seen = []
        monkeypatch.setattr(
            "dashscope_agenticx.cli.handlers_capability._handle_capability_command",
            lambda cmd, cfg: seen.append(cmd),
        )
        await dp.dispatch_async_command(
            "/capability list",
            Config(),
            _dispatch_agent(),
        )
        assert seen == ["/capability list"]

    async def test_mcp_requires_capability(self, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.cli.handlers_capability._require_capability",
            lambda c, k: False,
        )
        called = []
        monkeypatch.setattr(
            "dashscope_agenticx.cli.mcp._handle_mcp_command",
            AsyncMock(side_effect=lambda cmd, cfg: called.append(cmd)),
        )
        await dp.dispatch_async_command(
            "/mcp list",
            Config(),
            _dispatch_agent(),
        )
        assert not called  # gated off when capability disabled


# ---------------------------------------------------------------------------
# multimodal — @file expansion
# ---------------------------------------------------------------------------


class TestExpandAtReferences:
    def test_missing_file_left_untouched(self):
        expanded, images, audio = mm._expand_at_references(
            "看 @/no/such/file.py 这个",
        )
        assert "@/no/such/file.py" in expanded
        assert not images and not audio

    def test_text_file_fenced(self, tmp_path):
        f = tmp_path / "a.py"
        f.write_text("print('hi')\n", encoding="utf-8")
        expanded, _, _ = mm._expand_at_references(f"@{f}")
        assert f"--- @{f} ---" in expanded
        assert "```python\nprint('hi')" in expanded

    def test_large_file_truncated(self, tmp_path):
        f = tmp_path / "big.txt"
        f.write_text("x" * 25000, encoding="utf-8")
        expanded, _, _ = mm._expand_at_references(f"@{f}")
        assert "truncated" in expanded
        assert "x" * 25000 not in expanded

    def test_binary_file_skipped(self, tmp_path):
        """Regression: a direct @archive.zip reference was read as text with
        errors='replace' and dumped mojibake into the prompt — the binary
        filter only applied to directory expansion."""
        f = tmp_path / "a.zip"
        f.write_bytes(b"PK\x03\x04" + bytes(100))
        expanded, _, _ = mm._expand_at_references(f"@{f}")
        assert "binary file, skipped" in expanded
        assert "PK" not in expanded

    def test_image_becomes_sentinel(self, tmp_path, monkeypatch):
        f = tmp_path / "p.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n")
        monkeypatch.setattr(
            "dashscope_agenticx.utils.images.image_to_data_url",
            lambda p: "data:image/png;base64,AA",
        )
        expanded, images, _ = mm._expand_at_references(f"@{f}")
        assert "<<__AGENTICX_IMG_0__>>" in expanded
        assert images == ["data:image/png;base64,AA"]

    def test_audio_too_large(self, tmp_path, monkeypatch):
        f = tmp_path / "a.mp3"
        f.write_bytes(b"\x00" * (11 * 1024 * 1024))
        expanded, _, audio = mm._expand_at_references(f"@{f}")
        assert "audio too large" in expanded
        assert not audio

    def test_directory_walk_fallback(self, tmp_path, monkeypatch):
        """Force the non-git fallback (git ls-files -> FileNotFoundError)."""
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError),
        )
        (tmp_path / "x.py").write_text("print(1)\n", encoding="utf-8")
        (tmp_path / "y.so").write_bytes(b"\x7fELF")
        sub = tmp_path / ".hidden"
        sub.mkdir()
        (sub / "z.py").write_text("print(2)\n", encoding="utf-8")
        expanded, _, _ = mm._expand_at_references(f"@{tmp_path}")
        assert "1 files" in expanded  # only x.py; .so skipped, .hidden pruned
        assert "print(1)" in expanded
        assert "print(2)" not in expanded

    def test_directory_empty(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError),
        )
        expanded, _, _ = mm._expand_at_references(f"@{tmp_path}")
        assert "directory is empty or has no text files" in expanded

    def test_directory_truncation_cap(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            subprocess,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(FileNotFoundError),
        )
        (tmp_path / "a.txt").write_text("a" * 60000, encoding="utf-8")
        (tmp_path / "b.txt").write_text("b", encoding="utf-8")
        expanded, _, _ = mm._expand_at_references(f"@{tmp_path}")
        assert "... (truncated)" in expanded
        assert "b.txt" not in expanded  # loop breaks right after truncation


class TestToMultimodalContent:
    def test_plain_text_passthrough(self):
        assert mm._to_multimodal_content("hello", [], []) == "hello"

    def test_image_block_splice(self):
        blocks = mm._to_multimodal_content(
            "看这张图 <<__AGENTICX_IMG_0__>> 如何",
            ["data:image/png;base64,AA"],
            [],
        )
        assert (
            blocks[0]["type"] == "text" and blocks[0]["text"].strip() == "看这张图"
        )
        assert blocks[1]["type"] == "image_url"
        assert blocks[2]["text"].strip() == "如何"

    def test_audio_block_splice(self):
        blocks = mm._to_multimodal_content(
            "<<__AGENTICX_AUDIO_0__>>",
            [],
            [{"mime": "audio/mp3", "format": "mp3", "data": "QQ"}],
        )
        assert blocks == [
            {
                "type": "input_audio",
                "input_audio": {"data": "QQ", "format": "mp3"},
            },
        ]

    def test_bad_sentinel_index_tolerated(self):
        blocks = mm._to_multimodal_content(
            "<<__AGENTICX_IMG_9__>>",
            ["u"],
            [],
        )
        assert blocks == [] or blocks == "<<__AGENTICX_IMG_9__>>"


# ---------------------------------------------------------------------------
# completer
# ---------------------------------------------------------------------------


def _doc(text):
    from prompt_toolkit.document import Document

    return Document(text)


class TestExpertCommands:
    @pytest.mark.parametrize("command", EXPERT_COMMAND_ALIASES)
    def test_native_alias_routes_without_starting_agent(
        self,
        monkeypatch,
        command,
    ):
        handler = MagicMock()
        monkeypatch.setattr(
            sys,
            "argv",
            ["agenticx", command[1:], "download", "basic-chat"],
        )
        monkeypatch.setattr(cli_pkg, "_handle_example_command", handler)
        monkeypatch.setattr(
            Config,
            "load",
            MagicMock(side_effect=AssertionError("agent started")),
        )

        cli_pkg.main()

        handler.assert_called_once_with(["download", "basic-chat"])

    @pytest.mark.parametrize("command", EXPERT_COMMAND_ALIASES)
    def test_slash_alias_routes(self, monkeypatch, command):
        handler = MagicMock()
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._handle_example_command",
            handler,
        )

        assert (
            dp._handle_slash_command(
                f"{command} download basic-chat",
                MagicMock(),
                Config(),
            )
            is True
        )
        handler.assert_called_once_with(["download", "basic-chat"])

    def test_similar_prefix_is_not_an_expert_command(self, monkeypatch):
        handler = MagicMock()
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._handle_example_command",
            handler,
        )

        assert (
            dp._handle_slash_command(
                "/examples-extra list",
                MagicMock(),
                Config(),
            )
            is False
        )
        handler.assert_not_called()

    def test_help_advertises_canonical_command_only(self, monkeypatch, capsys):
        from dashscope_agenticx.commands import render_help_text

        monkeypatch.setattr(sys, "argv", ["agenticx", "--help"])
        cli_pkg.main()
        native_help = capsys.readouterr().out
        assert "expert download" in native_help
        assert "expert restore" in native_help
        assert "example download" not in native_help
        slash_help = render_help_text()
        assert "/expert download" in slash_help
        assert "/expert restore" in slash_help
        for alias in EXPERT_COMMAND_ALIASES[1:]:
            assert alias not in slash_help


class TestCompleter:
    def test_top_level_commands(self):
        comp = AcliCompleter(Config())
        out = [c.text for c in comp.get_completions(_doc("/"), None)]
        assert "/help" in out and "/exit" in out
        assert "/expert" in out
        assert not set(EXPERT_COMMAND_ALIASES[1:]) & set(out)

    @pytest.mark.parametrize("command", EXPERT_COMMAND_ALIASES)
    def test_expert_submenus(self, command):
        comp = AcliCompleter(Config())
        out = [
            item.text
            for item in comp.get_completions(_doc(f"{command} "), None)
        ]
        assert out == ["list", "download", "restore"]

    @pytest.mark.parametrize("command", EXPERT_COMMAND_ALIASES)
    def test_expert_download_name_completion_is_local(
        self,
        monkeypatch,
        command,
    ):
        monkeypatch.setattr(
            "dashscope_agenticx.cli.examples._list_remote_examples",
            MagicMock(
                side_effect=AssertionError(
                    "completion must not access network",
                ),
            ),
        )
        comp = AcliCompleter(Config())
        out = [
            item.text
            for item in comp.get_completions(
                _doc(f"{command} download "),
                None,
            )
        ]
        assert out == ["basic-chat", "dashscope-sdk-expert", "bailian-expert"]
        filtered = [
            item.text
            for item in comp.get_completions(
                _doc(f"{command} download dash"),
                None,
            )
        ]
        assert filtered == ["dashscope-sdk-expert"]

    def test_no_completion_for_plain_text(self):
        comp = AcliCompleter(Config())
        assert list(comp.get_completions(_doc("你好"), None)) == []

    def test_capability_enable_lists_only_disabled(self):
        cfg = Config()
        cfg.enabled_capabilities = ["bailian.mcp"]
        comp = AcliCompleter(cfg)
        out = [
            c.text
            for c in comp.get_completions(_doc("/capability enable "), None)
        ]
        assert "bailian.mcp" not in out
        assert "bailian.cli" in out

    def test_capability_disable_lists_only_enabled(self):
        cfg = Config()
        cfg.enabled_capabilities = ["bailian.mcp"]
        comp = AcliCompleter(cfg)
        out = [
            c.text
            for c in comp.get_completions(_doc("/capability disable "), None)
        ]
        assert out == ["bailian.mcp"]

    def test_dev_model_rm_lists_custom_models(self):
        cfg = Config()
        cfg.custom_models = ["openai:gpt-x"]
        comp = AcliCompleter(cfg)
        out = [
            c.text for c in comp.get_completions(_doc("/dev model rm "), None)
        ]
        assert out == ["openai:gpt-x"]

    def test_cron_remove_lists_jobs(self, monkeypatch):
        monkeypatch.setattr(
            cli_pkg,
            "_scheduler",
            types.SimpleNamespace(jobs={"j1": 1, "j2": 2}),
        )
        try:
            comp = AcliCompleter(Config())
            out = [
                c.text
                for c in comp.get_completions(_doc("/cron remove "), None)
            ]
            assert set(out) == {"j1", "j2"}
        finally:
            monkeypatch.setattr(cli_pkg, "_scheduler", None)

    def test_path_completion(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        (tmp_path / "alpha.txt").write_text("a")
        (tmp_path / "alps.py").write_text("b")
        (tmp_path / ".secret").write_text("s")
        (tmp_path / "subdir").mkdir()
        comp = AcliCompleter(Config())
        out = [c.text for c in comp.get_completions(_doc("看 @al"), None)]
        assert "alpha.txt" in out and "alps.py" in out
        # dotfiles hidden without explicit dot prefix
        all_out = [c.text for c in comp.get_completions(_doc("@"), None)]
        assert ".secret" not in all_out
        assert "subdir/" in all_out  # dirs get trailing separator
        # dotfiles appear once the typed prefix itself starts with a dot
        dot_out = [c.text for c in comp.get_completions(_doc("@.s"), None)]
        assert ".secret" in dot_out

    def test_path_completion_missing_dir(self):
        comp = AcliCompleter(Config())
        assert list(comp.get_completions(_doc("@/no/such/dir/x"), None)) == []

    def test_arg_hints(self):
        assert _get_arg_hint("/camera record ") == " <duration> <filename.mp4>"
        assert _get_arg_hint("plain text ") == ""
        assert _get_arg_hint("/camera record") == ""  # no trailing space


class TestSafeFileHistory:
    def test_redacts_provider_keys(self, tmp_path):
        hist = SafeFileHistory(str(tmp_path / "nested" / "hist.txt"))
        hist.store_string("/provider tongyi sk-secret123")
        hist.store_string("普通对话")
        content = (tmp_path / "nested" / "hist.txt").read_text()
        assert "sk-secret123" not in content
        assert "***" in content
        assert "普通对话" in content


# ---------------------------------------------------------------------------
# repl — slash dispatch guard
# ---------------------------------------------------------------------------


class TestReplSlashGuard:
    async def test_handler_exception_does_not_kill_loop(
        self,
        out,
        monkeypatch,
        tmp_path,
    ):
        """A buggy slash handler must print an error and keep the REPL alive
        (previously the exception escaped _run_loop and killed the session)."""
        from dashscope_agenticx.cli import repl

        prompts = ["/boom"]

        class _Session:
            def __init__(self):
                self.calls = 0

            async def prompt_async(self, *a, **k):
                self.calls += 1
                if self.calls <= len(prompts):
                    return prompts[self.calls - 1]
                raise EOFError

        sess = _Session()
        monkeypatch.setattr(repl, "PromptSession", lambda *a, **k: sess)
        monkeypatch.setattr(repl, "console", out)

        agent = MagicMock()
        agent.messages = []
        agent.load_session.return_value = 0

        session_manager = types.SimpleNamespace(
            get_current_topic=lambda: "default",
            get_history_path=lambda *a: tmp_path / "h.json",
            get_input_history_path=lambda *a: tmp_path / "in.txt",
        )

        class _Runtime:
            context = types.SimpleNamespace(session_manager=session_manager)
            executor = MagicMock()
            aclose = AsyncMock()

            def activate(self):
                from contextlib import nullcontext

                return nullcontext()

            def create_agent(self, **_kwargs):
                return agent

        class _Builder:
            def __init__(self, *_args, **_kwargs):
                pass

            def build(self):
                return _Runtime()

        monkeypatch.setattr(
            "dashscope_agenticx.runtime.RuntimeBuilder",
            _Builder,
        )
        monkeypatch.setattr(repl, "ensure_provider_key", lambda c, a: None)
        monkeypatch.setattr(repl, "_print_banner", lambda c: None)
        monkeypatch.setattr(
            repl,
            "sync_extensions_into_catalog",
            lambda e: None,
        )
        monkeypatch.setattr(repl, "WORKSPACE_DIR", tmp_path)
        cfg_file = tmp_path / "config.toml"
        cfg_file.write_text("")
        monkeypatch.setattr(repl, "WORKSPACE_CONFIG_FILE", cfg_file)
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.apply_extensions",
            lambda pm: types.SimpleNamespace(errors=[], capabilities=[]),
        )
        monkeypatch.setattr(
            "dashscope_agenticx.tools.platform.register_platform_tools",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.skills.get_skill_manager",
            lambda: types.SimpleNamespace(
                _registry_url="",
                _global=False,
                load=lambda **k: None,
            ),
        )
        monkeypatch.setattr(
            "dashscope_agenticx.tools.session.register_session_tools",
            lambda *a, **k: None,
        )
        monkeypatch.setattr(repl, "_init_mcp_servers", AsyncMock())

        def _boom(cmd, agent, config):
            raise RuntimeError("handler exploded")

        monkeypatch.setattr(repl, "_handle_slash_command", _boom)

        cfg = Config()
        cfg.session_persist = False
        cfg.memory_enabled = False
        await repl._run_loop(cfg)

        assert sess.calls == 2  # loop survived the exception, prompted again
        assert "Command error: handler exploded" in out.text


class TestOneshotFlagOverrides:
    """`-c` returns from main() early — overrides must still be applied."""

    def test_oneshot_applies_max_turns_and_protocol(self, monkeypatch):
        captured = {}

        async def fake_oneshot(config, prompt):
            captured["max_turns"] = config.max_turns
            captured["protocol"] = config.protocol
            captured["prompt"] = prompt

        monkeypatch.setattr(cli_pkg, "_run_oneshot", fake_oneshot)
        monkeypatch.setattr(
            cli_pkg.Config,
            "load",
            classmethod(lambda cls: Config()),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "agenticx",
                "-c",
                "do it",
                "--max-turns",
                "150",
                "--protocol",
                "anthropic",
            ],
        )

        cli_pkg.main()

        assert captured["max_turns"] == 150
        assert captured["protocol"] == "anthropic"
        assert captured["prompt"] == "do it"

    def test_oneshot_rejects_non_integer_max_turns(self, monkeypatch):
        async def fake_oneshot(config, prompt):
            raise AssertionError("oneshot ran despite a bad --max-turns")

        monkeypatch.setattr(cli_pkg, "_run_oneshot", fake_oneshot)
        monkeypatch.setattr(
            cli_pkg.Config,
            "load",
            classmethod(lambda cls: Config()),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            ["agenticx", "-c", "do it", "--max-turns", "many"],
        )

        with pytest.raises(SystemExit):
            cli_pkg.main()


class TestUnknownCommandRouting:
    """Only a bare `agenticx` and `-c/--command` enter the agent.

    Same contract the dashscope CLI enforces: an unrecognized token must fail
    loudly instead of silently opening a chat and discarding the command line.
    """

    @staticmethod
    def _refuse_agent(monkeypatch):
        def refuse(*args, **kwargs):
            raise AssertionError("agent started for an unrecognized token")

        monkeypatch.setattr(cli_pkg, "_run_loop", refuse)
        monkeypatch.setattr(cli_pkg, "_run_tui_mode", refuse)
        monkeypatch.setattr(cli_pkg, "_run_dry_run", refuse)
        monkeypatch.setattr(
            cli_pkg.Config,
            "load",
            classmethod(lambda cls: Config()),
        )

    def test_unknown_command_exits_with_usage_error(self, monkeypatch, capsys):
        self._refuse_agent(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["agenticx", "auth", "whoami"])

        with pytest.raises(SystemExit) as exc:
            cli_pkg.main()

        # 2 is the usage-error code typer/click exit with for the same case.
        assert exc.value.code == 2
        assert "No such command 'auth'" in capsys.readouterr().out

    def test_quoted_command_line_is_not_a_prompt(self, monkeypatch, capsys):
        """One shell word holding a whole command line is still a bad command,
        not a question — the decision the dashscope CLI makes too."""
        self._refuse_agent(monkeypatch)
        monkeypatch.setattr(sys, "argv", ["agenticx", "auth whoami"])

        with pytest.raises(SystemExit) as exc:
            cli_pkg.main()

        assert exc.value.code == 2
        out = capsys.readouterr().out
        assert "No such command 'auth whoami'" in out

    def test_bare_agenticx_still_enters_the_agent(self, monkeypatch):
        started: list = []

        async def fake_loop(config):
            started.append("loop")

        def fake_tui(config):
            started.append("tui")

        monkeypatch.setattr(cli_pkg, "_run_loop", fake_loop)
        monkeypatch.setattr(cli_pkg, "_run_tui_mode", fake_tui)
        monkeypatch.setattr(
            cli_pkg.Config,
            "load",
            classmethod(lambda cls: Config()),
        )
        monkeypatch.setattr(sys, "argv", ["agenticx"])

        cli_pkg.main()

        assert started in (["loop"], ["tui"])

    def test_stripped_flags_do_not_look_like_commands(self, monkeypatch):
        """`--cli/--tui/--dry-run/--protocol/--max-turns` are consumed before
        the command check, so they must not be reported as unknown."""
        started: list = []

        async def fake_loop(config):
            started.append("loop")

        monkeypatch.setattr(cli_pkg, "_run_loop", fake_loop)
        monkeypatch.setattr(cli_pkg, "_run_tui_mode", lambda c: None)
        monkeypatch.setattr(
            cli_pkg.Config,
            "load",
            classmethod(lambda cls: Config()),
        )
        monkeypatch.setattr(
            sys,
            "argv",
            [
                "agenticx",
                "--cli",
                "--protocol",
                "openai",
                "--max-turns",
                "7",
            ],
        )

        cli_pkg.main()

        assert started == ["loop"]
