# -*- coding: utf-8 -*-
"""--cli startup must reset stuck mouse-reporting modes left behind by a
hard-killed TUI session, otherwise iTerm2 keeps sending wheel events to the
app and the scrollback never scrolls."""

from __future__ import annotations

import io

from dashscope_agenticx.cli import repl


class TestResetTerminalModes:
    def test_writes_mouse_reporting_reset_on_tty(self, monkeypatch):
        buf = io.StringIO()
        buf.isatty = lambda: True
        monkeypatch.setattr(repl.sys, "stdout", buf)
        repl._reset_terminal_modes()
        out = buf.getvalue()
        assert "\x1b[?1000l" in out
        assert "\x1b[?1002l" in out
        assert "\x1b[?1003l" in out
        assert "\x1b[?1006l" in out

    def test_no_output_when_not_tty(self, monkeypatch):
        buf = io.StringIO()
        monkeypatch.setattr(repl.sys, "stdout", buf)
        repl._reset_terminal_modes()
        assert buf.getvalue() == ""
