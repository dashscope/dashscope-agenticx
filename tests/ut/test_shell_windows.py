# -*- coding: utf-8 -*-
"""Windows execution path of run_command.

Everything here is Windows-only, so it is exercised by forcing IS_WINDOWS
rather than by running on Windows. The decoding tests that came first also
install textual's stdout capture, because those defects only surfaced inside
the TUI: textual replaces sys.stdout with a _PrintCapture that has no
.encoding, which made every command fail while decoding its output.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from textual.app import _PrintCapture

from dashscope_agenticx.tools import shell


def _fake_proc(stdout: bytes = b"ok", stderr: bytes = b"") -> MagicMock:
    proc = MagicMock()
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.returncode = 0
    return proc


def _capture() -> _PrintCapture:
    return _PrintCapture(MagicMock())


def test_textual_stdout_capture_has_no_encoding():
    """Guard the premise the decoding fix rests on."""
    assert not hasattr(_capture(), "encoding")


async def test_run_command_decodes_under_textual_stdout_capture(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    monkeypatch.setattr(shell, "OUTPUT_ENCODING", "cp936")
    monkeypatch.setattr("sys.stdout", _capture())
    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_proc(b"C:\\app\r\n")),
    ):
        out = await shell.run_command("cd")
    assert out.startswith("C:\\app")
    assert "[exit code: 0]" in out


async def test_stderr_decodes_under_textual_stdout_capture(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    monkeypatch.setattr(shell, "OUTPUT_ENCODING", "cp936")
    monkeypatch.setattr("sys.stdout", _capture())
    with patch(
        "asyncio.create_subprocess_exec",
        new=AsyncMock(return_value=_fake_proc(b"", b"boom")),
    ):
        out = await shell.run_command("cd")
    assert "[stderr]" in out and "boom" in out


def test_output_encoding_survives_captured_stdout_and_stderr(monkeypatch):
    """Also covers a host with no ctypes.windll: the OEM lookup is guarded."""
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    monkeypatch.setattr("sys.stdout", _capture())
    monkeypatch.setattr("sys.__stdout__", _capture())
    assert shell._resolve_output_encodings()


async def test_command_reaches_shell_as_one_verbatim_argv(monkeypatch):
    """An embedded double quote must not be re-quoted through cmd.exe."""
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    cmd = 'python -c "import os; print(os.getcwd())"'
    with (
        patch(
            "asyncio.create_subprocess_exec",
            new=AsyncMock(return_value=_fake_proc()),
        ) as mock_exec,
        patch(
            "asyncio.create_subprocess_shell",
            new=AsyncMock(return_value=_fake_proc()),
        ) as mock_shell,
    ):
        await shell.run_command(cmd)
    assert mock_shell.await_count == 0
    argv = mock_exec.await_args.args
    assert argv[: len(shell.WIN_SHELL)] == shell.WIN_SHELL
    assert argv[len(shell.WIN_SHELL)] == cmd


def test_win_shell_prefers_pwsh_and_skips_profile(monkeypatch):
    monkeypatch.setattr(
        shell.shutil,
        "which",
        lambda name: "/usr/bin/pwsh" if name == "pwsh" else None,
    )
    argv = shell._resolve_win_shell()
    assert argv[0] == "/usr/bin/pwsh"
    assert "-NoProfile" in argv
    assert "-NonInteractive" in argv
    assert argv[-1] == "-Command"


def test_win_shell_falls_back_to_windows_powershell(monkeypatch):
    monkeypatch.setattr(
        shell.shutil,
        "which",
        lambda name: (
            "C:\\Windows\\powershell.exe" if name == "powershell" else None
        ),
    )
    assert shell._resolve_win_shell()[0].endswith("powershell.exe")


# ── the prompt must say which shell run_command really uses ────────────────


def test_shell_label_warns_off_posix_on_windows_powershell(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    monkeypatch.setattr(
        shell,
        "WIN_SHELL",
        ("C:\\Windows\\powershell.exe", "-Command"),
    )
    label = shell.shell_label()
    assert "PowerShell" in label
    assert "not POSIX" in label
    assert "2>/dev/null" in label
    assert "not statement separators" in label


def test_shell_label_allows_chaining_under_pwsh(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", True)
    monkeypatch.setattr(shell, "WIN_SHELL", ("pwsh", "-Command"))
    label = shell.shell_label()
    assert "PowerShell 7+" in label
    assert "not statement separators" not in label


def test_shell_label_names_posix_sh_off_windows(monkeypatch):
    monkeypatch.setattr(shell, "IS_WINDOWS", False)
    label = shell.shell_label()
    assert "/bin/sh -c" in label
    assert "PowerShell" not in label


# ── decoding a child that writes in its console's code page ────────────────


@pytest.fixture
def win_encodings(monkeypatch):
    """A zh-CN Windows host: UTF-8 primary, cp936 as the OEM fallback."""
    monkeypatch.setattr(shell, "OUTPUT_ENCODING", "utf-8")
    monkeypatch.setattr(shell, "OUTPUT_ENCODINGS", ("utf-8", "cp936"))


def test_decode_output_falls_back_to_the_oem_page(win_encodings):
    """The ParserError that reached the model as mojibake."""
    text = '标记"||"不是此版本中的有效语句分隔符。'
    assert shell._decode_output(text.encode("cp936")) == text


def test_decode_output_prefers_utf8(win_encodings):
    assert shell._decode_output("你好 ok".encode("utf-8")) == "你好 ok"


def test_decode_output_repairs_a_split_trailing_character(win_encodings):
    """Byte truncation must not make the buffer look like another encoding.

    run_command slices stdout/stderr on a byte budget, which can cut a
    multi-byte character in half. Falling through to cp936 there would garble
    the whole output, not just its last character.
    """
    data = ("x" * 10 + "你").encode("utf-8")[:11]
    assert shell._decode_output(data) == "x" * 10 + "\ufffd"


def test_decode_output_survives_an_unknown_encoding(monkeypatch):
    monkeypatch.setattr(shell, "OUTPUT_ENCODING", "no-such-codec")
    monkeypatch.setattr(shell, "OUTPUT_ENCODINGS", ("no-such-codec",))
    assert shell._decode_output(b"plain") == "plain"
