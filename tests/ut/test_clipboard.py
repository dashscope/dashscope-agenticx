# -*- coding: utf-8 -*-
"""Tests for utils/clipboard.py."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from dashscope_agenticx.utils import clipboard


def test_prefers_pbcopy(monkeypatch):
    calls = []
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )
    monkeypatch.setattr(
        clipboard.subprocess,
        "run",
        lambda cmd, **kw: calls.append((cmd, kw["input"]))
        or SimpleNamespace(returncode=0),
    )
    tool = clipboard.copy_to_clipboard("hello")
    assert tool == "pbcopy"
    assert calls == [(["pbcopy"], b"hello")]


def test_falls_back_to_xclip(monkeypatch):
    available = {"xclip": True}
    calls = []
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: "/usr/bin/" + name if name in available else None,
    )
    monkeypatch.setattr(
        clipboard.subprocess,
        "run",
        lambda cmd, **kw: calls.append(cmd) or SimpleNamespace(returncode=0),
    )
    assert clipboard.copy_to_clipboard("x") == "xclip"
    assert calls == [["xclip", "-selection", "clipboard"]]


def test_returns_none_without_tools(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: None)
    assert clipboard.copy_to_clipboard("x") is None


def test_skips_tool_that_raises(monkeypatch):
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: f"/usr/bin/{name}",
    )

    def run(cmd, **kw):
        if cmd[0] == "pbcopy":
            raise OSError("broken pipe")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.copy_to_clipboard("x") == "xclip"


def test_encodes_unicode(monkeypatch):
    captured = {}
    monkeypatch.setattr(
        clipboard.shutil,
        "which",
        lambda name: "/usr/bin/pbcopy",
    )
    monkeypatch.setattr(
        clipboard.subprocess,
        "run",
        lambda cmd, **kw: captured.update(input=kw["input"])
        or SimpleNamespace(returncode=0),
    )
    clipboard.copy_to_clipboard("中文 ✓")
    assert captured["input"] == "中文 ✓".encode("utf-8")


def test_skips_tool_with_nonzero_exit(monkeypatch):
    calls = []
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name)

    def run(command, **kwargs):
        calls.append(command[0])
        return SimpleNamespace(returncode=1 if command[0] == "pbcopy" else 0)

    monkeypatch.setattr(clipboard.subprocess, "run", run)
    assert clipboard.copy_to_clipboard("text") == "xclip"
    assert calls == ["pbcopy", "xclip"]


def test_returns_none_when_all_tools_fail(monkeypatch):
    monkeypatch.setattr(clipboard.shutil, "which", lambda name: name)
    monkeypatch.setattr(
        clipboard.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(returncode=1),
    )
    assert clipboard.copy_to_clipboard("text") is None


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_macos_pasteboard_is_not_loaded_elsewhere(monkeypatch, platform):
    monkeypatch.setattr(clipboard.sys, "platform", platform)
    clipboard.get_macos_clipboard.cache_clear()
    try:
        assert clipboard.get_macos_clipboard() is None
    finally:
        clipboard.get_macos_clipboard.cache_clear()


def test_macos_pasteboard_failure_is_optional(monkeypatch):
    monkeypatch.setattr(clipboard.sys, "platform", "darwin")
    clipboard.get_macos_clipboard.cache_clear()

    def unavailable():
        raise OSError("framework unavailable")

    monkeypatch.setattr(clipboard, "MacOSClipboard", unavailable)
    try:
        assert clipboard.get_macos_clipboard() is None
    finally:
        clipboard.get_macos_clipboard.cache_clear()


def test_native_clipboard_string_preserves_unicode_and_nulls():
    import ctypes

    text = "中文\x00第二行\n✓"
    encoded = text.encode("utf-8")
    buffer = ctypes.create_string_buffer(encoded)
    pasteboard = clipboard.MacOSClipboard.__new__(clipboard.MacOSClipboard)
    pasteboard._ctypes = ctypes
    pasteboard._selector = lambda name: name
    pasteboard._pointer = lambda *args: ctypes.addressof(buffer)
    pasteboard._length = lambda *args: len(encoded)
    assert pasteboard._string_value(1) == text
    assert pasteboard._string_value(None) is None


def test_native_pasteboard_read_errors_are_nonfatal():
    pasteboard = clipboard.MacOSClipboard.__new__(clipboard.MacOSClipboard)

    def unavailable():
        raise OSError("pasteboard unavailable")

    pasteboard._pool = unavailable
    assert pasteboard.change_count() is None
    assert pasteboard.read_text() is None


@pytest.mark.parametrize(
    "text,expected",
    [
        ("line 20\nline 21", True),
        ("  line 20  \r\n  line 21  ", True),
        ("line 21\nline 22", True),
        ("line 21", False),
        ("line 2", False),
        ("unrelated\nline 21", False),
        (None, False),
        ("", False),
    ],
)
def test_matches_only_known_visible_selection_lines(text, expected):
    candidates = ("line 20\nline 21\nline 22",)
    assert clipboard.matches_native_selection(text, candidates) is expected
