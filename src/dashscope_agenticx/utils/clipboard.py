# -*- coding: utf-8 -*-
"""System clipboard helpers.

Used by the TUI to write mouse selections directly to the system clipboard:
the app captures the mouse (alt-screen), so the terminal's own Cmd+C only
sees the visible screen — multi-screen selections would be truncated there.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from contextlib import contextmanager
from functools import lru_cache

# (tool, argv) tried in order; first one present wins.
_CLIPBOARD_TOOLS: tuple[tuple[str, list[str]], ...] = (
    ("pbcopy", ["pbcopy"]),
    ("xclip", ["xclip", "-selection", "clipboard"]),
    ("wl-copy", ["wl-copy"]),
    ("clip", ["clip"]),
)


class MacOSClipboard:
    """Read pasteboard revisions without text polling or keyboard hooks."""

    def __init__(self) -> None:
        import ctypes

        self._ctypes = ctypes
        self._appkit = ctypes.CDLL(
            "/System/Library/Frameworks/AppKit.framework/AppKit",
        )
        self._objc = ctypes.CDLL("/usr/lib/libobjc.A.dylib")
        self._class = self._objc.objc_getClass
        self._class.argtypes = [ctypes.c_char_p]
        self._class.restype = ctypes.c_void_p
        self._selector = self._objc.sel_registerName
        self._selector.argtypes = [ctypes.c_char_p]
        self._selector.restype = ctypes.c_void_p
        self._pointer = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", self._objc))
        self._integer = ctypes.CFUNCTYPE(
            ctypes.c_long,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", self._objc))
        self._void = ctypes.CFUNCTYPE(None, ctypes.c_void_p, ctypes.c_void_p)(
            ("objc_msgSend", self._objc),
        )
        self._argument = ctypes.CFUNCTYPE(
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_void_p,
        )(("objc_msgSend", self._objc))
        self._length = ctypes.CFUNCTYPE(
            ctypes.c_size_t,
            ctypes.c_void_p,
            ctypes.c_void_p,
            ctypes.c_size_t,
        )(("objc_msgSend", self._objc))

    @contextmanager
    def _pool(self):
        pool = self._pointer(
            self._pointer(
                self._class(b"NSAutoreleasePool"),
                self._selector(b"alloc"),
            ),
            self._selector(b"init"),
        )
        try:
            yield
        finally:
            self._void(pool, self._selector(b"drain"))

    def _pasteboard(self):
        return self._pointer(
            self._class(b"NSPasteboard"),
            self._selector(b"generalPasteboard"),
        )

    def _string_value(self, value) -> str | None:
        if not value:
            return None
        pointer = self._pointer(value, self._selector(b"UTF8String"))
        if not pointer:
            return None
        length = self._length(
            value,
            self._selector(b"lengthOfBytesUsingEncoding:"),
            4,
        )
        return self._ctypes.string_at(pointer, length).decode("utf-8")

    def change_count(self) -> int | None:
        try:
            with self._pool():
                return self._integer(
                    self._pasteboard(),
                    self._selector(b"changeCount"),
                )
        except Exception:
            return None

    def read_text(self) -> str | None:
        try:
            with self._pool():
                text_type = self._argument(
                    self._class(b"NSString"),
                    self._selector(b"stringWithUTF8String:"),
                    self._ctypes.cast(
                        self._ctypes.c_char_p(b"public.utf8-plain-text"),
                        self._ctypes.c_void_p,
                    ),
                )
                value = self._argument(
                    self._pasteboard(),
                    self._selector(b"stringForType:"),
                    text_type,
                )
                return self._string_value(value)
        except Exception:
            return None


@lru_cache(maxsize=1)
def get_macos_clipboard() -> MacOSClipboard | None:
    if sys.platform != "darwin":
        return None
    try:
        return MacOSClipboard()
    except Exception:
        return None


def matches_native_selection(
    text: str | None,
    candidates: tuple[str, ...],
) -> bool:
    """Match only known selected/visible lines, tolerating terminal padding."""
    if not text or not text.strip():
        return False

    def normalize(value: str) -> str:
        return "\n".join(line.strip() for line in value.splitlines()).strip()

    normalized = normalize(text)
    for candidate in candidates:
        visible = normalize(candidate)
        if normalized == visible:
            return True
        if (
            len(normalized.splitlines()) >= 2
            and f"\n{normalized}\n" in f"\n{visible}\n"
        ):
            return True
    return False


def copy_to_clipboard(text: str) -> str | None:
    """Write ``text`` to the system clipboard.

    Returns the tool name used, or ``None`` when no clipboard tool is
    available or the write failed. Never raises.
    """
    for name, cmd in _CLIPBOARD_TOOLS:
        if not shutil.which(name):
            continue
        try:
            result = subprocess.run(
                cmd,
                input=text.encode("utf-8"),
                check=False,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            continue
        if result.returncode != 0:
            continue
        return name
    return None
