# -*- coding: utf-8 -*-
"""Tests for sandbox.py — OS sandbox detection, profiles, argv building."""

from __future__ import annotations

import pytest

from dashscope_agenticx import sandbox


@pytest.fixture(autouse=True)
def _reset_override():
    """Ensure the enablement override never leaks between tests."""
    sandbox.set_enabled(None)
    yield
    sandbox.set_enabled(None)


# ── detection ────────────────────────────────────────────────────────────


def test_detect_backend_macos(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(
        sandbox.shutil,
        "which",
        lambda name: "/usr/bin/sandbox-exec",
    )
    assert sandbox.detect_backend() == sandbox.SEATBELT


def test_detect_backend_macos_missing_tool(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "darwin")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.detect_backend() is None


def test_detect_backend_linux(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/bwrap")
    assert sandbox.detect_backend() == sandbox.BWRAP


def test_detect_backend_linux_missing_tool(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "linux")
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: None)
    assert sandbox.detect_backend() is None


def test_detect_backend_windows(monkeypatch):
    monkeypatch.setattr(sandbox.sys, "platform", "win32")
    assert sandbox.detect_backend() is None


def test_available_follows_detection(monkeypatch):
    monkeypatch.setattr(sandbox, "detect_backend", lambda: sandbox.SEATBELT)
    assert sandbox.available() is True
    monkeypatch.setattr(sandbox, "detect_backend", lambda: None)
    assert sandbox.available() is False


# ── seatbelt profile ─────────────────────────────────────────────────────


def test_seatbelt_profile_confines_writes_to_cwd():
    profile = sandbox.seatbelt_profile("/home/user/proj")
    assert "(version 1)" in profile
    assert "(allow default)" in profile
    assert "(deny file-write*)" in profile
    assert '(allow file-write* (subpath "/home/user/proj"))' in profile
    # temp areas stay writable
    assert "/private/tmp" in profile


def test_seatbelt_profile_escapes_quotes():
    profile = sandbox.seatbelt_profile('/tmp/we"ird')
    assert '\\"' in profile
    assert 'subpath "/tmp/we\\"ird"' in profile


# ── bwrap argv ───────────────────────────────────────────────────────────


def test_bwrap_argv_structure():
    argv = sandbox.bwrap_argv("ls -la", "/work")
    assert argv[0] == "bwrap"
    assert "--ro-bind" in argv and "/" in argv
    # workspace is writable
    assert argv.count("/work") >= 2
    assert argv[-3:] == ["/bin/sh", "-c", "ls -la"]


# ── build_argv ───────────────────────────────────────────────────────────


def test_build_argv_seatbelt():
    argv = sandbox.build_argv("echo hi", "/w", backend=sandbox.SEATBELT)
    assert argv is not None
    assert argv[0] == "sandbox-exec"
    assert argv[-2:] == ["-c", "echo hi"]


def test_build_argv_bwrap():
    argv = sandbox.build_argv("echo hi", "/w", backend=sandbox.BWRAP)
    assert argv is not None
    assert argv[0] == "bwrap"


def test_build_argv_unknown_backend_returns_none():
    assert sandbox.build_argv("echo hi", "/w", backend="nope") is None


def test_build_argv_uses_detection_when_no_backend(monkeypatch):
    monkeypatch.setattr(sandbox, "detect_backend", lambda: None)
    assert sandbox.build_argv("echo hi", "/w") is None


# ── enablement ───────────────────────────────────────────────────────────


def test_set_enabled_override_true():
    sandbox.set_enabled(True)
    assert sandbox.is_enabled() is True


def test_set_enabled_override_false():
    sandbox.set_enabled(False)
    assert sandbox.is_enabled() is False


def test_is_enabled_reads_config(monkeypatch):
    class _Cfg:
        sandbox = True

    monkeypatch.setattr(
        "dashscope_agenticx.config.Config.load",
        staticmethod(lambda: _Cfg()),
    )
    assert sandbox.is_enabled() is True


def test_is_enabled_degrades_on_error(monkeypatch):
    def _boom():
        raise RuntimeError("no config")

    monkeypatch.setattr(
        "dashscope_agenticx.config.Config.load",
        staticmethod(_boom),
    )
    assert sandbox.is_enabled() is False


# ── informational helper ─────────────────────────────────────────────────


def test_is_sandboxed_path():
    assert sandbox.is_sandboxed_path("/work/a.txt", "/work") is True
    assert sandbox.is_sandboxed_path("/tmp/x", "/work") is True
    assert sandbox.is_sandboxed_path("/etc/passwd", "/work") is False
