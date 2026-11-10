# -*- coding: utf-8 -*-
"""Tests for cli/streaming.py TTS feeder lifecycle (M13 thread-leak fix)."""

from __future__ import annotations

from types import SimpleNamespace

import dashscope_agenticx.ui.tts as tts_mod
from dashscope_agenticx.cli.streaming import _stream_response


class _SpyThread:
    def __init__(self):
        self.join_calls: list = []

    def join(self, timeout=None):
        self.join_calls.append(timeout)


class _SpyFeeder:
    def __init__(self, **kwargs):
        self.started = False
        self.fed: list[str] = []
        self.finish_calls = 0
        self.cancel_calls = 0
        self._worker_thread = _SpyThread()

    def start(self):
        self.started = True

    def feed(self, text: str):
        self.fed.append(text)

    def finish(self):
        self.finish_calls += 1
        return None

    def cancel(self):
        self.cancel_calls += 1


class _FakeAgent:
    def __init__(self, chunks):
        self._chunks = chunks
        self.messages: list = []
        self.last_output = None

    async def run_stream(self, user_input):
        for chunk in self._chunks:
            yield chunk


def _make_feeder(monkeypatch) -> _SpyFeeder:
    feeder = _SpyFeeder()
    monkeypatch.setattr(tts_mod, "is_available", lambda: (True, None))
    monkeypatch.setattr(tts_mod, "StreamingTTSFeeder", lambda **kw: feeder)
    return feeder


def _config() -> SimpleNamespace:
    return SimpleNamespace(
        tts_enabled=True,
        tongyi_api_key="k",
        tts_model="m",
        tts_voice="v",
        tts_speed=1.0,
    )


class TestStreamingTTSFeederLifecycle:
    async def test_empty_buffer_turn_stops_feeder_thread(self, monkeypatch):
        """A turn producing no text must still stop+join the worker thread."""
        feeder = _make_feeder(monkeypatch)
        agent = _FakeAgent(chunks=[])

        await _stream_response(agent, _config(), "hi")

        assert feeder.started
        assert feeder.finish_calls == 0
        assert feeder.cancel_calls == 1
        assert len(feeder._worker_thread.join_calls) == 1

    async def test_non_empty_turn_finishes_and_joins(self, monkeypatch):
        feeder = _make_feeder(monkeypatch)
        agent = _FakeAgent(chunks=["你好。"])

        await _stream_response(agent, _config(), "hi")

        assert feeder.fed == ["你好。"]
        assert feeder.finish_calls == 1
        assert feeder.cancel_calls == 0
        assert len(feeder._worker_thread.join_calls) == 1

    async def test_error_turn_still_stops_feeder(self, monkeypatch):
        """Exceptions during streaming must not skip feeder cleanup."""
        feeder = _make_feeder(monkeypatch)

        class _BoomAgent(_FakeAgent):
            async def run_stream(self, user_input):
                yield "部分输出"
                raise RuntimeError("流式中断")

        agent = _BoomAgent(chunks=[])
        await _stream_response(agent, _config(), "hi")

        assert feeder.finish_calls + feeder.cancel_calls == 1
        assert len(feeder._worker_thread.join_calls) == 1
