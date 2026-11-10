# -*- coding: utf-8 -*-
"""Unit tests for dashscope_agenticx.ui.tts — fully mocked, no user config, no audio devices."""

from __future__ import annotations

import sys
from types import ModuleType

import pytest

from dashscope_agenticx.ui import tts
from dashscope_agenticx.ui.tts import (
    DEFAULT_VOICE,
    StreamingTTSFeeder,
    TTSPlayer,
    _split_text,
    _strip_markdown,
    is_available,
    speak_text,
)


@pytest.fixture(autouse=True)
def mocked_sounddevice(monkeypatch):
    monkeypatch.setitem(sys.modules, "sounddevice", ModuleType("sounddevice"))


class TestIsAvailable:
    def test_ok_when_deps_importable(self):
        ok, err = is_available()
        assert ok is True
        assert err == ""

    def test_missing_dashscope(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "dashscope", None)
        ok, err = is_available()
        assert ok is False
        assert "agenticx[voice]" in err

    def test_missing_sounddevice(self, monkeypatch):
        monkeypatch.setitem(sys.modules, "sounddevice", None)
        ok, err = is_available()
        assert ok is False
        assert "agenticx[voice]" in err

    @pytest.mark.parametrize("error_type", [OSError, Exception])
    def test_audio_backend_initialization_failure(
        self,
        monkeypatch,
        error_type,
    ):
        import builtins

        original_import = builtins.__import__

        def failing_import(name, *args, **kwargs):
            if name == "sounddevice":
                raise error_type("audio backend unavailable")
            return original_import(name, *args, **kwargs)

        monkeypatch.setattr(builtins, "__import__", failing_import)
        ok, err = is_available()
        assert ok is False
        assert "audio backend" in err


class TestStripMarkdown:
    def test_code_block_replaced(self):
        out = _strip_markdown("前文```py\nprint(1)\n```后文")
        assert "print" not in out
        assert "code block omitted" in out

    def test_inline_code_kept_text(self):
        assert _strip_markdown("运行 `ls -la` 即可") == "运行 ls -la 即可"

    def test_bold_italic_markers_removed(self):
        assert _strip_markdown("**粗** 和 *斜* 和 __b__ 和 _i_") == "粗 和 斜 和 b 和 i"

    def test_link_keeps_label_drops_url(self):
        assert _strip_markdown("见 [文档](https://example.com/x)") == "见 文档"

    def test_image_dropped(self):
        assert _strip_markdown("图 ![alt](http://x/y.png) 完") == "图  完"

    def test_headers_and_bullets(self):
        out = _strip_markdown("## 标题\n- 甲\n* 乙")
        assert "#" not in out
        assert "- 甲" not in out
        assert "甲" in out and "乙" in out

    def test_blank_lines_collapsed(self):
        assert "\n\n\n" not in _strip_markdown("a\n\n\n\nb")


class TestSplitText:
    def test_short_text_single_chunk(self):
        assert _split_text("短句。") == ["短句。"]

    def test_empty_text(self):
        assert _split_text("") == []

    def test_splits_on_sentence_boundary(self):
        text = "甲" * 100 + "。" + "乙" * 100 + "。"
        chunks = _split_text(text, max_len=120)
        assert len(chunks) == 2
        assert chunks[0].endswith("。")
        assert "".join(chunks) == text

    def test_forced_split_without_boundary(self):
        text = "啊" * 300
        chunks = _split_text(text, max_len=100)
        assert len(chunks) == 3
        assert all(len(c) <= 100 for c in chunks)
        assert "".join(chunks) == text

    def test_no_empty_chunks(self):
        chunks = _split_text("啊。" * 200, max_len=50)
        assert all(c.strip() for c in chunks)


@pytest.fixture()
def player():
    return TTSPlayer("sk-test", model="cosyvoice-v2")


class TestTTSPlayerConfig:
    @pytest.mark.parametrize("player_class", [TTSPlayer, StreamingTTSFeeder])
    def test_model_name_normalized_before_voice_selection(self, player_class):
        player = player_class("sk-test", model=" COSYVOICE-V1 ")
        assert player.model == "cosyvoice-v1"
        assert player.voice == DEFAULT_VOICE["cosyvoice-v1"]

    def test_set_model_normalizes_name_before_validation(self, player):
        player.set_model(" COSYVOICE-V1 ")
        assert player.model == "cosyvoice-v1"

    def test_default_voice_for_model(self, player):
        assert player.voice == DEFAULT_VOICE["cosyvoice-v2"]

    def test_set_model_resets_incompatible_voice(self, player):
        player.set_model("cosyvoice-v1")
        assert player.model == "cosyvoice-v1"
        assert player.voice == DEFAULT_VOICE["cosyvoice-v1"]

    def test_set_model_keeps_unknown_model_unchanged(self, player):
        player.set_model("no-such-model")
        assert player.model == "cosyvoice-v2"

    def test_set_speed_clamped(self, player):
        player.set_speed(9.9)
        assert player.speech_rate == 2.0
        player.set_speed(0.01)
        assert player.speech_rate == 0.5
        player.set_speed(1.25)
        assert player.speech_rate == 1.25


class TestSpeakEarlyReturns:
    def test_missing_deps_returns_error(self, player, monkeypatch):
        monkeypatch.setattr(tts, "is_available", lambda: (False, "缺依赖"))
        assert player.speak("你好") == "缺依赖"

    def test_empty_text_is_noop(self, player):
        assert player.speak("   ") is None

    def test_cancel_between_chunks_stops_loop(self, player, monkeypatch):
        called = []

        def fake_synth(text):
            called.append(text)
            player._cancel.set()
            return None

        monkeypatch.setattr(player, "_synthesize_and_play", fake_synth)
        # 3600 chars > MAX_TEXT_LENGTH → multiple chunks
        assert player.speak("一。二。" * 900) is None
        assert len(called) == 1

    def test_error_from_synth_returned(self, player, monkeypatch):
        monkeypatch.setattr(
            player,
            "_synthesize_and_play",
            lambda t: "合成炸了",
        )
        assert player.speak("你好。") == "合成炸了"
        assert player.is_playing is False

    def test_playing_flag_reset_after_success(self, player, monkeypatch):
        monkeypatch.setattr(player, "_synthesize_and_play", lambda t: None)
        assert player.speak("你好。") is None
        assert player.is_playing is False


class TestSpeakText:
    def test_delegates_to_player(self, monkeypatch):
        seen = {}

        def fake_speak(self, text, *, strip_md=True):
            seen["text"] = text
            seen["voice"] = self.voice
            return None

        monkeypatch.setattr(TTSPlayer, "speak", fake_speak)
        err = speak_text("sk-x", "**你好**", voice="longwan_v2")
        assert err is None
        assert seen["text"] == "**你好**"
        assert seen["voice"] == "longwan_v2"


@pytest.fixture()
def feeder(monkeypatch):
    """Feeder whose TTSPlayer.speak is mocked — no network, no audio."""
    spoken: list[str] = []
    monkeypatch.setattr(
        TTSPlayer,
        "speak",
        lambda self, text, *, strip_md=True: spoken.append(text),
    )
    f = StreamingTTSFeeder("sk-test")
    f._spoken = spoken
    return f


class TestStreamingFeeder:
    def test_sentence_queued_after_boundary(self, feeder):
        feeder.start()
        feeder.feed("第一句话。")
        feeder.finish()
        assert feeder._spoken == ["第一句话。"]

    def test_partial_sentence_flushed_by_finish(self, feeder):
        feeder.start()
        feeder.feed("没有标点的尾巴")
        feeder.finish()
        assert feeder._spoken == ["没有标点的尾巴"]

    def test_code_block_suppressed_until_closed(self, feeder):
        feeder.start()
        feeder.feed("前面。\n```python\nprint(1)\n")
        feeder.feed("```\n后面。")
        feeder.finish()
        assert "print" not in " ".join(feeder._spoken)
        assert "前面。" in feeder._spoken
        assert any("后面。" in s for s in feeder._spoken)

    def test_multiple_sentences_in_order(self, feeder):
        feeder.start()
        feeder.feed("甲。 乙！ 丙？")
        feeder.finish()
        assert feeder._spoken == ["甲。", "乙！", "丙？"]

    def test_worker_error_propagates(self, monkeypatch):
        monkeypatch.setattr(
            TTSPlayer,
            "speak",
            lambda self, text, *, strip_md=True: "boom",
        )
        f = StreamingTTSFeeder("sk-test")
        f.start()
        f.feed("会失败。")
        err = f.finish()
        assert err == "boom"

    def test_cancel_stops_worker(self, feeder):
        feeder.start()
        feeder.cancel()
        assert feeder._stop_event.is_set()
        feeder._done_event.wait(timeout=5)
        assert feeder._done_event.is_set()

    def test_idle_worker_does_not_hold_queue_lock(self, feeder, monkeypatch):
        """Regression: time.sleep in the idle loop must run outside
        _queue_lock, otherwise feed()/finish() block ~50ms per cycle."""
        violations = []

        real_sleep = tts.time.sleep

        def checking_sleep(seconds):
            if not feeder._queue_lock.acquire(blocking=False):
                violations.append(seconds)
                real_sleep(seconds)
                return
            feeder._queue_lock.release()
            real_sleep(seconds)

        monkeypatch.setattr(tts.time, "sleep", checking_sleep)
        feeder.start()
        # Let the worker hit the idle path a few times.
        real_sleep(0.3)
        feeder.cancel()
        feeder._done_event.wait(timeout=5)
        assert violations == []

    def test_feed_during_idle_not_blocked(self, feeder):
        feeder.start()
        tts.time.sleep(0.2)  # worker now idling
        acquired = feeder._queue_lock.acquire(timeout=0.5)
        feeder._queue_lock.release()
        assert acquired
        feeder.cancel()
        feeder._done_event.wait(timeout=5)
