# -*- coding: utf-8 -*-
"""M8 edge-case regressions: voice cancel-event lifecycle, camera degradation
strings, _tui_input timeout/concurrency races, embedded.run delegation."""

from __future__ import annotations

import subprocess
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual.widgets import RichLog

from dashscope_agenticx.ui import camera, voice
from dashscope_agenticx.ui.tui import AgenticXApp, CommandInput
from tests.ut.test_tui_scroll import _make_app, _wait_for


@pytest.fixture(autouse=True)
def _clean_voice_global():
    voice._voice_cancel_event = None
    yield
    voice._voice_cancel_event = None


class TestVoiceCancelEvent:
    def test_no_active_session_returns_false(self):
        assert voice.try_cancel_voice_input() is False

    def test_active_session_cancelled_once(self):
        voice._voice_cancel_event = threading.Event()
        assert voice.try_cancel_voice_input() is True
        assert voice._voice_cancel_event.is_set()
        # Already cancelled — second call must not report success again.
        assert voice.try_cancel_voice_input() is False

    async def test_completed_session_clears_global(self, monkeypatch):
        """Regression: after a finished recording the global event must be
        cleared, otherwise a later /voice off falsely reports a cancel."""

        async def fake_impl(*args):
            return "识别文本"

        monkeypatch.setattr(voice, "_voice_input_impl", fake_impl)
        text = await voice.voice_input("sk-test")
        assert text == "识别文本"
        assert voice._voice_cancel_event is None
        assert voice.try_cancel_voice_input() is False

    async def test_impl_error_still_clears_global(self, monkeypatch):
        async def fake_impl(*args):
            raise RuntimeError("asr exploded")

        monkeypatch.setattr(voice, "_voice_input_impl", fake_impl)
        with pytest.raises(RuntimeError):
            await voice.voice_input("sk-test")
        assert voice._voice_cancel_event is None

    async def test_caller_event_reused_for_cancel(self, monkeypatch):
        seen = {}

        async def fake_impl(
            api_key,
            model,
            display_callback,
            cancel_event,
            *a,
        ):
            seen["event"] = cancel_event
            return None

        monkeypatch.setattr(voice, "_voice_input_impl", fake_impl)
        caller_event = threading.Event()
        await voice.voice_input("sk-test", cancel_event=caller_event)
        # The caller's own event is passed straight through to the impl.
        assert seen["event"] is caller_event


class TestVoiceDegradation:
    async def test_missing_deps_reports_error(self, monkeypatch):
        monkeypatch.setattr(voice, "is_available", lambda: (False, "缺依赖-x"))
        shown: list[str] = []
        result = await voice._voice_input_impl(
            "sk",
            display_callback=shown.append,
        )
        assert result is None
        assert any("缺依赖-x" in s for s in shown)

    async def test_asr_start_failure_reports_error(self, monkeypatch):
        monkeypatch.setattr(voice, "is_available", lambda: (True, ""))

        class FakeASR:
            def __init__(self, api_key, model):
                pass

            def start(self):
                raise RuntimeError("连接拒绝")

        monkeypatch.setattr(voice, "_RealtimeASR", FakeASR)
        shown: list[str] = []
        result = await voice._voice_input_impl(
            "sk",
            display_callback=shown.append,
        )
        assert result is None
        assert any("连接拒绝" in s for s in shown)


class TestCameraDegradation:
    def test_capture_without_deps(self, monkeypatch):
        monkeypatch.setattr(camera, "is_available", lambda: (False, ""))
        msg = camera.capture("/tmp/x.jpg")
        assert msg.startswith("Error")
        assert "agenticx[camera]" in msg

    def test_record_without_deps(self, monkeypatch):
        monkeypatch.setattr(camera, "is_available", lambda: (False, ""))
        assert camera.record("/tmp/x.mp4").startswith("Error")

    def test_capture_unknown_backend(self, monkeypatch):
        monkeypatch.setattr(camera, "is_available", lambda: (True, "mystery"))
        assert "unknown camera backend" in camera.capture("/tmp/x.jpg")

    def test_record_unknown_backend(self, monkeypatch):
        monkeypatch.setattr(camera, "is_available", lambda: (True, "mystery"))
        assert "unknown camera backend" in camera.record("/tmp/x.mp4")

    def test_record_ffmpeg_missing(self, monkeypatch):
        monkeypatch.setattr(
            camera,
            "is_available",
            lambda: (True, "imagesnap"),
        )
        monkeypatch.setattr(camera.shutil, "which", lambda name: None)
        msg = camera.record("/tmp/x.mp4")
        assert "ffmpeg" in msg and msg.startswith("Error")

    def test_imagesnap_timeout(self, monkeypatch):
        monkeypatch.setattr(
            camera,
            "is_available",
            lambda: (True, "imagesnap"),
        )

        def boom(*a, **kw):
            raise subprocess.TimeoutExpired(cmd="imagesnap", timeout=10)

        monkeypatch.setattr(camera.subprocess, "run", boom)
        assert "timed out" in camera.capture("/tmp/x.jpg")

    def test_imagesnap_not_installed(self, monkeypatch):
        monkeypatch.setattr(
            camera,
            "is_available",
            lambda: (True, "imagesnap"),
        )

        def boom(*a, **kw):
            raise FileNotFoundError("imagesnap")

        monkeypatch.setattr(camera.subprocess, "run", boom)
        assert "imagesnap" in camera.capture("/tmp/x.jpg")

    def test_imagesnap_nonzero_exit(self, monkeypatch):
        monkeypatch.setattr(
            camera,
            "is_available",
            lambda: (True, "imagesnap"),
        )
        monkeypatch.setattr(
            camera.subprocess,
            "run",
            lambda *a, **kw: SimpleNamespace(returncode=1, stderr="设备忙"),
        )
        assert "设备忙" in camera.capture("/tmp/x.jpg")


class TestTuiInlineInput:
    async def test_prompt_roundtrip(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            result: list[str] = []
            worker = threading.Thread(
                target=lambda: result.append(app._tui_input("请输入名字")),
                daemon=True,
            )
            worker.start()
            assert await _wait_for(lambda: app._inline_input_active)
            assert await _wait_for(
                lambda: any("请输入名字" in s.text for s in output.lines),
            )

            app.on_command_input_submitted(CommandInput.Submitted("小明"))
            assert await _wait_for(lambda: result, timeout=2)
            worker.join(timeout=1)
            assert result == ["小明"]
            assert app._inline_input_active is False

    async def test_timeout_deactivates_inline_mode(self):
        """Regression: the 300s timeout path used to leave
        _inline_input_active=True, silently swallowing the next command."""
        app = _make_app()
        app._INLINE_INPUT_TIMEOUT = 0.2
        async with app.run_test(size=(80, 30)):
            result: list[str] = []
            worker = threading.Thread(
                target=lambda: result.append(app._tui_input("等不到回答")),
                daemon=True,
            )
            worker.start()
            assert await _wait_for(lambda: app._inline_input_active)
            assert await _wait_for(lambda: result, timeout=2)
            worker.join(timeout=1)
            assert result == [""]
            assert app._inline_input_active is False
            assert app._inline_input_future is None

    async def test_concurrent_prompt_rejected(self):
        """A second interactive command must not clobber the first prompt's
        future — it returns empty immediately."""
        app = _make_app()
        async with app.run_test(size=(80, 30)):
            first: list[str] = []
            second: list[str] = []
            t1 = threading.Thread(
                target=lambda: first.append(app._tui_input("第一个")),
                daemon=True,
            )
            t1.start()
            assert await _wait_for(lambda: app._inline_input_active)

            t2 = threading.Thread(
                target=lambda: second.append(app._tui_input("第二个")),
                daemon=True,
            )
            t2.start()
            assert await _wait_for(lambda: second, timeout=2)
            t2.join(timeout=1)
            assert second == [""]

            # First prompt still works after the rejected second attempt.
            app.on_command_input_submitted(CommandInput.Submitted("答案"))
            assert await _wait_for(lambda: first, timeout=2)
            t1.join(timeout=1)
            assert first == ["答案"]

    async def test_getpass_marks_password_mode(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            input_widget = app.query_one("#command-input", CommandInput)
            result: list[str] = []
            worker = threading.Thread(
                target=lambda: result.append(app._tui_getpass("输入密钥")),
                daemon=True,
            )
            worker.start()
            assert await _wait_for(lambda: app._inline_input_active)
            assert await _wait_for(lambda: input_widget.password_mode)

            app.on_command_input_submitted(CommandInput.Submitted("s3cret"))
            assert await _wait_for(lambda: result, timeout=2)
            worker.join(timeout=1)
            assert result == ["s3cret"]
            assert input_widget.password_mode is False


class TestEmbeddedRun:
    def _fake_config(self, **overrides):
        cfg = SimpleNamespace(
            api_key="",
            base_url=None,
            tui=False,
            provider="tongyi",
            model="m",
            max_turns=10,
        )
        for k, v in overrides.items():
            setattr(cfg, k, v)
        return cfg

    def test_oneshot_delegates_with_overrides(self, monkeypatch):
        import dashscope_agenticx.ui.embedded as embedded

        cfg = self._fake_config()
        monkeypatch.setattr(
            "dashscope_agenticx.config.Config.load",
            MagicMock(return_value=cfg),
        )
        seen = {}

        async def fake_oneshot(config, prompt, system_prompt):
            seen["prompt"] = prompt
            seen["system_prompt"] = system_prompt

        monkeypatch.setattr(embedded, "_run_oneshot_embedded", fake_oneshot)
        embedded.run(
            command="查一下",
            system_prompt="你是助手",
            api_key="sk-caller",
            app_name="MyApp",
            prompt_symbol="你> ",
        )
        assert seen["prompt"] == "查一下"
        assert seen["system_prompt"] == "你是助手"
        # Caller key fills the missing slot; identity attrs are injected.
        assert cfg.api_key == "sk-caller"
        assert cfg._embedded_app_name == "MyApp"
        assert cfg._embedded_prompt_symbol == "你> "
        assert cfg._embedded_system_prompt == "你是助手"

    def test_saved_key_takes_priority_over_caller(self, monkeypatch):
        import dashscope_agenticx.ui.embedded as embedded

        cfg = self._fake_config(api_key="sk-saved")
        monkeypatch.setattr(
            "dashscope_agenticx.config.Config.load",
            MagicMock(return_value=cfg),
        )

        async def fake_oneshot(*a):
            pass

        monkeypatch.setattr(embedded, "_run_oneshot_embedded", fake_oneshot)
        embedded.run(command="x", api_key="sk-caller")
        assert cfg.api_key == "sk-saved"

    def test_tui_mode_delegates(self, monkeypatch):
        import dashscope_agenticx.ui.embedded as embedded

        cfg = self._fake_config(tui=True)
        monkeypatch.setattr(
            "dashscope_agenticx.config.Config.load",
            MagicMock(return_value=cfg),
        )
        mock_tui = MagicMock()
        monkeypatch.setattr("dashscope_agenticx.cli._run_tui_mode", mock_tui)
        embedded.run()
        mock_tui.assert_called_once_with(cfg)

    def test_interactive_loop_delegates(self, monkeypatch):
        import dashscope_agenticx.ui.embedded as embedded

        cfg = self._fake_config(tui=False)
        monkeypatch.setattr(
            "dashscope_agenticx.config.Config.load",
            MagicMock(return_value=cfg),
        )
        seen = {}

        async def fake_loop(config):
            seen["config"] = config

        monkeypatch.setattr(embedded, "_run_loop_embedded", fake_loop)
        embedded.run(tui=False)
        assert seen["config"] is cfg

    def test_tui_override_wins_over_config(self, monkeypatch):
        import dashscope_agenticx.ui.embedded as embedded

        cfg = self._fake_config(tui=True)
        monkeypatch.setattr(
            "dashscope_agenticx.config.Config.load",
            MagicMock(return_value=cfg),
        )
        seen = {}

        async def fake_loop(config):
            seen["config"] = config

        monkeypatch.setattr(embedded, "_run_loop_embedded", fake_loop)
        embedded.run(tui=False)
        assert cfg.tui is False
        assert seen["config"] is cfg

    def test_keyboard_interrupt_swallowed(self, monkeypatch):
        import dashscope_agenticx.ui.embedded as embedded

        cfg = self._fake_config()
        monkeypatch.setattr(
            "dashscope_agenticx.config.Config.load",
            MagicMock(return_value=cfg),
        )

        async def boom(config):
            raise KeyboardInterrupt

        monkeypatch.setattr(embedded, "_run_loop_embedded", boom)
        embedded.run()  # must not raise
