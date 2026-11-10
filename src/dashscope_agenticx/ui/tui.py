# -*- coding: utf-8 -*-
"""
Textual-based TUI for AgenticX
Fixed input at bottom + scrolling output above
"""
# pylint: disable=wrong-import-position,protected-access,unused-import
# pylint: disable=too-many-return-statements,too-many-branches
# pylint: disable=too-many-statements,unused-argument

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import re
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

# The kitty keyboard protocol breaks input in several terminals: iTerm2 then
# sends e.g. "\x1b[32u" for space, which TextArea silently drops (no spaces),
# and IME/CJK composition stops working. Disable it by default; users who
# want it back can set TEXTUAL_DISABLE_KITTY_KEY=0 before launching.
os.environ.setdefault("TEXTUAL_DISABLE_KITTY_KEY", "1")

# JediTerm (the PyCharm terminal) never answers Textual's DECRQM probe,
# and versions before 2025.3.2 lack synchronized-output (mode 2026)
# buffering: every frame is written raw, segment by segment, so a
# full-screen repaint while the output sits at the bottom shows up as
# flicker. The JediTerm version cannot be probed, so always batch more
# aggressively for JediTerm (new versions have sync as a fallback, so
# extra batching is harmless).
_IS_JEDITERM = any(
    marker in os.environ.get(variable, "").lower()
    for variable in (
        "TERMINAL_EMULATOR",
        "TERM_PROGRAM",
        "LC_TERMINAL",
        "__CFBundleIdentifier",
    )
    for marker in ("jetbrains", "jediterm")
)

# Stream flush: time window + line-count threshold (the threshold only
# guards against over-frequent flushes on bursty bulk output)
_STREAM_FLUSH_INTERVAL = 0.8 if _IS_JEDITERM else 0.3
_STREAM_FLUSH_LINES = 400 if _IS_JEDITERM else 20

# Wheel batching window: full repaints are costly on JediTerm; trade
# frame rate for stability
_WHEEL_FLUSH_INTERVAL = 0.03
# Trackpad gestures decelerate: the tail of a wheel gesture arrives as
# isolated arrows 20-200ms after the last burst key, indistinguishable
# from real keypresses by timing alone. Within this window after the
# last wheel-classified key, deferred arrows are absorbed as scrolls
# instead of history/completion (which visibly raced the input box and
# popup while scrolling).
_WHEEL_TAIL_WINDOW = 0.35

from rich.cells import cell_len  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.markup import render  # noqa: E402
from rich.panel import Panel  # noqa: E402
from rich.segment import Segment  # noqa: E402
from rich.text import Text  # noqa: E402
from textual import events  # noqa: E402
from textual.app import App, ComposeResult  # noqa: E402
from textual.binding import Binding  # noqa: E402
from textual.containers import Container  # noqa: E402
from textual.geometry import Offset, Size  # noqa: E402
from textual.message import Message  # noqa: E402
from textual.screen import Screen  # noqa: E402
from textual.selection import SelectEnd, Selection  # noqa: E402
from textual.strip import Strip  # noqa: E402
from textual.timer import Timer  # noqa: E402
from textual.widgets import OptionList, RichLog, Static, TextArea  # noqa: E402
from textual.widgets.option_list import Option  # noqa: E402

from dashscope_agenticx.commands import (  # noqa: E402
    handle_shell_escape,
    render_help_text,
)
from dashscope_agenticx.events import (  # noqa: E402
    AgentEvent,
    AgentEventType,
)
from dashscope_agenticx.ui.selection_actions import (  # noqa: E402
    SelectionActions,
)  # noqa: E402
from dashscope_agenticx.ui.tool_output import ToolOutput  # noqa: E402
from dashscope_agenticx.ui.transcript import (  # noqa: E402
    MarkdownOutput,
    OutputBlock,
    TurnHeader,
    TurnStatistics,
)
from dashscope_agenticx.utils import (  # noqa: E402
    UserAbortedTurn,
    UserSupplement,
    mask_secret,
)


@dataclass
class _NativeCopyRequest:
    clipboard: Any
    revision: int
    text: str
    candidates: tuple[str, ...]
    selections: dict[Any, Selection] | None = None
    expires: float | None = None
    verify: bool = False
    screen_selection: Selection | None = None


def _relative_luminance(color: str) -> float | None:
    """WCAG relative luminance (0.0 = black, 1.0 = white) of a color string.

    Returns ``None`` when the color cannot be parsed.
    """
    from textual.color import Color

    try:
        parsed = Color.parse(color)
    except Exception:
        return None
    r, g, b = (channel / 255 for channel in (parsed.r, parsed.g, parsed.b))

    def _linearize(c: float) -> float:
        return c / 12.92 if c <= 0.04045 else ((c + 0.055) / 1.055) ** 2.4

    return (
        0.2126 * _linearize(r)
        + 0.7152 * _linearize(g)
        + 0.0722 * _linearize(b)
    )


def _app_theme_for_background(background: str | None) -> str:
    """Pick the Textual base theme for a background color.

    A light background needs ``textual-light`` so that base design tokens
    (``$foreground``, ``$text``, ...) and the ANSI→truecolor filter use a
    light-tuned palette instead of the dark theme's near-white foreground.
    """
    luminance = _relative_luminance(background) if background else None
    if luminance is not None and luminance > 0.5:
        return "textual-light"
    return "textual-dark"


def _tool_trail_style(theme: dict | None, *, light: bool) -> str:
    """Style for ``[tool] → result`` trail lines.

    ``dim cyan`` is unreadable on a light background (~2.5:1 on white), so
    in light mode use the theme's muted color (or a mid-gray fallback).
    """
    if light:
        return (theme or {}).get("muted") or "#595959"
    return "dim cyan"


def _diff_syntax_theme(*, light: bool) -> str:
    """Rich Syntax theme for diff highlighting, matched to the app theme."""
    return "ansi_light" if light else "ansi_dark"


class RichLogWriter(io.StringIO):
    """File-like object that redirects Console output to RichLog.
    Batches writes per flush() cycle to avoid line fragmentation."""

    def __init__(self, rich_log: RichLog, app: App):
        super().__init__()
        self.rich_log = rich_log
        self.app = app
        self._app_thread_id = threading.get_ident()
        self._buffer: list[str] = []

    def write(self, s: str) -> int:
        if s:
            self._buffer.append(s)
        return len(s) if s else 0

    def flush(self):
        if self._buffer:
            text = "".join(self._buffer)
            self._buffer = []
            if threading.get_ident() == self._app_thread_id:
                self.rich_log.write(Text.from_ansi(text))
            else:
                self.app.call_from_thread(
                    self.rich_log.write,
                    Text.from_ansi(text),
                )
        super().flush()


# Persistent hint below the input box: shown when no dynamic arg hint is
# available; arg hints override it while typing a / command
_INPUT_IDLE_HINT = (
    "Ctrl+C clear · Ctrl+T voice · Ctrl+J newline · "
    "Ctrl+O tools · Ctrl+G stats · ↑ history · / commands · @ files"
)


class OutputLog(RichLog):
    """RichLog with sticky auto-scroll and content-anchored text selection.

    - write(): only auto-scrolls when the view is already at the bottom, so
      streaming output never yanks the view away from a user who scrolled
      up (this also keeps wheel scrolling and selections stable).
    - render_line()/get_selection(): anchor mouse selections to content
      (like the builtin Log widget) so a selection survives scrolling and
      can extend across scrolled content; without this a drag degrades to a
      widget-level select-all that copies nothing.
    """

    # Explicit follow state: writes keep the view pinned to the bottom only
    # while following. An upward scroll (mouse wheel, PyCharm's arrow-key
    # wheel queue, page keys) stops following; a downward scroll re-engages
    # it once it lands back at the bottom; scroll_end() (command submit,
    # confirmation prompts) always re-engages. A position band or a
    # time-based suppression both misbehave at the bottom while streaming:
    # the band yanks the view on fine 1-line adjustments and a timeout
    # re-arms follow while the user is still reading.
    _follow_output: bool = True

    # A read-only view must never take keyboard focus: on PyCharm the
    # trackpad wheel arrives as arrow keys, and a focused OutputLog would
    # route them to ScrollView's *animated* key-scroll bindings — bypassing
    # CommandInput's burst classifier and tearing on JediTerm. Keyboard
    # scrolling is handled globally by CommandInput (pageup/shift+arrows);
    # mouse wheel and drag selection need no focus.
    can_focus = False
    lines: list[Strip]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._tool_blocks: dict[int, ToolOutput] = {}
        self._output_blocks: dict[int, OutputBlock] = {}
        self._statistics_blocks: dict[int, TurnStatistics] = {}
        self._pending_updates: dict[int, tuple[OutputBlock, bool | None]] = {}
        self._next_block_key = 0

    def write(
        self,
        content,
        width: int | None = None,
        expand: bool = False,
        shrink: bool = True,
        scroll_end: bool | None = None,
        animate: bool = False,
    ):
        if scroll_end is None and self.auto_scroll:
            scroll_end = self._follow_output
        if not self._size_known:
            return super().write(
                content,
                width=width,
                expand=expand,
                shrink=shrink,
                scroll_end=scroll_end,
                animate=animate,
            )
        start_line = self._start_line + len(self.lines)
        if isinstance(content, OutputBlock):
            self._next_block_key += 1
            content.key = self._next_block_key
        result = super().write(
            content,
            width=width,
            expand=expand,
            shrink=shrink,
            scroll_end=False,
            animate=animate,
        )
        if isinstance(content, OutputBlock):
            content.start_line = start_line
            content.line_count = (
                self._start_line + len(self.lines) - start_line
            )
            self._output_blocks[content.key] = content
            if isinstance(content, ToolOutput):
                self._tool_blocks[content.key] = content
            elif isinstance(content, TurnStatistics):
                self._statistics_blocks[content.key] = content
        self._prune_output_blocks()
        if scroll_end:
            # Follow with a fire-time check rather than RichLog's queued
            # scroll_end: a wheel-up landing between the write and the
            # refresh must win over the write's queued scroll.
            self.call_after_refresh(self._scroll_to_follow)
        return result

    def _prune_output_blocks(self) -> None:
        for key, block in list(self._output_blocks.items()):
            end = block.start_line + block.line_count
            retained = (
                end > self._start_line
                if isinstance(block, MarkdownOutput)
                else block.start_line >= self._start_line
            )
            if not retained:
                self._output_blocks.pop(key, None)
                self._tool_blocks.pop(key, None)
                self._statistics_blocks.pop(key, None)
                self._pending_updates.pop(key, None)

    def clear(self):
        self._tool_blocks.clear()
        self._output_blocks.clear()
        self._statistics_blocks.clear()
        self._pending_updates.clear()
        return super().clear()

    def update_block(
        self,
        block: OutputBlock,
        *,
        scroll_end: bool | None = None,
    ) -> None:
        selection = self.text_selection
        if selection is not None and selection.start != selection.end:
            self._pending_updates[block.key] = (block, scroll_end)
            return
        self._replace_block(block, scroll_end=scroll_end)

    def _replace_block(
        self,
        block: OutputBlock,
        *,
        scroll_end: bool | None = None,
    ) -> None:
        if block.key not in self._output_blocks:
            return
        trimmed = max(0, self._start_line - block.start_line)
        start = max(0, block.start_line - self._start_line)
        width = max(1, self.size.width - 4)
        options = self.app.console.options.update_width(width)
        strips = Strip.from_lines(
            list(Segment.split_lines(self.app.console.render(block, options))),
        )
        strips = strips[trimmed:]
        old_count = max(0, block.line_count - trimmed)
        difference = len(strips) - old_count
        scroll_y = self.scroll_offset.y
        if scroll_y >= start + old_count:
            scroll_y += difference
        elif scroll_y > start:
            scroll_y = start
        self.lines[start : start + old_count] = strips
        block.line_count = trimmed + len(strips)
        for other in self._output_blocks.values():
            if other.start_line > block.start_line:
                other.start_line += difference
        if self.max_lines is not None and len(self.lines) > self.max_lines:
            removed = len(self.lines) - self.max_lines
            self.lines = self.lines[removed:]
            self._start_line += removed
            scroll_y -= removed
        self._prune_output_blocks()
        self._line_cache.clear()
        self._widest_line_width = max(
            (strip.cell_length for strip in self.lines),
            default=0,
        )
        self.virtual_size = Size(self._widest_line_width, len(self.lines))
        self.refresh()
        max_scroll_y = max(
            0,
            len(self.lines) - self.scrollable_content_region.height,
        )
        scroll_y = max(0, min(scroll_y, max_scroll_y))
        if self._follow_output and scroll_end is not False:
            scroll_y = max_scroll_y
        self.scroll_to(y=scroll_y, animate=False, immediate=True)

    def toggle_tool(self, key: int) -> None:
        block = self._tool_blocks.get(key)
        if block is not None:
            self.screen.clear_selection()
            block.expanded = not block.expanded
            self.update_block(block)

    def toggle_tool_details(self) -> None:
        expanded = any(
            not block.expanded for block in self._tool_blocks.values()
        )
        self.screen.clear_selection()
        for key in reversed(list(self._tool_blocks)):
            block = self._tool_blocks.get(key)
            if block is not None and block.expanded != expanded:
                block.expanded = expanded
                self.update_block(block)

    def toggle_statistics(self, key: int | None = None) -> None:
        keys = [key] if key is not None else list(self._statistics_blocks)
        expanded = any(
            not self._statistics_blocks[item].expanded
            for item in keys
            if item in self._statistics_blocks
        )
        self.screen.clear_selection()
        for item in reversed(keys):
            block = self._statistics_blocks.get(item)
            if block is not None and block.expanded != expanded:
                block.expanded = expanded
                self.update_block(block)

    def update_tool_result(self, block: ToolOutput, result: str) -> None:
        block.result = result
        block.result_truncated = False
        self.update_block(block)

    def _scroll_to_follow(self) -> None:
        if self._follow_output:
            self.scroll_to(y=self.max_scroll_y, animate=False)

    def scroll_end(self, *args, **kwargs):
        self._follow_output = True
        return super().scroll_end(*args, **kwargs)

    def selection_updated(self, selection: Selection | None) -> None:
        """Invalidate Textual's line cache when selection styling changes.

        Textual 8 caches RichLog strips independently of the Screen selection
        reactive. ``Widget.refresh()`` alone can leave the old unselected line
        in ``StylesCache``, so the selection exists and copies correctly but
        has no visible highlight.
        """
        self._styles_cache.clear()
        super().selection_updated(selection)
        if selection is None or selection.start == selection.end:
            self.call_later(self._flush_pending_updates)

    def _flush_pending_updates(self) -> None:
        selection = self.text_selection
        if selection is not None and selection.start != selection.end:
            return
        pending = list(self._pending_updates.values())
        self._pending_updates.clear()
        for block, scroll_end in pending:
            self._replace_block(block, scroll_end=scroll_end)

    def render_line(self, y: int) -> Strip:
        scroll_x, scroll_y = self.scroll_offset
        line_y = scroll_y + y
        strip = self._render_line(
            line_y,
            scroll_x,
            self.scrollable_content_region.width,
        ).apply_style(self.rich_style)
        selection = self.text_selection if self.is_attached else None
        if selection is not None and line_y < len(self.lines):
            span = selection.get_span(line_y)
            if span is not None:
                start, end = span
                line_text = self.lines[line_y].text
                # Span offsets are in characters; the strip is cropped in
                # cells, so convert (scroll_x is 0 in practice: the log wraps).
                from_x = max(cell_len(line_text[:start]) - scroll_x, 0)
                to_x = (
                    strip.cell_length
                    if end == -1
                    else max(cell_len(line_text[:end]) - scroll_x, from_x)
                )
                to_x = min(to_x, strip.cell_length)
                if to_x > from_x:
                    select_style = self.screen.get_component_rich_style(
                        "screen--selection",
                    )
                    # Strip.apply_style is a pre-style and gets overridden
                    # by each segment's own background (the surface
                    # background of rich_style); only post-style makes the
                    # selection background truly visible
                    middle = strip.crop(from_x, to_x)
                    middle = Strip(
                        Segment.apply_style(middle, post_style=select_style),
                        middle.cell_length,
                    )
                    strip = Strip.join(
                        [
                            strip.crop(0, from_x),
                            middle,
                            strip.crop(to_x, None),
                        ],
                    )
        # Anchor segments to content coordinates so the screen can map mouse
        # drags back to (x, y) content offsets.
        return strip.apply_offsets(scroll_x, line_y)

    def get_selection(self, selection: Selection) -> tuple[str, str] | None:
        """Return the selected text from the log's content lines.

        Slices ``self.lines`` directly by content offsets instead of
        ``Selection.extract``: extract splits the joined text with
        ``splitlines()``, which also breaks on ``\\r``/``\\u2028`` etc. and
        desyncs indexes against ``self.lines``. Indexes are clamped: the
        buffer can be pruned (max_lines) while a selection exists.
        """
        if not self.lines:
            return None
        start = selection.start or Offset(0, 0)
        end = selection.end or Offset(
            len(self.lines[-1].text),
            len(self.lines) - 1,
        )
        sl, sx = start.y, start.x
        el, ex = end.y, end.x
        try:
            el = min(el, len(self.lines) - 1)
            if sl > el or sl >= len(self.lines):
                return None
            if sl == el:
                return self.lines[sl].text.rstrip()[sx:ex], "\n"
            parts = [self.lines[sl].text.rstrip()[sx:]]
            parts.extend(
                self.lines[i].text.rstrip() for i in range(sl + 1, el)
            )
            parts.append(self.lines[el].text.rstrip()[:ex])
            return "\n".join(parts), "\n"
        except (IndexError, TypeError):
            return None

    def native_copy_candidates(self) -> tuple[str, ...]:
        """Snapshot visible output and its selected part for native copying."""
        selection = self.text_selection
        if selection is None:
            return ()
        first = self.scroll_offset.y
        last = min(
            first + self.scrollable_content_region.height,
            len(self.lines),
        )
        selected = []
        for index in range(first, last):
            span = selection.get_span(index)
            if span is None:
                continue
            start, end = span
            line = self.lines[index].text.rstrip()
            selected.append(line[start:] if end == -1 else line[start:end])
        visible = "\n".join(
            self.lines[index].text for index in range(first, last)
        )
        return ("\n".join(selected), visible)

    async def _on_click(self, event: events.Click) -> None:
        if event.chain == 1 and event.button == 1 and event.style is not None:
            key = event.style.meta.get("tool_output")
            statistics_key = event.style.meta.get("statistics_output")
            selection = self.text_selection
            if (key or statistics_key) and (
                selection is None or selection.start == selection.end
            ):
                event.prevent_default()
                event.stop()
                if key:
                    self.toggle_tool(key)
                else:
                    self.toggle_statistics(statistics_key)
                return
        if event.chain >= 2 and event.button == 1:
            # Textual's default double-click is a widget-wide select-all —
            # on a long output log that selects the entire scrollback. Do
            # word (double) / line (triple) selection at the pointer
            # instead. prevent_default() is required: the pump dispatches
            # the handler of every MRO class that defines one, so
            # Widget._on_click (the select-all) runs anyway unless the
            # default is suppressed.
            event.prevent_default()
            event.stop()
            self._select_at_pointer(event)
            return
        # chain == 1: the pump dispatches Widget._on_click on its own —
        # do not call super() here or it would run twice.

    def _select_at_pointer(self, event: events.Click) -> None:
        try:
            widget, offset = self.screen.get_widget_and_offset_at(
                event.screen_x,
                event.screen_y,
            )
        except Exception:
            return
        if widget is not self or offset is None or not self.lines:
            return
        # render_line anchors segments in content coordinates, so the
        # hit-test offset is (character, content-line).
        line_idx = min(max(offset.y, 0), len(self.lines) - 1)
        text = self.lines[line_idx].text
        if event.chain >= 3:
            start_x, end_x = 0, len(text)
        else:
            span = self._word_span(text, offset.x)
            if span is None:
                return
            start_x, end_x = span
        self.screen.selections = {
            self: Selection(
                Offset(start_x, line_idx),
                Offset(end_x, line_idx),
            ),
        }

    @staticmethod
    def _word_span(text: str, x: int) -> tuple[int, int] | None:
        """Character span of the word under ``x`` in ``text``, or None."""
        if not text:
            return None
        x = min(max(x, 0), len(text) - 1)
        char = text[x]
        if ord(char) > 0x2E7F:
            # CJK has no whitespace word boundaries: select the character
            return x, x + 1
        if not char.isalnum() and char != "_":
            return None
        for match in re.finditer(r"\w+", text):
            if match.start() <= x < match.end():
                return match.start(), match.end()
        return None

    def _on_mouse_scroll_down(self, event: events.MouseScrollDown) -> None:
        # The pump dispatches this event to every MRO class defining the
        # handler; stop() doesn't suppress that, only prevent_default()
        # does — without it each wheel notch scrolls twice.
        event.prevent_default()
        super()._on_mouse_scroll_down(event)
        # Pointer scrolls apply synchronously (animate=False), so the
        # landed position is readable here: re-engage follow only when the
        # scroll actually reached the bottom.
        if self.scroll_offset.y >= self.max_scroll_y:
            self._follow_output = True
        self._extend_selection_after_wheel(event)

    def _on_mouse_scroll_up(self, event: events.MouseScrollUp) -> None:
        event.prevent_default()
        super()._on_mouse_scroll_up(event)
        if self.scroll_offset.y < self.max_scroll_y:
            self._follow_output = False
        self._extend_selection_after_wheel(event)

    def _extend_selection_after_wheel(self, event: events.MouseEvent) -> None:
        """Grow an in-progress selection when wheel-scrolling mid-drag.

        Textual's wheel handlers scroll the widget but never update the
        screen's select state, so holding the button and scrolling (the
        typical trackpad gesture) leaves the selection frozen at <= one
        screen and the highlight appears to drift with the content. After
        scrolling, re-derive the content offset under the pointer and move
        the selection end there, mirroring what a MouseMove does.
        """
        screen = self.screen
        state = screen._select_state
        if not screen._selecting or state is None:
            return
        select_widget, select_offset = screen.get_widget_and_offset_at(
            event.screen_x,
            event.screen_y,
        )
        if (
            select_widget is not self
            or select_offset is None
            or self.parent is None
        ):
            return
        screen._select_state = state.update_end(
            event.screen_offset,
            SelectEnd(self.parent, self, select_offset),
        )
        if (
            state.start.content_widget is self
            and state.start.content_offset != select_offset
        ):
            screen._mouse_down_offset = None


class AcliScreen(Screen):
    """Default screen that lets a drag selection grow during edge auto-scroll.

    Textual's auto-scroll timer scrolls the container and repaints the
    highlight, but never re-derives the content offset under the (stationary)
    pointer — a drag parked at the top/bottom edge scrolls content while the
    selection stays frozen at <= one screen. Stash the pointer position from
    _check_auto_scroll and, on each tick (_update_select), move the selection
    end to the content now under the pointer, mirroring
    OutputLog._extend_selection_after_wheel.
    """

    _auto_scroll_pointer: Offset | None = None
    _auto_scroll_target: Any = None  # widget our auto-scroll timer scrolls
    _select_state: Any  # textual Screen internal
    # True once any mouse event arrived: guards the pointer-position
    # heuristics against terminals that never report the pointer (the
    # stale 0,0 position would otherwise sit inside the output area).
    _mouse_seen: bool = False

    def _start_auto_scroll(
        self,
        widget,
        direction,
        speed: float = 1.0,
    ) -> None:
        # super() calls _stop_auto_scroll() first (clearing the target), so
        # record the target afterwards.
        super()._start_auto_scroll(widget, direction, speed)
        self._auto_scroll_target = widget

    def _stop_auto_scroll(self) -> None:
        self._auto_scroll_target = None
        super()._stop_auto_scroll()

    def _forward_event(self, event) -> None:
        if isinstance(event, events.MouseEvent):
            self._mouse_seen = True
        if isinstance(event, events.MouseDown):
            self.app._stop_clipboard_watch()
        if isinstance(event, events.MouseUp):
            if self._selecting:
                try:
                    self.query_one(
                        "#command-input",
                        CommandInput,
                    )._flush_wheel_scroll()
                except Exception:
                    pass
                self.extend_selection_to(event.screen_offset)
            self._stop_auto_scroll()
        if (
            isinstance(event, events.MouseDown)
            and self.app.mouse_captured is not None
        ):
            # A MouseUp that never reaches the captor (released outside the
            # window, intercepted by a modal, ...) leaks the capture: every
            # later MouseDown skips selection setup and routes to the stale
            # captor, so a drag can never start a new selection and copy
            # keeps serving the old one. A fresh MouseDown always begins a
            # new gesture — drop the stale capture.
            self.app.capture_mouse(None)
        super()._forward_event(event)
        if isinstance(event, events.MouseDown):
            # Anchor the pointer stash to the new drag: a wheel flush firing
            # before this drag's first MouseMove must not extend the
            # selection toward the previous drag's parked position.
            self._auto_scroll_pointer = Offset(
                int(event.pointer_screen_x),
                int(event.pointer_screen_y),
            )
            return
        if isinstance(event, events.MouseUp):
            self._auto_scroll_pointer = None
            return
        if not (isinstance(event, events.MouseMove) and self._selecting):
            return
        self._auto_scroll_pointer = Offset(
            int(event.pointer_screen_x),
            int(event.pointer_screen_y),
        )
        if event.delta_y == 0:
            return
        # Edge auto-scroll must scroll the widget being selected, not the
        # widget under the pointer. Below the output area (spinner, input
        # box, hint rows) Textual's ancestor walk either finds no scrollable
        # widget and stops the timer, or arms it for that widget — the
        # CommandInput is a TextArea, so it is *always* scrollable from
        # Textual's viewpoint even when the draft fits on one line, and the
        # timer then ticks against an input whose scroll offset is clamped
        # at 0 while the output never moves (this is exactly what a drag
        # into the prompt area hits in iTerm2). Whenever the drag started
        # in #output and the pointer sits in an edge zone, make sure the
        # armed auto-scroll targets #output.
        state = self._select_state
        if state is None:
            return
        try:
            output = self.query_one("#output")
        except Exception:
            return
        start_widget = state.start.content_widget or state.start.container
        if start_widget is not output:
            # Selecting inside another widget (e.g. the input draft) —
            # leave Textual's native auto-scroll alone.
            return
        lines = self.app.SELECT_AUTO_SCROLL_LINES
        y = event.pointer_screen_y
        if y < output.region.y + lines:
            direction = -1
            speed = min((output.region.y + lines - y) / lines, 1.0)
            can_scroll = output.scroll_y > 0
        elif y >= output.region.bottom:
            direction = +1
            speed = min((y - output.region.bottom + 1) / lines, 1.0)
            can_scroll = output.scroll_y < output.max_scroll_y
        else:
            return
        if self._auto_scroll_target is output:
            # Already scrolling the right widget (armed here or natively).
            return
        self._stop_auto_scroll()
        if can_scroll:
            self._start_auto_scroll(output, direction, speed)

    def extend_selection_to(self, pointer: Offset) -> None:
        """Move an in-progress selection's end to the content under pointer."""
        state = self._select_state
        if not self._selecting or state is None:
            return
        select_widget, select_offset = self.get_widget_and_offset_at(
            pointer.x,
            pointer.y,
        )
        if select_widget is None:
            # Pointer is in a no-hit zone (e.g. output-area padding rows
            # whose hit-test returns None): clamp the probe point into the
            # output content region so the selection end keeps following
            # the content under the edge
            try:
                region = self.query_one("#output").content_region
            except Exception:
                return
            select_widget, select_offset = self.get_widget_and_offset_at(
                min(max(pointer.x, region.x), region.right - 1),
                min(max(pointer.y, region.y), region.bottom - 1),
            )
            if select_widget is None:
                return
        if select_offset is not None:
            container = select_widget.parent
            if container is None:
                return
            end = SelectEnd(container, select_widget, select_offset)
        else:
            end = SelectEnd(select_widget, None, None)
        self._select_state = state.update_end(pointer, end)
        if (
            state.start.content_widget is select_widget
            and state.start.content_offset != select_offset
        ):
            self._mouse_down_offset = None

    def _update_select(self) -> None:
        if self._auto_scroll_pointer is not None:
            self.extend_selection_to(self._auto_scroll_pointer)
        super()._update_select()


@contextlib.contextmanager
def _capture_console(tui: "AgenticXApp", *, interactive: bool = False):
    """Temporarily redirect module consoles to the TUI output.

    Yields a Console wired either to a RichLogWriter (interactive commands that
    need real-time output) or to a buffered StringIO (commands whose output is
    wrapped in a Panel afterwards).
    """
    import types

    import dashscope_agenticx.agent as agent_module
    import dashscope_agenticx.agents.subagents as subagents_module
    import dashscope_agenticx.cli as cli_module
    import dashscope_agenticx.dev as dev_module
    import dashscope_agenticx.scheduler as scheduler_module

    output = tui.query_one("#output", RichLog)

    if interactive:
        writer = RichLogWriter(output, tui)
        tui_console = Console(file=writer, force_terminal=True)
    else:
        buf = io.StringIO()
        content_width = (output.size.width or 80) - 4
        tui_console = Console(file=buf, record=True, width=content_width)

    # Collect all modules that have a `console` attribute.
    # Start with the core modules always imported above, then scan
    # dashscope_agenticx.cli's
    # submodules so handler modules (handlers_config, handlers_profile, mcp,
    # dispatch, etc.) are captured too.
    captured: list[tuple[types.ModuleType, Console]] = []
    seen_ids: set[int] = set()

    def _capture(mod):
        if mod is None:
            return
        cid = id(mod)
        if cid in seen_ids:
            return
        seen_ids.add(cid)
        cons = getattr(mod, "console", None)
        if isinstance(cons, Console):
            captured.append((mod, cons))
            mod.console = tui_console

    for mod in (
        cli_module,
        dev_module,
        subagents_module,
        scheduler_module,
        agent_module,
    ):
        _capture(mod)

    # Scan dashscope_agenticx.cli submodules already loaded in sys.modules
    for name, mod in list(sys.modules.items()):
        if name.startswith("dashscope_agenticx.cli.") and isinstance(
            mod,
            types.ModuleType,
        ):
            _capture(mod)

    try:
        yield tui_console
    finally:
        for mod, old_cons in captured:
            mod.console = old_cons

        if not interactive:
            buf_text = tui_console.export_text(styles=True).rstrip()
            if buf_text:
                panel_border = (
                    getattr(tui, "_panel_border", "bright_blue")
                    or "bright_blue"
                )
                tui._write_output(
                    Panel(Text.from_ansi(buf_text), border_style=panel_border),
                )


def estimate_tokens(text: str) -> int:
    """Rough live token estimate: CJK chars ≈ 1 token each, else ≈ 4
    chars/token."""
    cjk = sum(1 for c in text if ord(c) > 0x2E7F)
    return cjk + (len(text) - cjk + 3) // 4


def fmt_chars(n: int) -> str:
    """Compact char count: 15230 -> '15.2k', 320 -> '320'."""
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


class Spinner(Static):
    """Animated spinner widget with live turn stats (elapsed / tokens / %)."""

    FRAMES = ("⠋", "⠙", "⠹", "⠸", "⠼", "⠴", "⠦", "⠧", "⠇", "⠏")

    def __init__(self, text: str = "Thinking...", **kwargs):
        super().__init__(**kwargs)
        self.text = text
        self.frame_idx = 0
        self.active = False
        self.max_out_tokens: int | None = None
        self._start_time: float | None = None
        self._in_tokens = 0
        self._cached_tokens = 0
        self._out_tokens = 0
        self._tools = 0
        self._subagents = 0
        self._api_calls = 0
        self._mcp_calls = 0
        self._skills = 0

    def on_mount(self) -> None:
        self.set_interval(0.25, self.update_frame)

    def update_frame(self) -> None:
        if self.active:
            frame = self.FRAMES[self.frame_idx % len(self.FRAMES)]
            self.update(f"{frame} {self.text}{self.stats_suffix()}")
            self.frame_idx += 1

    def start(self) -> None:
        self.active = True
        self.frame_idx = 0
        self._start_time = time.monotonic()
        self._in_tokens = 0
        self._cached_tokens = 0
        self._out_tokens = 0
        self._tools = 0
        self._subagents = 0
        self._api_calls = 0
        self._mcp_calls = 0
        self._skills = 0

    def stop(self) -> None:
        self.active = False
        self._start_time = None
        self.update("")

    def note_output(self, text: str) -> None:
        self._out_tokens += estimate_tokens(text)

    def set_input_tokens(self, n: int, cached: int = 0) -> None:
        self._in_tokens = n
        self._cached_tokens = cached

    def set_api_calls(self, n: int) -> None:
        self._api_calls = n

    def set_tool_stats(
        self,
        tools: int,
        subagents: int,
        mcp: int = 0,
        skills: int = 0,
    ) -> None:
        self._tools = tools
        self._subagents = subagents
        self._mcp_calls = mcp
        self._skills = skills

    def stats_suffix(self) -> str:
        if self._start_time is None:
            return ""
        parts = [f"{time.monotonic() - self._start_time:.1f}s"]
        if self._in_tokens:
            in_part = f"↑{self._in_tokens}"
            if self._cached_tokens:
                in_part += f" ({self._cached_tokens} cached)"
            parts.append(in_part)
        if self._out_tokens:
            suffix = f"↓~{self._out_tokens}"
            if self.max_out_tokens:
                pct = min(99, self._out_tokens * 100 // self.max_out_tokens)
                suffix += f" {pct}%"
            parts.append(suffix)
        if self._api_calls:
            parts.append(f"{self._api_calls} api")
        if self._tools:
            tools_part = f"{self._tools} tools"
            if self._mcp_calls:
                tools_part += f" ({self._mcp_calls} mcp)"
            parts.append(tools_part)
        if self._skills:
            parts.append(f"{self._skills} skills")
        if self._subagents:
            parts.append(f"{self._subagents} sub-agents")
        return "[dim]  " + " · ".join(parts) + "[/dim]"


class CompletionPopup(Container):
    """Completion popup showing slash-command subcommands."""

    DEFAULT_CSS = """
    CompletionPopup {
        width: auto;
        max-width: 60;
        height: 0;
        max-height: 8;
        background: $surface;
        border-top: solid $primary;
        padding: 0 2;
        display: none;
    }
    CompletionPopup.-visible {
        display: block;
        height: auto;
    }
    #completion-list {
        width: auto;
        height: auto;
        max-height: 8;
        background: $surface;
    }
    """

    def compose(self) -> ComposeResult:
        yield OptionList(id="completion-list")

    def show_completions(self, completions: list) -> None:
        option_list = self.query_one("#completion-list", OptionList)
        option_list.clear_options()
        for c in completions:
            option_list.add_option(Option(c, id=c))
        if completions:
            self.add_class("-visible")
            option_list.highlighted = 0
        else:
            self.hide_popup()

    def hide_popup(self) -> None:
        self.remove_class("-visible")

    @property
    def is_visible(self) -> bool:
        return "-visible" in self.classes

    def select_next(self) -> None:
        if not self.is_visible:
            return
        option_list = self.query_one("#completion-list", OptionList)
        count = option_list.option_count
        if count == 0:
            return
        if option_list.highlighted is None:
            option_list.highlighted = 0
        else:
            option_list.highlighted = (option_list.highlighted + 1) % count

    def select_prev(self) -> None:
        if not self.is_visible:
            return
        option_list = self.query_one("#completion-list", OptionList)
        count = option_list.option_count
        if count == 0:
            return
        if option_list.highlighted is None:
            option_list.highlighted = count - 1
        else:
            option_list.highlighted = (option_list.highlighted - 1) % count

    def get_selected(self):
        if not self.is_visible:
            return None
        option_list = self.query_one("#completion-list", OptionList)
        idx = option_list.highlighted
        if idx is None or idx < 0:
            return None
        try:
            opt = option_list.get_option_at_index(idx)
            return opt.id
        except Exception:
            return None


class CommandInput(TextArea):
    """TextArea subclass: Enter submits, Ctrl+J inserts newline, Up/Down for
    history."""

    class Submitted(Message):
        def __init__(self, text: str) -> None:
            self.text = text
            super().__init__()

    BINDINGS = [
        Binding("ctrl+j", "newline", "Newline", show=False, priority=True),
        Binding(
            "escape+enter",
            "newline",
            "Newline",
            show=False,
            priority=True,
        ),
    ]

    def __init__(self, history_path: Path | None = None, **kwargs):
        super().__init__(**kwargs)
        self.history_path = history_path
        self._history: list[str] = []
        self._history_idx: int = -1
        self._saved_input: str = ""
        self._completion_timer = None
        self._prev_arrow_key: str = ""
        self._prev_arrow_ts: float = 0.0
        self._wheel_pending: int = 0
        self._wheel_flush_timer = None
        self._last_wheel_ts: float = 0.0
        self._arrow_pending_key: str = ""
        self._arrow_timer = None
        # Password masking: real chars live in _password_real, the widget
        # only ever displays "*" per character (see _tui_getpass).
        self.password_mode: bool = False
        self._password_real: str = ""
        self._load_history()

    def on_mount(self) -> None:
        # Disable cursor blink to keep the input area stable while typing.
        self.cursor_blink = False

    def _load_history(self) -> None:
        """Load history from file."""
        if not self.history_path:
            return
        try:
            from dashscope_agenticx.config import WORKSPACE_DIR

            legacy_file = WORKSPACE_DIR / "session" / "input-history"
            lines: list[str] = []
            for path in (legacy_file, self.history_path):
                if path.exists():
                    lines.extend(
                        line for line in path.read_text().splitlines() if line
                    )
            # Deduplicate while preserving order
            seen: set[str] = set()
            self._history = []
            for line in lines:
                line = self._sanitize_history_text(line)
                if line not in seen:
                    seen.add(line)
                    self._history.append(line)
            # Persist merged history to the new path and remove legacy
            if legacy_file.exists() or self._history != lines:
                self.history_path.parent.mkdir(parents=True, exist_ok=True)
                self.history_path.write_text(
                    "\n".join(self._history) + "\n",
                    encoding="utf-8",
                )
                legacy_file.unlink(missing_ok=True)
        except Exception:
            pass

    @staticmethod
    def _sanitize_history_text(text: str) -> str:
        from dashscope_agenticx.cli.constants import _SECRET_PATTERN
        from dashscope_agenticx.utils import sanitize_text

        return sanitize_text(_SECRET_PATTERN.sub(r"\1***", text))

    def _save_history(self, text: str) -> None:
        """Save history item to file with API key redaction."""
        if not self.history_path:
            return
        try:
            # Redact API keys
            sanitized = self._sanitize_history_text(text)
            self.history_path.parent.mkdir(parents=True, exist_ok=True)
            with open(self.history_path, "a", encoding="utf-8") as f:
                f.write(sanitized + "\n")
        except Exception:
            pass

    def add_to_history(self, text: str) -> None:
        if text and (not self._history or self._history[-1] != text):
            self._history.append(text)
            self._save_history(text)
        self._history_idx = -1
        self._saved_input = ""

    def _on_key(self, event) -> None:
        if event.key in ("pageup", "pagedown", "shift+up", "shift+down"):
            try:
                output = self.app.query_one("#output")
            except Exception:
                output = None
            if output is not None:
                event.prevent_default()
                event.stop()
                if isinstance(output, OutputLog):
                    # The scroll below is deferred, so decide follow from
                    # the computed landing position, not the live offset.
                    if event.key in ("pageup", "shift+up"):
                        output._follow_output = False
                    else:
                        delta = (
                            output.scrollable_content_region.height
                            if event.key == "pagedown"
                            else 1
                        )
                        landed = output.scroll_offset.y + delta
                        if landed >= output.max_scroll_y:
                            output._follow_output = True
                if event.key == "pageup":
                    output.scroll_page_up(animate=False)
                elif event.key == "pagedown":
                    output.scroll_page_down(animate=False)
                elif event.key == "shift+up":
                    output.scroll_up(animate=False)
                else:
                    output.scroll_down(animate=False)
                return

        # On the alternate screen the PyCharm terminal translates the wheel
        # into bursts of arrow keys (2~3 per wheel notch, < 10ms apart);
        # real key presses/auto-repeat are >= 25ms apart. Defer the first
        # key by one window before routing it (history/completion);
        # same-direction repeats within the window classify the whole burst
        # as wheel input, batched into a single scroll (per-key scrolling =
        # per-key full-area repaint, which flickers badly on PyCharm).
        if event.key in ("up", "down"):
            if self._pointer_over_output():
                # JediTerm turns the wheel into arrow keys no matter where
                # the pointer is; the pointer position is the only hint of
                # where the gesture is aimed. Over the output area these
                # keys are scrolls — routing isolated (non-burst) keys to
                # history made the input box visibly change while the
                # output scrolled.
                event.prevent_default()
                event.stop()
                self._prev_arrow_key = event.key
                self._prev_arrow_ts = time.monotonic()
                self._queue_wheel_scroll(event.key)
                return
            now = time.monotonic()
            is_burst = (
                self._prev_arrow_key == event.key
                and now - self._prev_arrow_ts < 0.020
            )
            self._prev_arrow_key = event.key
            self._prev_arrow_ts = now
            if is_burst:
                event.prevent_default()
                event.stop()
                if self._arrow_pending_key:
                    # The burst's first key was deferred earlier — cancel
                    # it and count it as a scroll instead
                    first = self._arrow_pending_key
                    if self._arrow_timer is not None:
                        self._arrow_timer.stop()
                        self._arrow_timer = None
                    self._arrow_pending_key = ""
                    self._queue_wheel_scroll(first)
                self._queue_wheel_scroll(event.key)
                return
            if self._arrow_pending_key:
                # Direction changed: flush the previously deferred key first
                self._apply_deferred_arrow()
            event.prevent_default()
            event.stop()
            self._arrow_pending_key = event.key
            if self._arrow_timer is not None:
                self._arrow_timer.stop()
            self._arrow_timer = self.set_timer(
                0.022,
                self._apply_deferred_arrow,
            )
            return

        if self.password_mode:
            self._handle_password_key(event)
            return

        popup = None
        try:
            popup = self.app.query_one("#completion-popup", CompletionPopup)
        except Exception:
            pass

        if popup and popup.is_visible:
            if event.key == "up":
                event.prevent_default()
                event.stop()
                popup.select_prev()
                return
            if event.key == "down":
                event.prevent_default()
                event.stop()
                popup.select_next()
                return
            if event.key == "tab":
                event.prevent_default()
                event.stop()
                selected = popup.get_selected()
                if selected:
                    self._accept_completion(selected)
                return
            if event.key == "escape":
                event.prevent_default()
                event.stop()
                popup.hide_popup()
                return

        if event.key == "enter":
            event.prevent_default()
            event.stop()
            if popup and popup.is_visible:
                selected = popup.get_selected()
                if selected:
                    self._accept_completion(selected)
                    return
            self.post_message(self.Submitted(self.text))
            return

        if event.key == "up":
            event.prevent_default()
            event.stop()
            self.action_history_prev()
            return
        if event.key == "down":
            event.prevent_default()
            event.stop()
            self.action_history_next()
            return

        super()._on_key(event)
        # Debounce completion/hint updates to avoid flickering on every
        # keystroke.
        if self._completion_timer is not None:
            self._completion_timer.stop()
        self._completion_timer = self.app.set_timer(
            0.05,
            self._update_completions,
        )

    def _pointer_over_output(self) -> bool:
        """Whether the last known pointer position is inside the output area.

        Only meaningful on JediTerm, which is the terminal that translates
        the wheel into arrow keys; elsewhere arrows are real key presses.
        Requires a mouse event to have been seen (a never-reported pointer
        stays at 0,0, which sits inside the output area), and yields while
        the completion popup is open so arrows keep navigating it.
        """
        if not _IS_JEDITERM:
            return False
        screen = self.app.screen
        if not getattr(screen, "_mouse_seen", False):
            return False
        try:
            popup = self.app.query_one("#completion-popup", CompletionPopup)
        except Exception:
            popup = None
        if popup is not None and popup.is_visible:
            return False
        try:
            output = self.app.query_one("#output")
        except Exception:
            return False
        x, y = self.app.mouse_position
        region = output.region
        return region.x <= x < region.right and region.y <= y < region.bottom

    def _queue_wheel_scroll(self, key: str) -> None:
        self._wheel_pending += 1 if key == "down" else -1
        self._last_wheel_ts = time.monotonic()
        if key == "up":
            try:
                self.app.query_one("#output", OutputLog)._follow_output = False
            except Exception:
                pass
        if self._wheel_flush_timer is None:
            self._wheel_flush_timer = self.set_timer(
                _WHEEL_FLUSH_INTERVAL,
                self._flush_wheel_scroll,
            )

    def _flush_wheel_scroll(self) -> None:
        lines = self._wheel_pending
        self._wheel_pending = 0
        if self._wheel_flush_timer is not None:
            self._wheel_flush_timer.stop()
        self._wheel_flush_timer = None
        if not lines:
            return
        try:
            output = self.app.query_one("#output", OutputLog)
            screen = self.app.screen
        except Exception:
            return
        if lines < 0:
            output._follow_output = False
        output.scroll_to(
            y=output.scroll_offset.y + lines,
            animate=False,
            immediate=True,
        )
        if lines > 0 and output.scroll_offset.y >= output.max_scroll_y:
            output._follow_output = True
        # Wheel scrolling mid-drag (PyCharm wheel = arrow keys) must also
        # keep the selection following the pointer, or the selection cannot
        # grow past one screen
        extend = getattr(screen, "extend_selection_to", None)
        pointer = getattr(screen, "_auto_scroll_pointer", None)
        if extend is not None and pointer is not None:
            extend(pointer)

    def _apply_deferred_arrow(self) -> None:
        """Route a deferred arrow that turned out to be a real keypress."""
        key = self._arrow_pending_key
        self._arrow_pending_key = ""
        if self._arrow_timer is not None:
            self._arrow_timer.stop()
            self._arrow_timer = None
        if not key or self.password_mode:
            return
        if self._pointer_over_output():
            self._queue_wheel_scroll(key)
            return
        # A decelerating trackpad gesture keeps emitting isolated arrows
        # past the 20ms burst window; while a wheel gesture is (or was
        # just) active, these stragglers are scrolls, not history/completion
        # keys — routing them there visibly raced the input box and popup.
        if time.monotonic() - self._last_wheel_ts < _WHEEL_TAIL_WINDOW:
            self._queue_wheel_scroll(key)
            return
        popup = None
        try:
            popup = self.app.query_one("#completion-popup", CompletionPopup)
        except Exception:
            pass
        if popup is not None and popup.is_visible:
            if key == "up":
                popup.select_prev()
            else:
                popup.select_next()
            return
        if key == "up":
            self.action_history_prev()
        else:
            self.action_history_next()

    def _handle_password_key(self, event) -> None:
        """Password input: real chars go to _password_real, show only '*'."""
        event.prevent_default()
        event.stop()
        if event.key == "enter":
            text = self._password_real
            self._password_real = ""
            self.password_mode = False
            self.text = ""
            self.post_message(self.Submitted(text))
            return
        if event.key == "backspace":
            self._password_real = self._password_real[:-1]
        elif event.character and event.character.isprintable():
            self._password_real += event.character
        self.text = "*" * len(self._password_real)

    def _accept_completion(self, selected: str) -> None:
        """Accept a completion candidate into the input."""
        from dashscope_agenticx.cli import _AT_PATH_AT_CURSOR_RE

        text = self.text

        # @path completion: replace from the @ token, preserving the @ symbol
        m = _AT_PATH_AT_CURSOR_RE.search(text)
        if m:
            # No trailing space for directories so the popup stays open
            # for drilling
            suffix = " " if not selected.endswith("/") else ""
            self.text = text[: m.start()] + "@" + selected + suffix
            lines = self.text.splitlines()
            self.cursor_location = (
                len(lines) - 1,
                len(lines[-1]) if lines else 0,
            )
            self.app.call_after_refresh(self._update_completions)
            return

        if text.endswith(" "):
            self.text = text + selected + " "
        else:
            last_space = text.rfind(" ")
            if last_space >= 0:
                self.text = text[: last_space + 1] + selected + " "
            else:
                self.text = selected + " "
        lines = self.text.splitlines()
        self.cursor_location = (len(lines) - 1, len(lines[-1]) if lines else 0)
        self.app.call_after_refresh(self._update_completions)

    def _update_completions(self) -> None:
        """Refresh completion popup based on current input text."""
        popup = None
        try:
            popup = self.app.query_one("#completion-popup", CompletionPopup)
        except Exception:
            return
        completions = self.app._compute_completions(self.text)
        if completions:
            popup.show_completions(completions)
        else:
            popup.hide_popup()

        # Update ghost hint
        try:
            import dashscope_agenticx.cli as cli_module

            hint = cli_module._get_arg_hint(self.text)
            self.app._refresh_hint_label(hint or None)
        except Exception:
            pass

    def action_newline(self) -> None:
        self.insert("\n")

    def action_history_prev(self) -> None:
        if not self._history:
            return
        if self._history_idx == -1:
            self._saved_input = self.text
            self._history_idx = len(self._history) - 1
        elif self._history_idx > 0:
            self._history_idx -= 1
        self.text = self._history[self._history_idx]
        lines = self.text.splitlines()
        self.cursor_location = (len(lines) - 1, len(lines[-1]) if lines else 0)

    def action_history_next(self) -> None:
        if self._history_idx == -1:
            return
        if self._history_idx < len(self._history) - 1:
            self._history_idx += 1
            self.text = self._history[self._history_idx]
        else:
            self._history_idx = -1
            self.text = self._saved_input
        lines = self.text.splitlines()
        self.cursor_location = (len(lines) - 1, len(lines[-1]) if lines else 0)


class AgenticXApp(App):
    """Textual-based CLI with fixed input and scrolling output."""

    theme: Any  # textual App property
    _clipboard_request: _NativeCopyRequest | None
    _clipboard_settle_timer: Timer | None

    def get_default_screen(self) -> Screen:
        return AcliScreen()

    def get_css_variables(self) -> dict[str, str]:
        """Override to provide theme colors from config to CSS variables."""
        variables = super().get_css_variables()
        # textual-dark/light do not define screen-selection-*; the default
        # resolves to a near-invisible style with foreground ≈ background
        # (selection copies but shows no highlight); set explicit
        # high-contrast selection colors
        try:
            light = self._is_light_mode()
        except Exception:
            light = False
        if light:
            variables["screen-selection-background"] = "#ADD6FF"
            variables["screen-selection-foreground"] = "#1A1A1A"
        else:
            variables["screen-selection-background"] = "#264F78"
            variables["screen-selection-foreground"] = "#FFFFFF"
        if hasattr(self, "config") and self.config and self.config.theme:
            theme = self.config.theme
            # Map config.theme to CSS variables used in CSS below
            if "background" in theme:
                variables["surface"] = theme["background"]
            if "accent" in theme:
                variables["primary"] = theme["accent"]
            if "text" in theme:
                variables["text"] = theme["text"]
            if "muted" in theme:
                variables["text-muted"] = theme["muted"]
        return variables

    CSS = """
    Screen {
        layout: vertical;
    }

    #output {
        height: 1fr;
        border: none;
        overflow-y: auto;
        scrollbar-size: 0 0;
        padding: 1 2;
    }

    #spinner {
        height: 1;
        color: $text-muted;
        background: $surface;
    }

    #input-container {
        height: 7;
        background: $surface;
        border-top: solid $primary;
        padding: 0 2 1 2;
    }

    #command-input {
        height: 1fr;
        background: $surface;
        color: $text;
        border: none;
    }

    #command-input:focus {
        background: $surface;
        color: $text;
    }

    #hint-label {
        height: 1;
        color: $text-muted;
        text-style: italic;
        padding: 0;
    }

    #selection-actions {
        height: 1;
        color: $text;
    }

    #completion-popup {
        width: auto;
        max-width: 60;
        height: 0;
        max-height: 8;
        background: $surface;
        border-top: solid $primary;
        padding: 0 2;
        display: none;
    }
    #completion-popup.-visible {
        display: block;
        height: auto;
    }

    #confirm-container {
        height: 0;
        display: none;
    }
    #confirm-container.-active {
        display: block;
        height: 1;
        background: $surface;
        padding: 0 2;
    }
    #completion-list {
        height: auto;
        max-height: 8;
        background: $surface;
    }
    """

    def _is_light_mode(self) -> bool:
        """True when the active Textual base theme is a light theme."""
        try:
            return not self.current_theme.dark
        except Exception:
            return False

    def _apply_theme(self) -> None:
        """Apply theme colours from config to TUI widgets."""
        theme = self.config.theme
        if not theme:
            return

        from textual.color import Color

        # Switch the base Textual theme to match the configured background's
        # luminance. Without this the app stays on textual-dark, whose
        # $foreground and ANSI filter palette are unreadable on a light
        # background. Must happen before get_css_variables() is re-read below.
        app_theme = _app_theme_for_background(theme.get("background"))
        if self.theme != app_theme:
            self.theme = app_theme

        output = self.query_one("#output", RichLog)

        # Store border colors for Panel rendering (Rich) and CSS (Textual)
        self._theme_border = (
            theme.get("border") or theme.get("accent") or "bright_blue"
        )
        self._panel_border = (
            theme.get("panel_border") or theme.get("border") or "bright_blue"
        )

        # Update CSS variables and re-parse stylesheet
        self.stylesheet.set_variables(self.get_css_variables())
        self.stylesheet.reparse()
        # Update all screens and widgets
        self.stylesheet.update(self)
        if self.screen:
            self.stylesheet.update(self.screen)

        if bg := theme.get("background"):
            try:
                color = Color.parse(bg)
                self.styles.background = color
                self.screen.styles.background = color
                output.styles.background = color
                container = self.query_one("#input-container")
                container.styles.background = color
                # Also update completion popup and other widgets
                try:
                    popup = self.query_one("#completion-popup")
                    popup.styles.background = color
                except Exception:
                    pass
                try:
                    confirm = self.query_one("#confirm-container")
                    confirm.styles.background = color
                except Exception:
                    pass
                try:
                    comp_list = self.query_one("#completion-list")
                    comp_list.styles.background = color
                except Exception:
                    pass
            except Exception:
                pass

        if text_color := theme.get("text"):
            try:
                color = Color.parse(text_color)
                inp = self.query_one("#command-input", TextArea)
                inp.styles.color = color
            except Exception:
                pass

        border_color = theme.get("border") or theme.get("accent")
        border_style = theme.get("border_style", "solid")
        if border_color:
            try:
                color = Color.parse(border_color)
                container = self.query_one("#input-container")
                container.styles.border_top = (border_style, color)
                # Update completion popup border too
                try:
                    popup = self.query_one("#completion-popup")
                    popup.styles.border_top = (border_style, color)
                except Exception:
                    pass
            except Exception:
                pass

        if accent := theme.get("accent"):
            try:
                color = Color.parse(accent)
                spinner = self.query_one("#spinner", Spinner)
                spinner.styles.color = color
            except Exception:
                pass

        # Force a full refresh to apply all changes
        self.refresh(layout=True)
        if self.screen:
            self.screen.refresh(layout=True)

    BINDINGS = [
        # priority=True: match on the App side first, overriding the
        # Screen's own ctrl+c→copy_text (OSC 52 only, silently fails on
        # PyCharm/JediTerm)
        Binding(
            "ctrl+c",
            "smart_quit",
            "Cancel/Quit",
            show=True,
            priority=True,
        ),
        Binding(
            "super+c",
            "copy_selection_after_refresh",
            "Copy selection",
            show=False,
            priority=True,
        ),
        Binding("ctrl+t", "voice_input", "Voice", show=False),
        Binding("ctrl+q", "quote_selection", "Quote selection", show=False),
        Binding(
            "ctrl+o",
            "toggle_tool_details",
            "Tool details",
            show=False,
            priority=True,
        ),
        Binding(
            "ctrl+g",
            "toggle_statistics",
            "Statistics",
            show=False,
            priority=True,
        ),
    ]

    def action_toggle_tool_details(self) -> None:
        self.query_one("#output", OutputLog).toggle_tool_details()

    def action_toggle_statistics(self) -> None:
        self.query_one("#output", OutputLog).toggle_statistics()

    def action_voice_input(self) -> None:
        """Trigger voice input via Ctrl+T hotkey."""
        if not getattr(self, "_processing", False):
            asyncio.create_task(self._handle_voice_input())

    def action_copy_selection(self) -> bool:
        """Copy the output-area selection to the system clipboard.

        A terminal on the alternate screen only copies the visible screen,
        so a multi-screen selection loses off-screen content. Explicitly
        take Textual's internal selection (including the scrolled-off part)
        and write it to the system clipboard: prefer native tools (pbcopy
        etc.), fall back to OSC 52 when unavailable. No auto-copy on
        mouse-up, to avoid clobbering the user's existing clipboard
        content. Returns whether a copy was performed. Ctrl+C invokes
        this action only on non-macOS platforms; macOS uses Cmd+C or Copy.
        """
        text = self.screen.get_selected_text()
        return self._copy_text(text)

    def action_copy_selection_after_refresh(self) -> bool:
        return self._copy_text(
            self.screen.get_selected_text(),
            after_refresh=True,
        )

    def _native_screen_selection(self) -> Selection | None:
        if sys.platform != "darwin" or not _IS_JEDITERM:
            return None
        state = self.screen._select_state
        if state is None or state.end is None:
            return None
        start = (
            state.start.container_initial_offset
            + state.start.container_pointer_delta
        )
        selection = Selection.from_offsets(start, state.screen_offset)
        return Selection(selection.start, selection.end + (1, 0))

    def _native_screen_candidate(self, selection: Selection) -> str:
        selected = []
        for index, strip in enumerate(self.screen._compositor.render_strips()):
            span = selection.get_span(index)
            if span is not None:
                start, end = span
                selected.append(
                    strip.crop(start, None if end == -1 else end).text,
                )
        return "\n".join(selected)

    def _copy_text(
        self,
        text: str | None,
        *,
        after_refresh: bool = False,
        native_candidates: tuple[str, ...] | None = None,
    ) -> bool:
        if not text or not text.strip():
            return False
        from dashscope_agenticx.utils.clipboard import copy_to_clipboard

        self._clipboard_copy_generation += 1
        generation = self._clipboard_copy_generation
        self._stop_clipboard_watch()
        if native_candidates is None:
            native_candidates = self.query_one(
                "#output",
                OutputLog,
            ).native_copy_candidates()
        screen_selection = self._native_screen_selection()
        if screen_selection is not None:
            native_candidates += (
                self._native_screen_candidate(screen_selection),
            )
        self.screen.clear_selection()
        self._clear_selection_actions()
        self.screen.refresh()
        tool = copy_to_clipboard(text)
        if after_refresh and tool == "pbcopy":
            request = self._native_copy_request(
                text,
                native_candidates,
                expires=time.monotonic() + 1.0,
                screen_selection=screen_selection,
            )
            if request is not None:
                self._clipboard_request = request
                self.call_after_refresh(
                    self._schedule_clipboard_settle,
                    request,
                    generation,
                )
        if not tool:
            # OSC 52: the terminal writes the text to the system clipboard
            # (unsupported by macOS Terminal; iTerm2 needs "Allow clipboard
            # access to terminal apps" enabled)
            self.copy_to_clipboard(text)
            self.notify(
                "Clipboard tool unavailable; sent OSC 52 copy request. "
                "The terminal may not support it.",
                severity="warning",
                timeout=3,
            )
        return True

    def _native_copy_request(
        self,
        text: str,
        candidates: tuple[str, ...],
        *,
        selections: dict[Any, Selection] | None = None,
        expires: float | None = None,
        screen_selection: Selection | None = None,
    ) -> _NativeCopyRequest | None:
        if sys.platform != "darwin" or not _IS_JEDITERM:
            return None
        from dashscope_agenticx.utils.clipboard import get_macos_clipboard

        clipboard = get_macos_clipboard()
        if clipboard is None:
            return None
        revision = clipboard.change_count()
        if revision is None:
            return None
        return _NativeCopyRequest(
            clipboard,
            revision,
            text,
            candidates,
            selections,
            expires,
            verify=expires is not None,
            screen_selection=screen_selection,
        )

    def _stop_clipboard_watch(self) -> None:
        self._clipboard_request = None
        if self._clipboard_settle_timer is not None:
            self._clipboard_settle_timer.stop()
            self._clipboard_settle_timer = None

    def _watch_native_selection_copy(self, text: str) -> None:
        self._stop_clipboard_watch()
        output = self.query_one("#output", OutputLog)
        selection = output.text_selection
        if selection is None:
            return
        for widget, widget_selection in self.screen.selections.items():
            if widget is output:
                continue
            selected = widget.get_selection(widget_selection)
            if selected is not None and selected[0].strip():
                return
        request = self._native_copy_request(
            text,
            output.native_copy_candidates(),
            selections=self.screen.selections.copy(),
            screen_selection=self._native_screen_selection(),
        )
        if request is not None:
            self._clipboard_request = request
            self._schedule_clipboard_settle(
                request,
                self._clipboard_copy_generation,
            )

    def _schedule_clipboard_settle(
        self,
        request: _NativeCopyRequest,
        generation: int,
    ) -> None:
        if (
            generation != self._clipboard_copy_generation
            or self._clipboard_request is not request
        ):
            return
        if request.expires is not None:
            request.expires = time.monotonic() + 1.0
        self._clipboard_settle_timer = self.set_interval(
            0.1,
            lambda: self._settle_clipboard_copy(request, generation),
        )

    def _settle_clipboard_copy(
        self,
        request: _NativeCopyRequest,
        generation: int,
    ) -> None:
        """Expand native copying only when it matches the output snapshot."""
        if (
            generation != self._clipboard_copy_generation
            or self._clipboard_request is not request
        ):
            return
        if (
            request.expires is not None and time.monotonic() >= request.expires
        ) or not self.app_focus:
            self._stop_clipboard_watch()
            return
        if request.selections is not None:
            output = self.query_one("#output", OutputLog)
            if (
                self.screen.selections != request.selections
                or self.screen._selecting
            ):
                self._stop_clipboard_watch()
                return
        revision = request.clipboard.change_count()
        if revision is None:
            self._stop_clipboard_watch()
            return
        if revision == request.revision and not request.verify:
            return
        candidates = request.candidates[:3]
        if request.selections is not None:
            candidates += output.native_copy_candidates()
        if request.screen_selection is not None:
            candidates += (
                self._native_screen_candidate(request.screen_selection),
            )
        request.candidates = tuple(dict.fromkeys(candidates))
        copied_text = request.clipboard.read_text()
        if request.clipboard.change_count() != revision:
            return
        request.verify = False
        request.revision = revision
        from dashscope_agenticx.utils.clipboard import (
            copy_to_clipboard,
            matches_native_selection,
        )

        if copied_text != request.text and not matches_native_selection(
            copied_text,
            request.candidates,
        ):
            self._stop_clipboard_watch()
            return
        if request.selections is not None:
            self._copy_text(
                request.text,
                after_refresh=True,
                native_candidates=request.candidates,
            )
        elif copied_text != request.text:
            if copy_to_clipboard(request.text):
                revision = request.clipboard.change_count()
                if revision is None:
                    self._stop_clipboard_watch()
                else:
                    request.revision = revision
            else:
                self._stop_clipboard_watch()
                self.notify(
                    "Clipboard copy retry failed; click Copy",
                    severity="warning",
                    timeout=3,
                )

    def on_app_blur(self, event: events.AppBlur) -> None:
        request = self._clipboard_request
        if request is not None:
            self._settle_clipboard_copy(
                request,
                self._clipboard_copy_generation,
            )
        self._stop_clipboard_watch()

    def action_smart_quit(self) -> None:
        # Ctrl+C = cancel: close completion popup → cancel recording →
        # discard input box content → cancel task → press again with empty
        # input to quit
        if sys.platform != "darwin" and self.action_copy_selection():
            self._ctrl_c_once = False
            return
        try:
            popup = self.query_one("#completion-popup", CompletionPopup)
            if popup.is_visible:
                popup.hide_popup()
                self._ctrl_c_once = False
                return
        except Exception:
            pass

        # If voice recording is active, cancel it
        if (
            getattr(self, "_voice_recording", False)
            and self._voice_cancel_event
        ):
            self._voice_cancel_event.set()
            output = self.query_one("#output", RichLog)
            output.write(Text("Recording cancelled", style="yellow"))
            return

        # Input box has content: discard current input (do not quit)
        input_widget = self.query_one("#command-input", CommandInput)
        if input_widget.text.strip():
            input_widget.text = ""
            self._ctrl_c_once = False
            return

        if self._processing:
            if self._agent_task and not self._agent_task.done():
                self._agent_task.cancel()
                self._ctrl_c_once = True
                output = self.query_one("#output", RichLog)
                output.write(Text("Press Ctrl+C again to quit", style="dim"))
        elif self._ctrl_c_once:
            self.exit()
        else:
            self._ctrl_c_once = True
            output = self.query_one("#output", RichLog)
            output.write(Text("Press Ctrl+C again to quit", style="dim"))

    def __init__(
        self,
        config,
        agent,
        input_history_path: Path | None = None,
        **kwargs,
    ):
        super().__init__(**kwargs)
        self.config = config
        self.agent = agent
        self.input_history_path = input_history_path
        self._processing = False
        self._pending_commands: list[str] = []
        self._agent_task: asyncio.Task | None = None
        self._ctrl_c_once = False
        self._confirm_future: asyncio.Future | None = None
        self._confirm_is_dangerous: bool = False
        # Serializes concurrent confirm prompts (created lazily on the
        # running loop) so a second confirm cannot clobber _confirm_future.
        self._confirm_lock: asyncio.Lock | None = None
        self._supplement_future: asyncio.Future | None = None
        # Draft text parked out of the input box while a confirmation
        # prompt owns the box; restored when the prompt resolves.
        self._confirm_saved_draft: str | None = None
        # Inline input state — for input() calls without modal popups
        self._inline_input_lock = threading.Lock()
        self._inline_input_future: threading.Event | None = None
        self._inline_input_value: list[str] = [""]
        self._inline_input_active: bool = False
        # Set on teardown: wizards parked in executor threads get an empty
        # answer (and future prompts fail fast) instead of blocking the
        # default-executor join at interpreter shutdown.
        self._inline_input_aborted: bool = False
        # Calm streaming (old JediTerm fallback): writes during streaming
        # do not follow-scroll
        self._calm_streaming: bool = False
        self._turn_number = 0
        self._clipboard_copy_generation = 0
        self._clipboard_settle_timer = None
        self._clipboard_request = None
        self._session_activity = {
            "tools": 0,
            "mcp": 0,
            "skills": 0,
            "subagents": 0,
            "failures": 0,
            "duration_ms": 0,
            "timed_tools": 0,
        }
        # Hook executor's confirm callback
        self.agent.executor._confirm_callback = self._tui_confirm_callback
        # Store event loop for thread-safe input() calls
        self._loop: asyncio.AbstractEventLoop | None = None

    def _compute_completions(self, text: str) -> list:
        """Compute completion candidates for slash commands and @ paths."""
        from dashscope_agenticx.cli import (
            _AT_PATH_AT_CURSOR_RE,
            _SUBCOMMANDS,
            _TOP_LEVEL_COMMANDS,
        )

        # @path completion takes priority — works anywhere in the input
        m = _AT_PATH_AT_CURSOR_RE.search(text)
        if m:
            return self._path_completions(m.group(1))

        stripped = text.lstrip()
        if not stripped.startswith("/"):
            return []

        ends_with_space = text != "" and text[-1] in (" ", "\t")
        tokens = stripped.split()

        if ends_with_space:
            current = ""
            arg_index = len(tokens)
        else:
            current = tokens[-1] if tokens else ""
            arg_index = max(0, len(tokens) - 1)

        if arg_index == 0:
            return [c for c in _TOP_LEVEL_COMMANDS if c.startswith(current)]

        cmd = tokens[0]
        if arg_index == 1 and cmd in _SUBCOMMANDS:
            subs = _SUBCOMMANDS[cmd]
            # Dynamic completions
            if cmd == "/skill":
                from dashscope_agenticx.skills import BUILTIN_SKILLS
                from dashscope_agenticx.skills.base import load_skill_files

                load_skill_files()
                subs = subs + list(BUILTIN_SKILLS.keys())

            # Check if current token exactly matches a first-level subcommand
            # If so, show second-level completions
            if current in subs and cmd == "/dev":
                from dashscope_agenticx.cli import _DEV_SUBCOMMANDS

                if current in _DEV_SUBCOMMANDS:
                    return _DEV_SUBCOMMANDS[current]

            return [s for s in subs if s.startswith(current)]

        # Handle nested subcommands like /dev model list
        if arg_index == 2 and cmd == "/dev":
            from dashscope_agenticx.cli import _DEV_SUBCOMMANDS

            parent = tokens[1]
            if parent in _DEV_SUBCOMMANDS:
                return [
                    s
                    for s in _DEV_SUBCOMMANDS[parent]
                    if s.startswith(current)
                ]

        # /tts voice <partial> — show voice names for current TTS model
        if (
            arg_index == 2
            and cmd == "/tts"
            and len(tokens) >= 2
            and tokens[1] == "voice"
        ):
            from dashscope_agenticx.ui.tts import TTS_VOICES

            voices = TTS_VOICES.get(self.config.tts_model, [])
            return [v for v in voices if v.startswith(current)]

        return []

    def _path_completions(self, raw_path: str) -> list:
        """List entries of the directory implied by ``raw_path``."""
        from dashscope_agenticx.cli import _PATH_COMPLETION_LIMIT, _is_dir_safe

        sep = os.sep
        if raw_path == "":
            dir_str, prefix = ".", ""
        elif raw_path.endswith("/") or raw_path.endswith(sep):
            dir_str, prefix = raw_path, ""
        elif "/" in raw_path or sep in raw_path:
            last = max(raw_path.rfind("/"), raw_path.rfind(sep))
            dir_str, prefix = raw_path[: last + 1], raw_path[last + 1 :]
        elif raw_path in (".", "..", "~"):
            dir_str, prefix = raw_path + sep, ""
        else:
            dir_str, prefix = ".", raw_path

        directory = Path(dir_str).expanduser()
        if not directory.is_dir():
            return []

        try:
            entries = []
            for entry in directory.iterdir():
                entries.append(entry)
                if len(entries) >= _PATH_COMPLETION_LIMIT * 4:
                    break
        except (OSError, PermissionError):
            return []
        entries.sort(key=lambda e: (not _is_dir_safe(e), e.name.lower()))

        results = []
        shown = 0
        path_prefix = "" if dir_str in (".", "./", f".{sep}") else dir_str
        for entry in entries:
            if shown >= _PATH_COMPLETION_LIMIT:
                break
            name = entry.name
            if name.startswith(".") and not prefix.startswith("."):
                continue
            if not name.startswith(prefix):
                continue
            is_dir = _is_dir_safe(entry)
            completion = path_prefix + name + (sep if is_dir else "")
            results.append(completion)
            shown += 1
        return results

    def _write_output(self, content) -> None:
        """Write to the output log (auto-scrolls only when already at the
        bottom).

        Pass the widget's content width explicitly for plain text so long lines
        are rendered at the full available width rather than wrapping at a
        narrow fallback. Rich renderables like Panels manage their own width.
        """
        output = self.query_one("#output", RichLog)
        width = None
        if isinstance(content, (str, Text, OutputBlock)):
            widget_width = output.size.width or 80
            width = widget_width - 4 if widget_width > 4 else None
        # During calm streaming: content is still written but the view does
        # not follow-scroll — on old JediTerm following means a full-screen
        # tear on every flush (flicker), while a frozen viewport means zero
        # repaints
        scroll_end = False if self._calm_streaming else None
        output.write(content, width=width, scroll_end=scroll_end)

    def on_text_selected(self, event: events.TextSelected) -> None:
        # Do not write the clipboard automatically on mouse-up — that would
        # clobber the user's clipboard content.
        text = self.screen.get_selected_text()
        if not text or not text.strip():
            return
        shortcut = self._copy_shortcut_hint()
        self.query_one("#hint-label", Static).display = False
        self.query_one("#selection-actions", SelectionActions).show_selection(
            text,
            shortcut,
        )
        self._watch_native_selection_copy(text)

    @staticmethod
    def _copy_shortcut_hint() -> str:
        if sys.platform == "darwin":
            return "Cmd+C to copy; if intercepted, click Copy"
        return "Ctrl+C to copy"

    def _clear_selection_actions(self) -> None:
        self.query_one("#selection-actions", SelectionActions).clear()
        self.query_one("#hint-label", Static).display = True

    def on_selection_actions_requested(
        self,
        event: SelectionActions.Requested,
    ) -> None:
        if event.action == "copy":
            self._copy_text(event.text)
        elif event.action == "quote":
            self._quote_text(event.text)

    def action_quote_selection(self) -> None:
        """Ctrl+Q: insert the output-area selection as a quote block into
        the input box, handy for follow-up questions."""
        text = self.screen.get_selected_text()
        self._quote_text(text)

    def _quote_text(self, text: str | None) -> None:
        if not text or not text.strip():
            self.notify("Select content in the output area first", timeout=3)
            return
        quote = "\n".join(
            f"> {line}" for line in text.rstrip("\n").splitlines()
        )
        input_widget = self.query_one("#command-input", CommandInput)
        existing = input_widget.text
        # Append a blank line: the cursor lands on a fresh line below the
        # quote block, ready for a follow-up question
        input_widget.text = (
            f"{existing}\n{quote}\n" if existing.strip() else f"{quote}\n"
        )
        input_widget.focus()
        input_widget.move_cursor(input_widget.document.end)
        self.screen.clear_selection()
        self._clear_selection_actions()
        self._ctrl_c_once = False

    def on_command_input_submitted(
        self,
        event: CommandInput.Submitted,
    ) -> None:
        """Handle Enter key in the command input."""
        command = event.text.strip()
        input_widget = self.query_one("#command-input", CommandInput)

        # Check if we're waiting for confirmation response
        if self._confirm_future and not self._confirm_future.done():
            choice = command.lower() or "y"  # Empty = default to yes
            # Dangerous ops accept only y/n, matching the sync path (no
            # always-trust granted)
            valid_choices = (
                ("y", "n")
                if self._confirm_is_dangerous
                else ("y", "n", "u", "a", "s")
            )
            if choice in valid_choices:
                input_widget.text = ""
                self._confirm_future.set_result(choice)
                self._confirm_future = None
            else:
                # Remove the invalid answer only; the user's draft was
                # already parked into _confirm_saved_draft at prompt time.
                input_widget.text = ""
                output = self.query_one("#output", RichLog)
                if self._confirm_is_dangerous:
                    output.write(
                        render("[yellow]Enter y/n (Enter = yes)[/yellow]"),
                    )
                else:
                    output.write(
                        render(
                            "[yellow]Enter y/n/u/a/s "
                            "(Enter = yes)[/yellow]",
                        ),
                    )
            input_widget.focus()
            return

        # Check if we're waiting for inline input (e.g., /setup prompts)
        if self._inline_input_active and self._inline_input_future:
            self._inline_input_value[0] = command
            self._inline_input_future.set()
            self._inline_input_active = False
            input_widget.password_mode = False
            input_widget._password_real = ""
            input_widget.text = ""
            input_widget.focus()
            return

        # Check if we're waiting for supplemental info after [u]pdate
        if self._supplement_future and not self._supplement_future.done():
            input_widget.text = ""
            self._supplement_future.set_result(command)
            self._supplement_future = None
            return

        if command:
            input_widget.add_to_history(command)
            input_widget.text = ""
            self._handle_command(command)
            self._refresh_hint_label()
        input_widget.focus()

    def compose(self) -> ComposeResult:
        """Create child widgets."""
        yield OutputLog(
            id="output",
            wrap=True,
            highlight=False,
            markup=True,
            auto_scroll=True,
            max_lines=10000,
        )
        yield Spinner(id="spinner")
        yield CompletionPopup(id="completion-popup")
        yield Container(id="confirm-container")
        with Container(id="input-container"):
            yield CommandInput(
                id="command-input",
                language=None,
                show_line_numbers=False,
                history_path=self.input_history_path,
            )
            yield Static(_INPUT_IDLE_HINT, id="hint-label")
            yield SelectionActions(id="selection-actions")

    def _render_banner(self) -> None:
        """Render the ASCII logo and info panel into the output area."""
        from dashscope_agenticx.cli.constants import STARTUP_LOGO

        output = self.query_one("#output", RichLog)

        # Banner
        for line in STARTUP_LOGO.splitlines():
            output.write(Text(line, style="bold cyan"))
        output.write("")

        # Build detailed info panel
        from dashscope_agenticx import __version__
        from dashscope_agenticx.tools.registry import registry

        embedded_name = getattr(self.config, "_embedded_app_name", None)
        info_lines = [
            f"[bold]{embedded_name or 'AgenticX'}[/bold] "
            f"v{__version__} — Drive everything with natural language\n",
        ]

        # Provider, Model, User
        from dashscope_agenticx.cli.examples import _selected_expert

        user_display = self.config.user_name or "(not set)"
        info_lines.append(
            f"[bold]Provider:[/bold] [cyan]{self.config.provider}[/cyan]  "
            f"[bold]Model:[/bold] [cyan]{self.config.model}[/cyan]  "
            f"[bold]User:[/bold] [cyan]{user_display}[/cyan]",
        )
        info_lines.append(
            "[bold]Expert:[/bold] "
            f"[cyan]{_selected_expert() or '(not recorded)'}[/cyan]",
        )

        # API Key status
        from dashscope_agenticx.cli.handlers_key import all_key_targets

        targets = all_key_targets(self.config)
        key_info = targets.get(self.config.provider)
        if key_info:
            key_val = getattr(self.config, key_info["field"], "")
            if key_val:
                info_lines.append(
                    f"[bold]API Key:[/bold] [green]✓ "
                    f"{mask_secret(key_val)}[/green] "
                    f"[dim]({key_info['env']})[/dim]",
                )
            else:
                info_lines.append(
                    f"[bold]API Key:[/bold] [red]✗ not set[/red] "
                    f"[dim]({key_info['env']})[/dim]",
                )

        # Enabled capabilities
        caps_display = self.config.enabled_capabilities
        if caps_display is not None:
            caps_str = ", ".join(caps_display) if caps_display else "none"
            info_lines.append(
                f"[bold]Capabilities:[/bold] [dim]{caps_str}[/dim]",
            )

        # Tool count
        tool_count = (
            len(registry.list_tools())
            if hasattr(registry, "list_tools")
            else "?"
        )
        info_lines.append(
            f"[bold]Tools:[/bold] [dim]{tool_count} registered[/dim]",
        )

        # Scenario doc link (embedded mode only)
        guide_url = getattr(self.config, "_embedded_guide_url", "")
        if guide_url:
            info_lines.append(
                f"[bold]Guide:[/bold] [dim]{guide_url}[/dim]",
            )

        info_lines.append(
            "\n[dim]Input: Enter to submit; Ctrl+J newline; "
            "Ctrl+C cancel/quit [/dim]",
        )
        info_lines.append(
            "[dim]Output: wheel to scroll; drag to select, then "
            + self._copy_shortcut_hint()
            + "; Ctrl+Q to quote; Ctrl+O tool details; Ctrl+G statistics"
            "[/dim]",
        )

        panel_border = (
            getattr(self, "_panel_border", "bright_blue") or "bright_blue"
        )
        output.write(Panel("\n".join(info_lines), border_style=panel_border))
        output.write("")

    def on_mount(self) -> None:
        """Initialize the app."""
        # JetBrains terminals (PyCharm etc.) support synchronized-output
        # (2026) but never answer Textual's DECRQM probe, so every frame is
        # written raw and scrolling/streaming tears the whole screen
        # (flicker). Force it on even though undetected: terminals without
        # support silently ignore the private mode sequence.
        if _IS_JEDITERM:
            self._sync_available = True

        # Apply theme before rendering anything
        self._apply_theme()

        self._render_banner()
        self._refresh_hint_label()

        # Restore prior conversation if session persistence is enabled.
        if self.agent.session_path:
            output = self.query_one("#output", RichLog)
            try:
                restored = self.agent.load_session()
                if restored:
                    output.write(
                        Text(
                            f"  [Restored {restored} history messages]",
                            style="dim",
                        ),
                    )
                else:
                    output.write(Text("  [No history messages]", style="dim"))
                output.write("")
            except Exception:
                output.write(Text("  [Failed to load history]", style="dim"))
                output.write("")

        # Focus the input
        input_widget = self.query_one("#command-input", CommandInput)
        input_widget.focus()

        # Store event loop reference for thread-safe input() calls
        self._loop = asyncio.get_event_loop()

        # Monkey-patch builtins.input and getpass.getpass so that blocking
        # handlers (e.g. /key, /dev xxx add, /setup, /update without args)
        # can prompt via the TUI modal instead of hanging on stdin. Never
        # reverted — see on_unmount.
        import builtins
        import getpass

        builtins.input = self._tui_input
        getpass.getpass = self._tui_getpass

    async def on_unmount(self) -> None:
        """Wake any wizard parked on input() in an executor thread.

        The input()/getpass() patch deliberately stays installed: restoring
        the originals here races the unwinding wizard, whose next prompt
        could land on the real input() and block on stdin, stranding the
        default executor at interpreter shutdown. Post-exit calls hit the
        aborted flag in _tui_input and fail fast with EOFError.
        """
        self._inline_input_aborted = True
        self._cancel_inline_input()
        self._stop_clipboard_watch()
        from dashscope_agenticx.runtime import Runtime

        runtime = getattr(self.agent, "runtime", None)
        if isinstance(runtime, Runtime):
            await runtime.aclose()

    async def _tui_confirm_callback(
        self,
        tool_def,
        arguments: dict,
        is_dangerous: bool,
    ) -> str:
        """Async callback for executor — display confirmation in TUI output
        area.

        Concurrent confirmations are serialized through a lock: a second
        prompt waits for the first to resolve instead of clobbering
        `_confirm_future` (which would hang the first waiter).
        """
        if self._confirm_lock is None:
            self._confirm_lock = asyncio.Lock()
        async with self._confirm_lock:
            return await self._prompt_confirm(
                tool_def,
                arguments,
                is_dangerous,
            )

    async def _prompt_confirm(
        self,
        tool_def,
        arguments: dict,
        is_dangerous: bool,
    ) -> str:
        """Show one confirmation prompt and wait for the user's choice."""
        from dashscope_agenticx.utils.text import truncate_value

        # Build the confirmation panel
        title = (
            "⚠️  Dangerous operation"
            if is_dangerous
            else "Confirmation required"
        )
        border_style = "red bold" if is_dangerous else "yellow"
        args_display = "\n".join(
            f"  {k}: {truncate_value(v)}" for k, v in arguments.items()
        )
        content = f"Tool: {tool_def.name}\nArguments:\n{args_display}"

        self._write_output(
            Panel(content, title=title, border_style=border_style),
        )

        # Prompt text
        if is_dangerous:
            prompt_text = Text("Execute? [y]es / [n]o  [y]", style="bold")
        else:
            prompt_text = Text(
                "Execute? [y]es / [n]o / [u]pdate (add info, replan) / "
                "[a]lways (allow this session) / [s]top (abort turn)  [y]",
                style="bold",
            )
        self._write_output(prompt_text)
        # Make sure the pending confirmation is visible to the user.
        self.query_one("#output", RichLog).scroll_end(animate=False)

        # Change spinner text to indicate waiting for confirmation
        spinner = self.query_one("#spinner", Spinner)
        old_spinner_text = spinner.text
        was_active = spinner.active
        spinner.text = "Waiting for confirmation..."
        if not was_active:
            spinner.start()
        else:
            spinner.update()

        # Focus input and wait
        input_widget = self.query_one("#command-input", CommandInput)
        # Park whatever the user was drafting out of the box so the
        # confirmation owns a clean input; it is restored in finally.
        self._confirm_saved_draft = input_widget.text or None
        if self._confirm_saved_draft:
            input_widget.text = ""
        input_widget.focus()
        self._confirm_is_dangerous = is_dangerous
        self._confirm_future = asyncio.get_running_loop().create_future()
        try:
            result = await self._confirm_future
            if result == "u":
                self._write_output(
                    Text("Enter supplemental info:", style="bold yellow"),
                )
                # During calm streaming _write_output does not follow-
                # scroll, so scroll explicitly to make it visible
                self.query_one("#output", RichLog).scroll_end(animate=False)
                input_widget.focus()
                self._supplement_future = (
                    asyncio.get_running_loop().create_future()
                )
                try:
                    supplement = await self._supplement_future
                except (asyncio.CancelledError, asyncio.InvalidStateError):
                    supplement = ""
                finally:
                    self._supplement_future = None
                if not supplement.strip():
                    raise UserAbortedTurn("No supplemental info provided")
                raise UserSupplement(supplement.strip())
            return result
        except (asyncio.CancelledError, asyncio.InvalidStateError):
            return "n"
        finally:
            self._confirm_future = None
            # Give the user's draft back (skip if the box already holds
            # newer content, e.g. typing resumed on another path).
            saved_draft = self._confirm_saved_draft
            self._confirm_saved_draft = None
            if saved_draft and not input_widget.text:
                input_widget.text = saved_draft
            # Restore spinner text
            spinner.text = old_spinner_text
            if not was_active:
                spinner.stop()
            else:
                spinner.update()

    # How long an inline input() prompt waits before giving up.
    _INLINE_INPUT_TIMEOUT = 300.0

    def _cancel_inline_input(self) -> None:
        """Wake a thread parked in _tui_input with an empty answer. Every
        wizard treats empty as keep-default/cancel, so the thread unwinds
        through its remaining steps without blocking again. The future
        object is left in place; the waiter clears it."""
        with self._inline_input_lock:
            future = self._inline_input_future
            if future is not None:
                self._inline_input_value[0] = ""
                self._inline_input_active = False
                future.set()

    def _tui_input(self, prompt: str = "", password: bool = False) -> str:
        """Thread-safe replacement for builtins.input() in TUI mode.
        Writes prompt inline to output and reads from command input box.
        Blocks the calling thread until input is received."""
        if self._loop is None or self._inline_input_aborted:
            # App is exiting: nothing can answer this prompt. EOFError
            # mirrors a closed stdin — wizard handlers catch it as
            # keep-default/cancel and unwind without blocking.
            raise EOFError("input() called after TUI exit")

        with self._inline_input_lock:
            if self._inline_input_active:
                # Another interactive command is already prompting — refuse
                # rather than clobbering its future and stealing its answer.
                return ""
            # Set up inline input mode
            self._inline_input_future = threading.Event()
            self._inline_input_value = [""]
            self._inline_input_active = True
            future = self._inline_input_future

        def _show_prompt():
            # Write prompt to output (inline, not modal)
            output = self.query_one("#output", RichLog)
            if password:
                output.write(Text(prompt + " (password input)", style="bold"))
            else:
                output.write(Text(prompt, style="bold"))
            # Focus the command input widget
            input_widget = self.query_one("#command-input", CommandInput)
            input_widget.password_mode = password
            input_widget._password_real = ""
            input_widget.focus()

        self.call_from_thread(_show_prompt)

        # Block until input is received
        future.wait(timeout=self._INLINE_INPUT_TIMEOUT)
        with self._inline_input_lock:
            # Deactivate even on timeout — otherwise the next submitted
            # command would be silently swallowed as the stale answer.
            if self._inline_input_future is future:
                self._inline_input_active = False
                self._inline_input_future = None
        return self._inline_input_value[0]

    def _tui_getpass(self, prompt: str = "") -> str:
        """Thread-safe replacement for getpass.getpass() in TUI mode."""
        return self._tui_input(prompt, password=True)

    def _handle_command(self, command: str, *, _echo: bool = True) -> None:
        """Route command to handler. ``_echo=False`` skips the prompt echo
        (used when draining queued commands — they were already echoed when
        first submitted)."""
        from dashscope_agenticx.cli.constants import EXPERT_COMMAND_ALIASES

        output = self.query_one("#output", RichLog)
        self._clear_selection_actions()
        self.screen.clear_selection()
        # A submitted command re-engages follow mode (OutputLog's sticky
        # auto-scroll otherwise keeps a scrolled-up view in place).
        output.scroll_end(animate=False)

        if self._processing:
            self._pending_commands.append(command)
            prompt_sym = (
                getattr(self.config, "_embedded_prompt_symbol", None)
                or "agenticx> "
            )
            output.write(Text(prompt_sym, style="bold green") + Text(command))
            output.write(
                render("[dim](queued; runs after current task)[/dim]"),
            )
            return

        # Echo user data (skipped for dequeued commands that were already
        # echoed)
        if _echo and command.startswith(("/", "!")):
            sep_width = (output.size.width or 80) - 4
            self._write_output(Text("─" * sep_width, style="dim"))
            prompt_sym = (
                getattr(self.config, "_embedded_prompt_symbol", None)
                or "agenticx> "
            )
            self._write_output(
                Text(prompt_sym, style="bold green") + Text(command),
            )

        if command in ("/exit", "/quit", "/q"):
            self.exit()
        elif command.startswith("!"):
            # Shell escape — run in a worker so a long-running command
            # cannot freeze the event loop.
            shell_cmd = command[1:].strip()
            if shell_cmd:
                asyncio.create_task(self._run_shell_escape(shell_cmd))
            else:
                self._write_output(
                    Text("Usage: !<shell command>", style="yellow"),
                )
        elif command in ("/feedback good", "/feedback bad"):
            import dashscope_agenticx.cli as cli_module

            with _capture_console(self):
                cli_module._handle_slash_command(
                    command,
                    self.agent,
                    self.config,
                )
        elif command == "/report":
            import dashscope_agenticx.cli as cli_module

            with _capture_console(self):
                cli_module._handle_report_command(self.agent)
        elif command == "/help":
            help_text = render_help_text()
            panel_border = (
                getattr(self, "_panel_border", "bright_blue") or "bright_blue"
            )
            self._write_output(
                Panel(
                    render(help_text),
                    title="Help",
                    border_style=panel_border,
                ),
            )
        elif command == "/clear":
            output.clear()
            self.agent.reset()
            if self.agent.session_path:
                self.agent.save_session()
            self._render_banner()
            self._write_output(Text("Conversation cleared", style="dim"))
        elif command.startswith("/"):
            # Try to handle as a slash command from cli.py
            import dashscope_agenticx.cli as cli_module

            # Route interactive commands (that may call input()) through
            # _handle_async_command, which runs them in a thread executor
            # with monkey-patched builtins.input -> _tui_input
            interactive_prefixes = ("/provider", "/setup")
            # /tts voice without args is interactive (numbered list + input())
            is_interactive_tts_voice = command.strip() == "/tts voice"
            # /dev is interactive only for "add" subcommands (which call
            # input())
            is_interactive_dev = (
                command.startswith("/dev") and " add" in command
            )
            # Expert commands may prompt (repo url, overwrite confirm) and
            # runs blocking git clone + file copies
            is_interactive_expert = (
                command.split(maxsplit=1)[0] in EXPERT_COMMAND_ALIASES
            )
            if (
                any(command.startswith(p) for p in interactive_prefixes)
                or is_interactive_dev
                or is_interactive_tts_voice
                or is_interactive_expert
            ):
                asyncio.create_task(self._handle_async_command(command))
                return

            # /camera and /tts block on hardware I/O (capture/record) or TTS
            # network + audio playback — run them in a worker thread.
            if command.startswith("/camera") or command.startswith("/tts"):
                asyncio.create_task(self._handle_device_command(command))
                return

            with _capture_console(self):
                try:
                    result = cli_module._handle_slash_command(
                        command,
                        self.agent,
                        self.config,
                    )
                except Exception as e:
                    self._write_output(
                        Text(f"Command failed: {e}", style="red"),
                    )
                    return

            if result is True:
                # Re-apply theme immediately after /theme set/change
                if command.startswith("/theme"):
                    self._apply_theme()
            elif result == "voice":
                asyncio.create_task(self._handle_voice_input())
            elif result == "compress":
                asyncio.create_task(self._async_compress())
            elif result == "summarize":
                asyncio.create_task(self._async_summarize())
            elif result == "async":
                asyncio.create_task(self._handle_async_command(command))
            elif result == "skill":
                asyncio.create_task(self._handle_skill_command(command))
            else:
                # Not a recognized slash command, send to agent
                self._agent_task = asyncio.create_task(
                    self.run_agent(command, display_input=""),
                )
        else:
            # Apply @ file/directory/image/audio expansion before sending
            # to agent
            import dashscope_agenticx.cli as cli_module
            from dashscope_agenticx.config import (
                is_audio_model,
                is_vision_model,
            )

            (
                expanded_text,
                images,
                audio_clips,
            ) = cli_module._expand_at_references(
                command,
            )
            if images and not is_vision_model(self.config.model):
                output = self.query_one("#output", RichLog)
                output.write(
                    Text(
                        f"Model {self.config.model} does not support "
                        f"images; {len(images)} image(s) ignored. "
                        "Switch to a vision model (e.g. qwen-vl-max) "
                        "and retry.",
                        style="yellow",
                    ),
                )
                images = []
            if audio_clips and not is_audio_model(self.config.model):
                output = self.query_one("#output", RichLog)
                output.write(
                    Text(
                        f"Model {self.config.model} does not support "
                        f"audio; {len(audio_clips)} clip(s) ignored. "
                        "Switch to an audio model (e.g. qwen-omni-turbo) "
                        "and retry.",
                        style="yellow",
                    ),
                )
                audio_clips = []
            agent_input = cli_module._to_multimodal_content(
                expanded_text,
                images,
                audio_clips,
            )
            self._agent_task = asyncio.create_task(
                self.run_agent(
                    agent_input,
                    display_input=command if _echo else "",
                ),
            )

    async def _run_shell_escape(self, shell_cmd: str) -> None:
        """Run a `!<cmd>` shell escape in a worker thread, then print
        output."""
        try:
            stdout, stderr, rc = await asyncio.to_thread(
                handle_shell_escape,
                shell_cmd,
            )
        except Exception as e:
            self._write_output(Text(f"Error: {e}", style="red"))
            return
        if stdout:
            self._write_output(Text(stdout.rstrip(), style="dim"))
        if stderr:
            self._write_output(Text(stderr.rstrip(), style="red"))
        if rc != 0:
            self._write_output(Text(f"(exit code {rc})", style="yellow"))
        elif not stdout and not stderr:
            self._write_output(Text("Done", style="dim"))

    async def _handle_device_command(self, command: str) -> None:
        """Handle /camera and /tts in a worker thread — they block on
        hardware I/O and audio playback, which must not freeze the loop."""
        import dashscope_agenticx.cli as cli_module

        output = self.query_one("#output", RichLog)
        with _capture_console(self, interactive=True):
            try:
                await asyncio.to_thread(
                    cli_module._handle_slash_command,
                    command,
                    self.agent,
                    self.config,
                )
            except Exception as e:
                output.write(Text(f"Error: {e}", style="red"))

        try:
            input_widget = self.query_one("#command-input", CommandInput)
            input_widget.focus()
        except Exception:
            pass

    async def _handle_voice_input(self) -> None:
        """Handle /v voice input command."""
        from dashscope_agenticx.ui.voice import voice_input

        output = self.query_one("#output", RichLog)

        # Create a cancel event
        cancel_event = threading.Event()

        # Store current recording state
        self._voice_cancel_event = cancel_event
        self._voice_recording = True

        output.write(
            Text("🎤 Recording... (auto-stops when done)", style="cyan"),
        )

        # Real-time display callback
        last_text = [""]

        def display_callback(text: str):
            if text != last_text[0]:
                # Style errors in red, diagnostics in cyan, normal ASR
                # output in dim
                if text.startswith(("[错误]", "[提示]", "[Error]", "[Info]")):
                    output.write(Text(text, style="red"))
                elif text.startswith(("[诊断]", "[警告]", "[Diag]", "[Warn]")):
                    output.write(Text(text, style="cyan"))
                else:
                    output.write(Text(f"  → {text}", style="dim"))
                last_text[0] = text

        try:
            text = await voice_input(
                self.config.tongyi_api_key,
                model=self.config.asr_model,
                display_callback=display_callback,
                cancel_event=cancel_event,
                silence_threshold=self.config.voice_silence_threshold,
                silence_duration=self.config.voice_silence_duration,
                max_recording_seconds=self.config.voice_max_seconds,
            )
            if text:
                output.write(Text(f"✓ Recognized: {text}", style="green"))
                # Put the transcribed text into the input area
                input_widget = self.query_one("#command-input", CommandInput)
                input_widget.text = text
                input_widget.focus()
            else:
                output.write(Text("No speech recognized", style="yellow"))
        except Exception as e:
            output.write(Text(f"Voice input failed: {e}", style="red"))
        finally:
            self._voice_recording = False
            self._voice_cancel_event = None
            input_widget = self.query_one("#command-input", CommandInput)
            input_widget.focus()

    async def _async_compress(self) -> None:
        """Compress conversation context using the shared CLI
        implementation."""
        import dashscope_agenticx.cli as cli_module

        with _capture_console(self):
            await cli_module._do_compress(self.agent)

    async def _async_summarize(self) -> None:
        """Summarize current task using the shared CLI implementation."""
        import dashscope_agenticx.cli as cli_module

        with _capture_console(self):
            await cli_module._do_summarize(self.agent)

    async def _handle_async_command(self, command: str) -> None:
        """Handle async slash commands (profile, prompt, data, kb, etc.)
        Redirects module consoles to RichLog, dispatches, then restores."""
        import dashscope_agenticx.cli as cli_module
        from dashscope_agenticx.cli.constants import EXPERT_COMMAND_ALIASES

        output = self.query_one("#output", RichLog)

        # /provider's wizard modules are normally imported lazily inside the
        # executor thread — after _capture_console's sys.modules scan — which
        # would leave their console pointed at stdout (invisible in the TUI).
        if command.startswith(("/provider", "/setup")):
            import dashscope_agenticx.cli.handlers_key  # noqa: F401
            import dashscope_agenticx.cli.handlers_provider  # noqa: F401

        # Same lazy-import console capture issue for Expert commands.
        expert_command = command.split(maxsplit=1)[0] in EXPERT_COMMAND_ALIASES
        if expert_command or command.startswith("/setup"):
            import dashscope_agenticx.cli.examples  # noqa: F401

        # Interactive commands call input() and must run in a thread executor
        # so they don't block the TUI event loop.
        interactive = (
            expert_command
            or command.startswith(("/provider", "/setup"))
            or (command.startswith("/dev") and " add" in command)
        )

        with _capture_console(self, interactive=interactive):
            try:
                if interactive:
                    loop = asyncio.get_event_loop()

                    def _run_interactive():
                        if command.startswith("/setup"):
                            asyncio.run(
                                cli_module._handle_setup(
                                    self.config,
                                    self.agent,
                                ),
                            )
                        elif command.startswith("/provider"):
                            from dashscope_agenticx.cli.handlers_provider import (  # noqa: E501
                                handle_provider_command,
                            )

                            handle_provider_command(
                                command,
                                self.agent,
                                self.config,
                            )
                        elif command.startswith("/dev"):
                            cli_module.handle_dev_command(command, self.config)
                        elif expert_command:
                            cli_module._handle_slash_command(
                                command,
                                self.agent,
                                self.config,
                            )

                    await loop.run_in_executor(None, _run_interactive)
                else:
                    await cli_module.dispatch_async_command(
                        command,
                        self.config,
                        self.agent,
                    )
            except Exception as e:
                output.write(Text(f"Error: {e}", style="red"))

        # Commands like /model switch the model inside the executor
        # thread, after the post-submission hint refresh already ran;
        # re-read agent.model_name so the status line is current.
        self._refresh_hint_label()

        try:
            input_widget = self.query_one("#command-input", CommandInput)
            input_widget.focus()
        except Exception:
            pass

    async def _handle_skill_command(self, command: str) -> None:
        """Handle /skill — list skills or render a skill prompt and feed to
        agent."""
        import dashscope_agenticx.cli as cli_module

        output = self.query_one("#output", RichLog)

        with _capture_console(self):
            try:
                rendered = await cli_module._handle_skill_continue(
                    command,
                    self.config,
                    self.agent,
                )
                if rendered:
                    self._agent_task = asyncio.create_task(
                        self.run_agent(rendered),
                    )
            except Exception as e:
                output.write(Text(f"Error: {e}", style="red"))

        try:
            input_widget = self.query_one("#command-input", CommandInput)
            input_widget.focus()
        except Exception:
            pass

    def _token_usage_snapshot(self) -> dict | None:
        """Cumulative executor token usage + API calls; None if unavailable."""
        try:
            stats = self.agent.executor.get_stats()
            usage = stats["token_usage"]
            return {
                **{
                    k: int(usage[k])
                    for k in (
                        "input_tokens",
                        "output_tokens",
                        "total_tokens",
                        "cached_tokens",
                    )
                },
                "api_calls": int(stats["api_calls"]),
                **(
                    {"usage_calls": int(stats["usage_calls"])}
                    if "usage_calls" in stats
                    else {}
                ),
            }
        except Exception:
            return None

    def _prompt_composition_snapshot(self) -> dict | None:
        """Cumulative executor prompt composition; None if unavailable."""
        try:
            stats = self.agent.executor.get_stats()
            comp = stats["prompt_composition"]
            return {
                **{k: int(v) for k, v in comp.items()},
                **{
                    k: int(v)
                    for k, v in stats.get("prompt_details", {}).items()
                },
            }
        except Exception:
            return None

    def _refresh_known_input_tokens(
        self,
        spinner: Spinner,
        snap: dict | None,
    ) -> None:
        if snap is None:
            return
        cur = self._token_usage_snapshot()
        if cur is not None:
            spinner.set_input_tokens(
                max(0, cur["input_tokens"] - snap["input_tokens"]),
                max(0, cur["cached_tokens"] - snap["cached_tokens"]),
            )
            spinner.set_api_calls(max(0, cur["api_calls"] - snap["api_calls"]))

    def _refresh_hint_label(self, arg_hint: str | None = None) -> None:
        """Input-box bottom line: current model in front, then the hint."""
        try:
            label = self.query_one("#hint-label", Static)
        except Exception:
            return
        model = getattr(self.agent, "model_name", "") or ""
        base = arg_hint or _INPUT_IDLE_HINT
        label.update(f"{model} · {base}" if model else base)

    def _turn_stats_line(
        self,
        turn_start: float,
        snap: dict | None,
        spinner: Spinner,
        prompt_snap: dict | None = None,
    ) -> str:
        return self._turn_statistics(
            turn_start,
            snap,
            spinner,
            prompt_snap,
        ).summary

    def _turn_statistics(
        self,
        turn_start: float,
        snap: dict | None,
        spinner: Spinner,
        prompt_snap: dict | None = None,
        activity: dict | None = None,
    ) -> TurnStatistics:
        elapsed = time.monotonic() - turn_start
        parts = [f"{elapsed:.1f}s"]
        cur = self._token_usage_snapshot() if snap is not None else None
        deltas = (
            {
                key: max(0, value - snap.get(key, 0))
                for key, value in cur.items()
            }
            if cur is not None and snap is not None
            else {}
        )
        has_usage = bool(deltas.get("usage_calls")) or bool(
            deltas.get("input_tokens") or deltas.get("output_tokens"),
        )
        partial = "usage_calls" in deltas and deltas[
            "usage_calls"
        ] < deltas.get("api_calls", 0)
        session_partial = (
            cur is not None
            and "usage_calls" in cur
            and cur["usage_calls"] < cur["api_calls"]
        )
        rows = [("Elapsed", f"{elapsed:.1f}s", "—")]

        def token_value(value: int, available: bool, incomplete: bool) -> str:
            if not available:
                return "—"
            return f"{value:,}" + (" (reported only)" if incomplete else "")

        def cache_rate(values: dict, available: bool) -> str:
            input_tokens = values.get("input_tokens", 0)
            if not available or not input_tokens:
                return "—"
            return (
                f"{100 * values.get('cached_tokens', 0) / input_tokens:.1f}%"
            )

        if has_usage:
            parts.append(
                f"↑{deltas['input_tokens']} ↓{deltas['output_tokens']} tokens"
                + (" (partial usage)" if partial else ""),
            )
        else:
            parts.append(
                f"↓~{spinner._out_tokens} tokens (est.; usage unavailable)",
            )
        session_usage = cur is not None and bool(
            cur.get("usage_calls")
            or cur["input_tokens"]
            or cur["output_tokens"],
        )
        for key, label in (
            ("input_tokens", "Input tokens"),
            ("output_tokens", "Output tokens"),
            ("total_tokens", "Total tokens (input + output)"),
            ("cached_tokens", "Cached tokens (included in input)"),
        ):
            turn_value = deltas.get(key, 0)
            session_value = (cur or {}).get(key, 0)
            if key == "total_tokens":
                turn_value = deltas.get("input_tokens", 0) + deltas.get(
                    "output_tokens",
                    0,
                )
                session_value = (cur or {}).get("input_tokens", 0) + (
                    cur or {}
                ).get("output_tokens", 0)
            rows.append(
                (
                    label,
                    token_value(turn_value, has_usage, partial),
                    token_value(session_value, session_usage, session_partial),
                ),
            )
        if not has_usage:
            rows.append(
                ("Output estimate", f"~{spinner._out_tokens:,} tokens", "—"),
            )
        rows.append(
            (
                "Cache rate (cached / input)",
                cache_rate(deltas, has_usage),
                cache_rate(cur or {}, session_usage),
            ),
        )
        if cur is not None:
            parts.append(f"{deltas['api_calls']} api")
        rows.append(
            (
                "API calls",
                str(deltas.get("api_calls", "—")),
                str((cur or {}).get("api_calls", "—")),
            ),
        )
        if cur is not None and "usage_calls" in deltas:
            rows.append(
                (
                    "API calls reporting usage",
                    str(deltas["usage_calls"]),
                    str(cur["usage_calls"]),
                ),
            )
        if prompt_snap is not None:
            cur_comp = self._prompt_composition_snapshot()
            if cur_comp is not None:
                prompt_roles = ("system", "user", "assistant", "tools")
                prompt_deltas = {
                    k: cur_comp[k] - prompt_snap.get(k, 0) for k in cur_comp
                }
                turn_chars = sum(
                    prompt_deltas.get(key, 0) for key in prompt_roles
                )
                session_chars = sum(
                    cur_comp.get(key, 0) for key in prompt_roles
                )
                rows.append(
                    (
                        "Prompt characters (all API requests)",
                        f"{turn_chars:,} chars",
                        f"{session_chars:,} chars",
                    ),
                )
                for key in ("system", "user", "assistant", "tools"):
                    rows.append(
                        (
                            f"  {key} characters",
                            f"{prompt_deltas.get(key, 0):,} chars",
                            f"{cur_comp.get(key, 0):,} chars",
                        ),
                    )
                for key, label in (
                    ("tool_result_chars", "    tool results characters"),
                    ("tool_call_args_chars", "    tool arguments characters"),
                    ("tools_schema_chars", "    tool schema characters"),
                ):
                    if key in cur_comp:
                        rows.append(
                            (
                                label,
                                f"{prompt_deltas.get(key, 0):,} chars",
                                f"{cur_comp[key]:,} chars",
                            ),
                        )
        activity = activity or self._turn_activity([])
        parts.append(f"{activity['tools']} tools")
        if activity["failures"]:
            parts.append(f"{activity['failures']} failed/blocked")
        for key, label in (
            ("tools", "Tool calls"),
            ("failures", "Failed / blocked tools"),
            ("mcp", "MCP calls"),
            ("skills", "Skills loaded"),
            ("subagents", "Sub-agent invocations"),
        ):
            rows.append(
                (label, str(activity[key]), str(self._session_activity[key])),
            )
        rows.append(
            (
                "Tool duration (sum; may overlap)",
                self._tool_duration_value(activity),
                self._tool_duration_value(self._session_activity),
            ),
        )
        rows.append(
            (
                "Tools reporting duration",
                str(activity["timed_tools"]),
                str(self._session_activity["timed_tools"]),
            ),
        )
        return TurnStatistics(
            self._turn_number,
            "📊 " + " · ".join(parts),
            rows,
            notes=[
                "Activity totals cover turns shown in this UI; "
                "tokens/API cover the executor session.",
                "Cached tokens are part of input, not additional tokens. "
                "Prompt sizes are characters, not tokens.",
            ],
        )

    def _turn_activity(self, blocks: list[ToolOutput]) -> dict:
        counts = {}
        for key, attribute in (
            ("tools", "turn_tool_calls"),
            ("mcp", "turn_mcp_calls"),
            ("skills", "turn_skills"),
            ("subagents", "turn_subagents"),
        ):
            value = getattr(self.agent, attribute, 0)
            counts[key] = value if isinstance(value, int) else 0
        counts["tools"] = max(counts["tools"], len(blocks))
        counts["failures"] = sum(block.success is False for block in blocks)
        counts["duration_ms"] = sum(block.duration_ms or 0 for block in blocks)
        counts["timed_tools"] = sum(
            block.duration_ms is not None for block in blocks
        )
        return counts

    @staticmethod
    def _tool_duration_value(activity: dict) -> str:
        if activity["tools"] and not activity["timed_tools"]:
            return "—"
        value = f"{activity['duration_ms'] / 1000:.2f}s"
        if activity["timed_tools"] < activity["tools"]:
            value += " (reported only)"
        return value

    async def _iter_turn_events(self, command: str):
        if getattr(type(self.agent), "run_events", None) is not None:
            async for event in self.agent.run_events(command):
                yield event
        else:
            async for chunk in self.agent.run_stream(command):
                yield AgentEvent(
                    AgentEventType.MESSAGE_DELTA,
                    {"text": chunk, "legacy": True},
                )

    async def run_agent(
        self,
        command: Any,
        *,
        display_input: str | None = None,
    ) -> None:
        """Run agent in a background worker — keeps UI responsive."""
        self._processing = True
        output = self.query_one("#output", OutputLog)
        self._turn_number += 1
        header = TurnHeader(
            self._turn_number,
            (
                display_input
                if display_input is not None
                else (
                    command
                    if isinstance(command, str)
                    else "[multimodal input]"
                )
            ),
            getattr(self.config, "model", "") or "",
            prompt=getattr(self.config, "_embedded_prompt_symbol", None)
            or "agenticx> ",
        )
        self._write_output(header)
        spinner = self.query_one("#spinner", Spinner)
        spinner.start()
        turn_start = time.monotonic()
        usage_snap = self._token_usage_snapshot()
        prompt_snap = self._prompt_composition_snapshot()
        spinner.max_out_tokens = getattr(
            getattr(self.agent, "provider", None),
            "default_max_tokens",
            None,
        )

        # Old JediTerm has no synchronized-output, so every follow-scrolling
        # flush tears the whole screen (flicker). Degrade to calm streaming:
        # content is still written but the viewport stays frozen (zero
        # repaints, spinner keeps animating), and we scroll to the bottom
        # once at turn end (a single frame).
        self._calm_streaming = _IS_JEDITERM
        full_chunks: list[str] = []
        segment_chunks: list[str] = []
        segment: MarkdownOutput | None = None
        segment_dirty = False
        pending_line_count = 0
        tool_arguments: dict[str, dict] = {}
        tool_blocks: dict[str, ToolOutput] = {}
        turn_tools: list[ToolOutput] = []
        loop = asyncio.get_running_loop()
        last_flush = loop.time()

        def _flush_text(*, end_segment: bool = False) -> None:
            nonlocal segment, segment_dirty, pending_line_count
            if segment_dirty:
                text = "".join(segment_chunks)
                if segment is None:
                    segment = MarkdownOutput(
                        text,
                        syntax_theme=_diff_syntax_theme(
                            light=self._is_light_mode(),
                        ),
                    )
                    self._write_output(segment)
                else:
                    segment.text = text
                    output.update_block(
                        segment,
                        scroll_end=False if self._calm_streaming else None,
                    )
                segment_dirty = False
                pending_line_count = 0
            if end_segment:
                segment = None
                segment_chunks.clear()

        try:
            async for event in self._iter_turn_events(command):
                payload = event.payload
                call_id = payload.get("tool_call_id", "")
                if event.type == AgentEventType.TOOL_STARTED:
                    _flush_text(end_segment=True)
                    tool_arguments[call_id] = payload.get("arguments", {})
                    continue
                if event.type == AgentEventType.TOOL_FINISHED:
                    _flush_text(end_segment=True)
                    block = self._write_tool_output(
                        payload.get("name", "tool"),
                        str(payload.get("result", "")),
                        arguments=tool_arguments.pop(call_id, None),
                        success=payload.get("success"),
                        duration_ms=payload.get("duration_ms"),
                        result_truncated=payload.get(
                            "result_truncated",
                            False,
                        ),
                    )
                    tool_blocks[call_id] = block
                    turn_tools.append(block)
                    continue
                if event.type != AgentEventType.MESSAGE_DELTA:
                    continue
                chunk = str(payload.get("text", ""))
                if not chunk:
                    continue

                # Detect tool trail lines with diffs (e.g. [write_file] →
                # ... --- diff ---)
                stripped = chunk.strip()
                tool_delta = payload.get("kind") == "tool"
                legacy_trail = (
                    payload.get("legacy")
                    and stripped.startswith("[")
                    and "] →" in stripped
                )
                if tool_delta or legacy_trail:
                    _flush_text(end_segment=True)
                    block = tool_blocks.get(call_id) if tool_delta else None
                    if block is not None:
                        result = stripped.partition("] →")[2].lstrip("\n ")
                        if block.result_truncated and len(result) > len(
                            block.result,
                        ):
                            output.update_tool_result(block, result)
                    else:
                        block = self._write_tool_trail(stripped)
                        if block is not None:
                            turn_tools.append(block)
                    continue

                spinner.note_output(chunk)
                segment_chunks.append(chunk)
                full_chunks.append(chunk)
                segment_dirty = True
                pending_line_count += chunk.count("\n")
                now = loop.time()
                # Every flush scrolls and repaints the whole visible area;
                # too small a window (e.g. 0.1s) still causes several
                # full-screen repaints per second on terminals without
                # synchronized-output (old PyCharm) and bottom flicker. The
                # line threshold only limits flush frequency for bursty
                # bulk output.
                if (
                    now - last_flush >= _STREAM_FLUSH_INTERVAL
                    or pending_line_count >= _STREAM_FLUSH_LINES
                    and now - last_flush >= 0.05
                ):
                    _flush_text()
                    last_flush = now
                    self._refresh_known_input_tokens(spinner, usage_snap)
                    spinner.set_tool_stats(
                        getattr(self.agent, "turn_tool_calls", 0),
                        getattr(self.agent, "turn_subagents", 0),
                        getattr(self.agent, "turn_mcp_calls", 0),
                        getattr(self.agent, "turn_skills", 0),
                    )

            _flush_text()
            full_output = "".join(full_chunks)
            header.status = "Finished"

            # Auto TTS: speak agent reply if enabled
            if (
                getattr(self.config, "tts_enabled", False)
                and full_output.strip()
            ):
                try:
                    self._start_tts_speak(full_output)
                except Exception:
                    pass
        except asyncio.CancelledError:
            _flush_text()
            header.status = "Interrupted"
            self._write_output(Text("\n(Interrupted)", style="yellow"))
        except UserAbortedTurn:
            _flush_text()
            header.status = "Aborted"
            self._write_output(Text("\nTurn aborted", style="dim"))
        except Exception as e:
            _flush_text()
            header.status = "Error"
            import traceback

            tb = traceback.format_exc()
            self._write_output(Text(f"\nError: {e}", style="red"))
            # Log full traceback to workspace for debugging
            try:
                from dashscope_agenticx.config import WORKSPACE_DIR

                err_log = WORKSPACE_DIR / "session" / "error.log"
                err_log.parent.mkdir(parents=True, exist_ok=True)
                err_log.write_text(f"Exception: {e}\n\n{tb}", encoding="utf-8")
            except Exception:
                pass
        finally:
            self.agent.last_output = "".join(full_chunks)
            output.update_block(
                header,
                scroll_end=False if self._calm_streaming else None,
            )
            activity = self._turn_activity(turn_tools)
            for key, value in activity.items():
                self._session_activity[key] += value
            statistics = self._turn_statistics(
                turn_start,
                usage_snap,
                spinner,
                prompt_snap,
                activity,
            )
            self._write_output(statistics)
            spinner.stop()
            self._refresh_hint_label()
            self._processing = False
            if self._calm_streaming:
                self._calm_streaming = False
                self._write_output("")
                if output._follow_output:
                    output.scroll_end(animate=False)
            else:
                self._write_output("")
            # Refocus input
            try:
                input_widget = self.query_one("#command-input", CommandInput)
                input_widget.focus()
            except Exception:
                pass
            # Drain queued commands (suppress echo — already shown when queued)
            if self._pending_commands:
                next_cmd = self._pending_commands.pop(0)
                self._handle_command(next_cmd, _echo=False)

    def _start_tts_speak(self, text: str) -> None:
        """Speak ``text`` in a daemon thread; widget writes are marshaled
        back to the UI thread via call_from_thread."""
        from dashscope_agenticx.ui.tts import is_available, speak_text

        ok, _err = is_available()
        if not ok:
            return

        def _bg_speak():
            try:
                err = speak_text(
                    api_key=self.config.tongyi_api_key,
                    text=text,
                    model=self.config.tts_model,
                    voice=self.config.tts_voice,
                    speech_rate=self.config.tts_speed,
                )
                if err:
                    self.call_from_thread(
                        self._write_output,
                        Text(f"TTS: {err}", style="yellow"),
                    )
            except Exception as e:
                try:
                    self.call_from_thread(
                        self._write_output,
                        Text(f"TTS error: {e}", style="yellow"),
                    )
                except Exception:
                    pass  # App already shutting down

        threading.Thread(target=_bg_speak, daemon=True).start()

    def _write_tool_output(
        self,
        name: str,
        result: str,
        *,
        arguments: dict | None = None,
        success: bool | None = None,
        duration_ms: int | None = None,
        result_truncated: bool = False,
    ) -> ToolOutput:
        light = self._is_light_mode()
        block = ToolOutput(
            name=name,
            result=result,
            arguments=arguments or {},
            success=success,
            duration_ms=duration_ms,
            expanded=success is False,
            result_truncated=result_truncated,
            arguments_provided=arguments is not None,
            style=_tool_trail_style(
                getattr(self.config, "theme", None),
                light=light,
            ),
            syntax_theme=_diff_syntax_theme(light=light),
        )
        self._write_output(block)
        return block

    def _write_tool_trail(self, line: str) -> ToolOutput | None:
        """Fold legacy tool trails when no structured runtime is available."""
        head, marker, result = line.partition("] →")
        if not marker:
            self._write_output(Text(line))
            return None
        result = result.lstrip("\n ")
        failed = re.match(
            r"^(?:Error|Failed|Blocked|Denied|Refused|Rejected|Cancelled)\b"
            r"|^Permission denied\b|^##\s+⛔\s+Not executed:",
            result,
            re.IGNORECASE,
        )
        return self._write_tool_output(
            head.lstrip("["),
            result,
            success=False if failed else None,
            result_truncated=result.endswith("..."),
        )


def run_tui(config, agent, input_history_path: Path | None = None):
    """Run the Textual TUI."""
    app = AgenticXApp(
        config=config,
        agent=agent,
        input_history_path=input_history_path,
    )
    # App captures the mouse so the wheel scrolls the output area only.
    # Without capture the terminal scrolls its own scrollback and the whole
    # screen (including the input line) appears to move. Set tui_mouse =
    # false for terminal-native selection; bypass capture with Alt/Option
    # drag to copy.
    app.run(mouse=getattr(config, "tui_mouse", True))
