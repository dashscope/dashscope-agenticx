# -*- coding: utf-8 -*-
"""M9 + NEW-1/2/3: TUI scrolling and event-loop fixes.

M9a  — background TTS thread marshals widget writes via call_from_thread.
M9b  — !cmd shell escape and /camera|/tts run in worker threads.
M9c  — concurrent confirmations are serialized (no _confirm_future clobber).
M9d  — /quit and /q exit cleanly through app.exit().
NEW-1 — mouse text selection in the output area is content-anchored and
        survives scrolling.
NEW-2 — auto-scroll only follows when already at the bottom (no yanking).
NEW-3 — mouse wheel over the output area scrolls it without clicking first.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from textual import events
from textual.geometry import Offset
from textual.selection import Selection
from textual.widgets import RichLog

from dashscope_agenticx.events import AgentEvent, AgentEventType
from dashscope_agenticx.ui.tui import AgenticXApp, CommandInput, OutputLog


@pytest.fixture(autouse=True)
def disable_system_pasteboard(monkeypatch):
    monkeypatch.setattr(
        "dashscope_agenticx.utils.clipboard.get_macos_clipboard",
        lambda: None,
    )


class TestInputHistorySecurity:
    @pytest.mark.parametrize(
        "command, secret",
        [
            (
                "export DASHSCOPE_API_KEY=example-secret-value",
                "example-secret-value",
            ),
            ("Authorization: Bearer example-auth-value", "example-auth-value"),
            ("/provider tongyi example-provider-key", "example-provider-key"),
            ("/key custom-provider short", "short"),
            ("password=short", "short"),
        ],
    )
    def test_history_redacts_credentials_before_persisting(
        self,
        tmp_path,
        monkeypatch,
        command,
        secret,
    ):
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path / "workspace",
        )
        history_path = tmp_path / "history.txt"
        widget = CommandInput(history_path=history_path)
        widget.add_to_history("list files")
        widget.add_to_history(command)

        persisted = history_path.read_text(encoding="utf-8")
        assert secret not in persisted
        assert "list files" in persisted

    def test_loading_history_scrubs_current_and_legacy_credentials(
        self,
        tmp_path,
        monkeypatch,
    ):
        workspace = tmp_path / "workspace"
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            workspace,
        )
        legacy = workspace / "session" / "input-history"
        legacy.parent.mkdir(parents=True)
        legacy.write_text(
            "/provider tongyi legacy-provider-key\n",
            encoding="utf-8",
        )
        history_path = tmp_path / "history.txt"
        history_path.write_text(
            "export DASHSCOPE_API_KEY=current-secret-value\nlist files\n",
            encoding="utf-8",
        )

        widget = CommandInput(history_path=history_path)

        persisted = history_path.read_text(encoding="utf-8")
        assert "legacy-provider-key" not in persisted
        assert "current-secret-value" not in persisted
        assert "list files" in persisted
        assert widget._history == persisted.splitlines()
        assert not legacy.exists()


class _FakeMacOSClipboard:
    def __init__(self):
        self.text = "existing clipboard"
        self.revision = 0
        self.reads = 0
        self.writes = []

    def change_count(self):
        return self.revision

    def read_text(self):
        self.reads += 1
        return self.text

    def native_copy(self, text):
        self.text = text
        self.revision += 1

    def copy(self, text):
        self.writes.append(text)
        self.native_copy(text)
        return "pbcopy"


@pytest.fixture
def macos_clipboard(monkeypatch, disable_system_pasteboard):
    clipboard = _FakeMacOSClipboard()
    monkeypatch.setattr("dashscope_agenticx.ui.tui._IS_JEDITERM", True)
    monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", "darwin")
    monkeypatch.setattr(
        "dashscope_agenticx.utils.clipboard.get_macos_clipboard",
        lambda: clipboard,
    )
    monkeypatch.setattr(
        "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
        clipboard.copy,
    )
    return clipboard


def _make_app() -> AgenticXApp:
    config = MagicMock()
    config.theme = {}
    config.enabled_capabilities = []
    config.provider = "test"
    config.model = "test-model"
    config.user_name = "tester"
    config.tts_enabled = False
    config._embedded_prompt_symbol = None
    agent = MagicMock()
    agent.session_path = None
    return AgenticXApp(config=config, agent=agent)


async def _wait_for(cond, timeout: float = 3.0) -> bool:
    """Poll ``cond`` on the event loop until it holds or ``timeout`` elapses."""
    for _ in range(int(timeout / 0.05)):
        if cond():
            return True
        await asyncio.sleep(0.05)
    return False


def _wheel(app, x: int, y: int, *, down: bool, times: int = 1) -> None:
    """Forward mouse-wheel events at screen coordinates (as the driver would)."""
    cls = events.MouseScrollDown if down else events.MouseScrollUp
    for _ in range(times):
        app.screen._forward_event(
            cls(
                widget=None,
                x=x,
                y=y,
                delta_x=0,
                delta_y=0,
                button=0,
                shift=False,
                meta=False,
                ctrl=False,
                screen_x=x,
                screen_y=y,
                style=None,
            ),
        )


def _fill(output: RichLog, count: int = 100) -> None:
    for i in range(count):
        output.write(f"line {i:03d} filler content for scrolling tests")


def _mouse_move(app, x: int, y: int, *, delta_y: int) -> None:
    """Forward a MouseMove at screen coordinates with a real delta_y.

    pilot.hover always posts delta_y=0, which Textual's _check_auto_scroll
    dismisses as a horizontal move — the timer only arms on vertical motion.
    """
    app.screen._forward_event(
        events.MouseMove(
            widget=None,
            x=x,
            y=y,
            delta_x=0,
            delta_y=delta_y,
            button=1,
            shift=False,
            meta=False,
            ctrl=False,
            screen_x=x,
            screen_y=y,
            style=None,
        ),
    )


def _key(widget, key: str) -> None:
    """Feed a key through CommandInput._on_key (PyCharm sends wheel as arrows)."""
    widget._on_key(events.Key(key, None))


class TestStickyAutoScroll:
    """NEW-2: streaming writes must not fight the user's scroll position."""

    async def test_write_follows_when_at_bottom(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            assert output.scroll_offset.y >= output.max_scroll_y - 1
            output.write("tail-marker")
            await pilot.pause()
            assert output.scroll_offset.y >= output.max_scroll_y - 1
            assert any("tail-marker" in s.text for s in output.lines)

    async def test_write_does_not_yank_when_scrolled_up(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            x, y = output.region.x + 5, output.region.y + 5
            _wheel(app, x, y, down=False, times=20)
            await pilot.pause()
            pos = output.scroll_offset.y
            assert pos < output.max_scroll_y
            output.write("no-yank-marker")
            await pilot.pause()
            assert output.scroll_offset.y == pos

    async def test_submit_command_reengages_follow(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output)
            await pilot.pause()
            input_widget._queue_wheel_scroll("up")
            input_widget._flush_wheel_scroll()
            await pilot.pause()
            assert output._follow_output is False
            app._handle_command("/help")
            await pilot.pause()
            assert output.scroll_offset.y >= output.max_scroll_y - 1


class TestWheelScroll:
    """NEW-3: wheel over the output area scrolls it without clicking first."""

    async def test_wheel_scrolls_output_without_click(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output)
            await pilot.pause()
            assert app.focused is input_widget
            x, y = output.region.x + 5, output.region.y + 5
            before = output.scroll_offset.y
            _wheel(app, x, y, down=False, times=3)
            await pilot.pause()
            assert output.scroll_offset.y < before
            _wheel(app, x, y, down=True, times=3)
            await pilot.pause()
            assert output.scroll_offset.y == before
            # Focus never left the input (no click happened)
            assert app.focused is input_widget

    async def test_click_output_does_not_steal_input_focus(self):
        """Clicking the output (e.g. to select text) must keep focus on the
        input box. A focused OutputLog routes PyCharm's wheel-arrow keys to
        ScrollView's animated bindings (flicker) and swallows typing."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output)
            await pilot.pause()
            assert app.focused is input_widget
            await pilot.click(output)
            await pilot.pause()
            assert app.focused is input_widget
            assert not output.can_focus

    async def test_wheel_up_is_not_undone_by_streaming_write(self):
        """Wheel scrolling up must stick while new output streams in."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            x, y = output.region.x + 5, output.region.y + 5
            _wheel(app, x, y, down=False, times=3)
            await pilot.pause()
            pos = output.scroll_offset.y
            assert pos < output.max_scroll_y
            output.write("streamed while user reads above")
            await pilot.pause()
            assert output.scroll_offset.y == pos


class TestSelection:
    """NEW-1: mouse selection is content-anchored and survives scrolling."""

    async def _drag_select(self, app, pilot, output):
        await pilot.mouse_down(output, offset=Offset(4, 2))
        await pilot.hover(output, offset=Offset(10, 3))
        await pilot.mouse_up(output, offset=Offset(20, 4))
        await pilot.pause()

    def _scroll_to_filler(self, output, marker: str) -> int:
        """Scroll so the filler line containing ``marker`` is at the top."""
        idx = next(i for i, s in enumerate(output.lines) if marker in s.text)
        output.scroll_to(y=idx, animate=False)
        return idx

    async def test_drag_selection_extracts_text(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            self._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await self._drag_select(app, pilot, output)
            text = app.screen.get_selected_text()
            # Content lines 31..33 sit under the drag (1-line top padding);
            # the first line is clipped at the drag's x offset.
            assert "031 filler" in text
            assert "line 032" in text
            assert "line 033" in text

    async def test_mouse_up_does_not_touch_clipboard(self, monkeypatch):
        """Mouse-up after drag-select must NOT write the clipboard — that
        would clobber content the user already has. It only hints copying."""
        copied: dict[str, str] = {}

        def fake_copy(text):
            copied["text"] = text
            return "pbcopy"

        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            fake_copy,
        )
        app = _make_app()
        notes = []
        app.notify = lambda msg, **kw: notes.append(str(msg))
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            self._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await self._drag_select(app, pilot, output)
            assert app.screen.get_selected_text()
            assert not copied, "mouse-up must not write to the clipboard"
            assert notes == []
            toolbar = app.query_one("#selection-actions")
            assert toolbar.display
            assert toolbar.selection_text == app.screen.get_selected_text()

    async def test_plain_click_without_selection_does_not_crash(self):
        """A click with no drag posts TextSelected with no selection —
        the app must handle it without error."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output, 20)
            await pilot.pause()
            await pilot.mouse_down(output, offset=Offset(4, 2))
            await pilot.mouse_up(output, offset=Offset(4, 2))
            await pilot.pause()
            assert not getattr(app, "_clipboard", None)

    async def test_selection_highlight_reaches_rendered_segments(self):
        """The selection background must survive render_line (post-style);
        with pre-style the base surface bgcolor hides it and drags show
        no visible highlight (iTerm2 report)."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            idx = self._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await self._drag_select(app, pilot, output)
            style = app.screen.get_component_rich_style("screen--selection")
            assert style is not None and style.bgcolor is not None
            r, g, b = style.bgcolor.get_truecolor()
            bg = f"#{r:02x}{g:02x}{b:02x}"
            # Find a rendered row inside the selection and check its segments
            found = False
            for row in range(output.region.height):
                strip = output.render_line(row)
                if any(
                    seg.style and bg in str(seg.style).lower() for seg in strip
                ):
                    found = True
                    break
            assert found, f"no segment carries selection bg {bg}"

    async def test_selection_survives_wheel_scroll(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            idx = self._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await self._drag_select(app, pilot, output)
            text_before = app.screen.get_selected_text()
            assert text_before.strip()
            x, y = output.region.x + 5, output.region.y + 5
            _wheel(app, x, y, down=True, times=3)
            await pilot.pause()
            assert output.scroll_offset.y > idx
            assert app.screen.selections
            assert app.screen.get_selected_text() == text_before


class TestEdgeAutoScrollSelection:
    """NEW-4: a drag parked at the output's bottom edge must keep growing the
    selection as the auto-scroll timer scrolls — past one screen of content."""

    async def test_mouse_up_flushes_pending_pycharm_wheel_before_copy(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr("dashscope_agenticx.ui.tui._IS_JEDITERM", True)
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            _fill(output, 200)
            await pilot.pause()
            output.scroll_to(y=0, animate=False, immediate=True)
            await pilot.pause()
            await pilot.mouse_down(output, offset=Offset(4, 2))
            await pilot.pause()
            input_widget = app.query_one("#command-input", CommandInput)
            for index in range(60):
                input_widget._queue_wheel_scroll("down")
            x, y = output.region.x + 4, output.region.y + 2
            app.screen._forward_event(
                events.MouseUp(
                    widget=None,
                    x=x,
                    y=y,
                    delta_x=0,
                    delta_y=0,
                    button=1,
                    shift=False,
                    meta=False,
                    ctrl=False,
                    screen_x=x,
                    screen_y=y,
                    style=None,
                ),
            )
            await pilot.pause()
            assert input_widget._wheel_pending == 0
            selected = app.screen.get_selected_text()
            assert "line 030" in selected
            assert len(selected.splitlines()) > output.region.height

    @pytest.mark.parametrize("arrow_wheel", [False, True])
    @pytest.mark.parametrize("forwarded", [False, True])
    async def test_stationary_pointer_scroll_selection_survives_release_and_cmd_c(
        self,
        macos_clipboard,
        arrow_wheel,
        forwarded,
    ):
        copied = macos_clipboard.writes
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            _fill(output, 200)
            await pilot.pause()
            output.scroll_to(y=0, animate=False, immediate=True)
            await pilot.pause()
            await pilot.mouse_down(output, offset=Offset(4, 2))
            await pilot.pause()
            if arrow_wheel:
                input_widget = app.query_one("#command-input", CommandInput)
                for index in range(60):
                    input_widget._on_key(events.Key("down", None))
                input_widget._flush_wheel_scroll()
            else:
                _wheel(
                    app,
                    output.region.x + 4,
                    output.region.y + 2,
                    down=True,
                    times=20,
                )
            await pilot.pause()
            before_release = app.screen.get_selected_text()
            assert "line 030" in before_release
            assert len(before_release.splitlines()) > output.region.height
            await pilot.mouse_up(output, offset=Offset(4, 2))
            await pilot.pause()
            expected = app.screen.get_selected_text()
            assert expected == before_release
            if forwarded:
                await pilot.press("super+c")
            else:
                macos_clipboard.native_copy(output.native_copy_candidates()[0])
                await pilot.pause(0.25)
            await pilot.pause()
            assert copied and all(text == expected for text in copied)
            assert "line 030" in copied[0]
            assert len(copied[0].splitlines()) > output.region.height

    async def test_edge_auto_scroll_grows_selection_beyond_one_screen(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output, 200)
            await pilot.pause()
            output.scroll_to(y=0, animate=False)
            await pilot.pause()

            screen = app.screen
            await pilot.mouse_down(output, offset=Offset(4, 2))
            # Park the pointer on the last content row (region minus padding).
            edge_y = output.region.height - 2
            _mouse_move(
                app,
                output.region.x + 4,
                output.region.y + edge_y,
                delta_y=edge_y - 2,
            )
            await pilot.pause()

            state = screen._select_state
            assert state is not None and state.end is not None
            assert state.end.content_offset is not None
            end_y_before = state.end.content_offset.y
            scroll_before = output.scroll_offset.y
            # The edge hover must have armed the auto-scroll timer and
            # stashed the pointer position for AcliScreen.
            assert screen._auto_select_scroll_timer is not None
            assert screen._auto_scroll_pointer is not None

            await pilot.pause(1.0)

            # Assertions must run before mouse_up: its bundled MouseMove
            # re-derives the selection end at the scrolled position, which
            # would mask a broken auto-scroll path.
            assert output.scroll_offset.y > scroll_before
            state = screen._select_state
            assert state is not None and state.end is not None
            assert state.end.content_offset is not None
            assert state.end.content_offset.y > end_y_before
            assert state.start.content_offset is not None
            span = state.end.content_offset.y - state.start.content_offset.y
            assert span > output.region.height
            selected = screen.get_selected_text()
            assert len(selected.splitlines()) > output.region.height

            await pilot.mouse_up(output, offset=Offset(4, edge_y))

    async def test_multiscreen_selection_copied_line_for_line(
        self,
        monkeypatch,
    ):
        """Regression: a selection spanning multiple screens was truncated at
        the clipboard hop (terminal Cmd+C only sees the visible screen). The
        user-initiated Cmd+C must copy the full multi-screen selection
        exactly."""
        copied: dict[str, str] = {}

        def fake_copy(text):
            copied["text"] = text
            return "pbcopy"

        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            fake_copy,
        )
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output, 200)
            await pilot.pause()
            output.scroll_to(y=0, animate=False)
            await pilot.pause()

            await pilot.mouse_down(output, offset=Offset(4, 2))
            edge_y = output.region.height - 2
            _mouse_move(
                app,
                output.region.x + 4,
                output.region.y + edge_y,
                delta_y=edge_y - 2,
            )
            await pilot.pause(1.0)
            expected_before_up = app.screen.get_selected_text()
            assert len(expected_before_up.splitlines()) > output.region.height

            await pilot.mouse_up(output, offset=Offset(4, edge_y))
            await pilot.pause()
            assert not copied, "mouse-up must not copy"
            # mouse_up's bundled MouseMove may re-derive the selection end,
            # so snapshot after mouse-up, before the copy action clears it.
            expected = app.screen.get_selected_text()
            assert len(expected.splitlines()) > output.region.height
            app.action_copy_selection()
            assert copied["text"] == expected

    async def test_edge_drag_over_input_area_auto_scrolls(self):
        """iTerm2 regression: a drag slammed to the window bottom parks over
        the fixed input box, where Textual finds no scrollable ancestor and
        stops the timer — the screen-edge fallback must scroll #output and
        keep growing the selection past one screen."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output, 200)
            await pilot.pause()
            output.scroll_to(y=0, animate=False)
            await pilot.pause()

            screen = app.screen
            await pilot.mouse_down(output, offset=Offset(4, 2))
            bottom_y = screen.size.height - 1
            _mouse_move(app, output.region.x + 4, bottom_y, delta_y=5)
            await pilot.pause()
            assert screen._auto_select_scroll_timer is not None

            scroll_before = output.scroll_offset.y
            await pilot.pause(1.0)
            assert output.scroll_offset.y > scroll_before
            selected = screen.get_selected_text()
            assert len(selected.splitlines()) > output.region.height

            await pilot.mouse_up(output, offset=Offset(4, 2))

    async def test_edge_drag_over_scrollable_input_scrolls_output(self):
        """iTerm2 report: with a multiline draft the input box is scrollable,
        and parking the drag over the input's content rows makes Textual arm
        the auto-scroll for the *input* (or nothing) — #output freezes and
        the selection can't grow past one screen. The screen fallback must
        scroll #output instead."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output, 200)
            await pilot.pause()
            await pilot.pause()
            output.scroll_to(y=0, animate=False)
            input_widget.text = "\n".join(f"draft {i}" for i in range(10))
            await pilot.pause()
            assert input_widget.allow_vertical_scroll  # the hijack candidate

            screen = app.screen
            await pilot.mouse_down(output, offset=Offset(4, 2))
            # Park over the input's content rows — below the output region
            # but above the outermost screen rows (the old fallback's gap).
            park_y = input_widget.content_region.y + 1
            assert park_y >= output.region.bottom
            _mouse_move(app, output.region.x + 4, park_y, delta_y=20)
            await pilot.pause()
            assert screen._auto_select_scroll_timer is not None

            out_before = output.scroll_offset.y
            in_before = input_widget.scroll_y
            await pilot.pause(1.0)
            assert output.scroll_offset.y > out_before
            assert input_widget.scroll_y == in_before
            selected = screen.get_selected_text()
            assert selected is not None

            await pilot.mouse_up(output, offset=Offset(4, 2))

    async def test_edge_drag_over_single_line_input_steals_timer(self):
        """iTerm2 report: even with an empty one-line draft the CommandInput
        (a TextArea) is scrollable from Textual's viewpoint, so parking the
        drag over the prompt arms the auto-scroll timer on the *input* —
        whose scroll offset is clamped at 0 — and #output never moves. The
        fallback must take the timer over for #output."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output, 200)
            await pilot.pause()
            await pilot.pause()
            output.scroll_to(y=0, animate=False)
            assert input_widget.text == ""  # default single-line draft

            screen = app.screen
            await pilot.mouse_down(output, offset=Offset(4, 2))
            park_y = input_widget.content_region.y
            assert park_y >= output.region.bottom
            _mouse_move(app, output.region.x + 4, park_y, delta_y=20)
            await pilot.pause()
            assert screen._auto_scroll_target is output

            out_before = output.scroll_offset.y
            await pilot.pause(1.0)
            assert output.scroll_offset.y > out_before
            assert input_widget.scroll_y == 0
            selected = screen.get_selected_text()
            assert selected is not None
            assert len(selected.splitlines()) > output.region.height

            await pilot.mouse_up(output, offset=Offset(4, 2))

    async def test_edge_drag_at_screen_top_row_auto_scrolls_up(self):
        """iTerm2 upward drag: at screen row 0 the output's padding hit-tests
        to None, so Textual stops the auto-scroll timer — the fallback must
        re-arm it and keep growing the selection upward past one screen."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output, 200)
            # RichLog defers writes until its size is known, and every write
            # queues an after-refresh scroll_end. Wait for the flush, drain
            # the queued scrolls, and only then scroll — otherwise they land
            # after scroll_to and yank the view back to the bottom.
            assert await _wait_for(
                lambda: output.max_scroll_y >= 60
                and output.allow_vertical_scroll,
            )
            await pilot.pause()
            await pilot.pause()
            assert output.scroll_offset.y == output.max_scroll_y
            output.scroll_to(y=60, animate=False)
            assert output.scroll_offset.y == 60
            await pilot.pause()
            assert output.scroll_offset.y == 60

            screen = app.screen
            await pilot.mouse_down(output, offset=Offset(4, 10))
            # Set the selection end mid-screen, outside the edge auto-scroll
            # zones — no timer may arm here. (Dragging through the top edge
            # rows first would arm Textual's native auto-scroll, which then
            # drains scroll_y to 0 during pilot.pause's wait-for-quiescence
            # and leaves nothing for the fallback to re-arm.)
            _mouse_move(app, output.region.x + 4, 10, delta_y=-1)
            await pilot.pause()
            assert screen._auto_select_scroll_timer is None
            # ...then park at screen row 0 — hit-test returns None there, so
            # Textual stops the timer; the fallback must re-arm it at once.
            _mouse_move(app, output.region.x + 4, 0, delta_y=-1)
            assert screen._auto_select_scroll_timer is not None

            scroll_before = output.scroll_offset.y
            await pilot.pause(1.0)
            assert output.scroll_offset.y < scroll_before
            selected = screen.get_selected_text()
            assert len(selected.splitlines()) > output.region.height

            await pilot.mouse_up(output, offset=Offset(4, 10))

    async def test_stale_mouse_capture_does_not_break_selection(self):
        """iTerm2 report: a previous selection became impossible to replace
        and copy kept serving it. A MouseUp that never reaches the captor
        (released outside the window, modal interposed, ...) leaks
        app.mouse_captured; every later MouseDown then skips selection
        setup. A fresh MouseDown must drop the stale capture."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output)
            await pilot.pause()
            TestSelection()._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await TestSelection()._drag_select(app, pilot, output)
            assert app.screen.get_selected_text()

            # Mouse-down in the input box captures the mouse (TextArea);
            # simulate the lost MouseUp.
            await pilot.mouse_down(input_widget, offset=Offset(2, 1))
            await pilot.pause()
            assert app.mouse_captured is input_widget

            # The next drag on the output must still start a new selection.
            await TestSelection()._drag_select(app, pilot, output)
            assert app.mouse_captured is None
            text = app.screen.get_selected_text()
            assert text and "03" in text

    async def test_pointer_stash_reanchors_on_new_drag(self):
        """The auto-scroll pointer stash survives a finished drag; a wheel
        flush firing before the next drag's first MouseMove must extend
        toward the new drag's position, not the old parked one."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output, 200)
            await pilot.pause()
            output.scroll_to(y=0, animate=False)
            await pilot.pause()
            screen = app.screen

            # Drag #1 parks the pointer near the bottom of the output.
            await pilot.mouse_down(output, offset=Offset(4, 2))
            _mouse_move(
                app,
                output.region.x + 4,
                output.region.y + output.region.height - 3,
                delta_y=5,
            )
            await pilot.pause()
            parked = screen._auto_scroll_pointer
            assert parked is not None and parked.y > output.region.height // 2
            await pilot.mouse_up(output, offset=Offset(4, 2))
            await pilot.pause()
            assert screen._auto_scroll_pointer is None

            # Drag #2 starts at the top; a wheel flush before any MouseMove
            # must anchor at the drag-2 down point (top), not drag-1's park.
            await pilot.mouse_down(output, offset=Offset(4, 2))
            await pilot.pause()
            assert screen._auto_scroll_pointer is not None
            assert screen._auto_scroll_pointer.y <= output.region.y + 2
            input_widget._queue_wheel_scroll("down")
            input_widget._flush_wheel_scroll()
            await pilot.pause()
            state = screen._select_state
            assert state is not None and state.end is not None
            assert state.end.content_offset is not None
            # Content offsets include the current scroll position. The second
            # drag is correctly anchored near the visible top even if a queued
            # auto-scroll tick advanced the content before this assertion.
            visible_y = state.end.content_offset.y - output.scroll_offset.y
            assert 0 <= visible_y < output.region.height
            await pilot.mouse_up(output, offset=Offset(4, 2))


class TestArrowBurstWheel:
    """PyCharm: the wheel arrives as bursts of arrow keys (2-3 per notch,
    <10ms apart). Bursts must scroll the output batched (one repaint), must
    not touch history navigation, and must extend a mid-drag selection."""

    async def test_arrow_burst_scrolls_output_without_history_nav(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output)
            await pilot.pause()
            input_widget.add_to_history("cmd-one")
            before = output.scroll_offset.y
            _key(input_widget, "up")
            _key(input_widget, "up")
            _key(input_widget, "up")
            await pilot.pause(0.2)
            assert output.scroll_offset.y < before
            # First key of the burst must not recall history
            assert input_widget.text == ""

    async def test_single_arrow_still_recalls_history(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            input_widget = app.query_one("#command-input", CommandInput)
            input_widget.add_to_history("cmd-one")
            _key(input_widget, "up")
            assert await _wait_for(lambda: input_widget.text == "cmd-one")

    async def test_mid_drag_arrow_burst_extends_selection(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output, 200)
            await pilot.pause()
            output.scroll_to(y=0, animate=False)
            await pilot.pause()

            screen = app.screen
            await pilot.mouse_down(output, offset=Offset(4, 2))
            _mouse_move(
                app,
                output.region.x + 4,
                output.region.y + 6,
                delta_y=4,
            )
            await pilot.pause()
            state = screen._select_state
            assert state is not None and state.end is not None
            assert state.end.content_offset is not None
            end_y_before = state.end.content_offset.y
            scroll_before = output.scroll_offset.y

            _key(input_widget, "down")
            _key(input_widget, "down")
            _key(input_widget, "down")
            await pilot.pause(0.2)

            assert output.scroll_offset.y > scroll_before
            state = screen._select_state
            assert state is not None and state.end is not None
            assert state.end.content_offset is not None
            assert state.end.content_offset.y > end_y_before
            await pilot.mouse_up(output, offset=Offset(4, 6))


class TestWheelScrollResponsiveness:
    async def test_pycharm_wheel_uses_short_bounded_batches(self, monkeypatch):
        import dashscope_agenticx.ui.tui as tui_module

        monkeypatch.setattr(tui_module, "_IS_JEDITERM", True)
        app = _make_app()
        async with app.run_test(size=(80, 30)):
            input_widget = app.query_one("#command-input", CommandInput)
            timer = MagicMock()
            schedule = MagicMock(return_value=timer)
            monkeypatch.setattr(input_widget, "set_timer", schedule)
            for _ in range(12):
                input_widget._queue_wheel_scroll("down")
            schedule.assert_called_once()
            assert schedule.call_args.args[0] <= 0.04
            assert input_widget._wheel_pending == 12
            input_widget._flush_wheel_scroll()
            timer.stop.assert_called_once()

    async def test_consecutive_flushes_apply_every_line_before_refresh(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output)
            await pilot.pause()
            output.scroll_to(y=30, animate=False, immediate=True)
            for _ in range(3):
                input_widget._queue_wheel_scroll("down")
            input_widget._flush_wheel_scroll()
            assert output.scroll_offset.y == 33
            for _ in range(3):
                input_widget._queue_wheel_scroll("down")
            input_widget._flush_wheel_scroll()
            assert output.scroll_offset.y == 36
            await pilot.pause()
            assert output.scroll_offset.y == 36

    async def test_wheel_up_stops_follow_before_batch_timer_fires(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output)
            await pilot.pause()
            before = output.scroll_offset.y
            input_widget._queue_wheel_scroll("up")
            assert output._follow_output is False
            output.write("arrived before wheel flush")
            input_widget._flush_wheel_scroll()
            assert output.scroll_offset.y == before - 1
            await pilot.pause()
            assert output.scroll_offset.y == before - 1


class TestWheelGestureTail:
    """PyCharm trackpad jitter fix: a decelerating gesture's tail arrives as
    isolated arrows past the 20ms burst window. Those stragglers must be
    absorbed as scrolls (not history/completion), and a fresh wheel-up must
    suppress sticky follow so writes don't yank the view mid-gesture."""

    @staticmethod
    def _key_at(widget, key: str, fake_now: float) -> None:
        """Feed a key with the burst classifier's clock frozen at fake_now."""
        import dashscope_agenticx.ui.tui as tui_mod

        orig = time.monotonic
        tui_mod.time.monotonic = lambda: fake_now
        try:
            widget._on_key(events.Key(key, None))
        finally:
            tui_mod.time.monotonic = orig

    @staticmethod
    def _count_history(widget) -> dict[str, int]:
        calls = {"prev": 0, "next": 0}
        orig_prev = widget.action_history_prev
        orig_next = widget.action_history_next

        def prev():
            calls["prev"] += 1
            orig_prev()

        def next_():
            calls["next"] += 1
            orig_next()

        widget.action_history_prev = prev
        widget.action_history_next = next_
        return calls

    async def test_decelerating_up_gesture_never_touches_history(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            for i in range(5):
                input_widget.add_to_history(f"hist-cmd-{i}")
            _fill(output, 200)
            await pilot.pause()
            await pilot.pause()
            output.scroll_to(y=40, animate=False)
            await pilot.pause()
            calls = self._count_history(input_widget)

            # Burst gaps, then a decelerating tail past the 20ms window.
            t = time.monotonic()
            for g in [0.0, 0.005, 0.006, 0.014, 0.026, 0.09, 0.16]:
                t += g
                self._key_at(input_widget, "up", t)
                await asyncio.sleep(0.005)
            await pilot.pause(0.5)

            assert calls == {"prev": 0, "next": 0}
            assert input_widget.text == ""
            assert output.scroll_offset.y < 40  # gesture still scrolled

    async def test_decelerating_down_gesture_at_bottom_is_calm(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            input_widget.add_to_history("cmd-one")
            _fill(output, 200)
            await pilot.pause()
            await pilot.pause()
            assert output.scroll_offset.y == output.max_scroll_y
            calls = self._count_history(input_widget)

            t = time.monotonic()
            for g in [0.0, 0.005, 0.008, 0.026, 0.09, 0.16]:
                t += g
                self._key_at(input_widget, "down", t)
                await asyncio.sleep(0.005)
            await pilot.pause(0.5)

            assert calls == {"prev": 0, "next": 0}
            assert input_widget.text == ""
            assert output.scroll_offset.y == output.max_scroll_y

    async def test_single_arrow_after_gesture_window_still_recalls_history(
        self,
    ):
        """The tail window must not swallow real keypresses: a lone up
        arrow well after any wheel activity still recalls history."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            input_widget = app.query_one("#command-input", CommandInput)
            input_widget.add_to_history("cmd-one")
            _key(input_widget, "up")
            assert await _wait_for(lambda: input_widget.text == "cmd-one")

    async def test_wheel_up_near_bottom_sticks_indefinitely(self):
        """One line above the bottom used to sit inside the sticky band: a
        write landing past the 0.5s suppression window yanked the view back
        to the bottom — the "jitter at the bottom" report. Follow must stay
        off until the user scrolls back down, with no timeout."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output, 200)
            await pilot.pause()
            await pilot.pause()
            assert output.scroll_offset.y == output.max_scroll_y

            input_widget._queue_wheel_scroll("up")
            input_widget._flush_wheel_scroll()
            await pilot.pause()
            pos = output.scroll_offset.y
            assert pos == output.max_scroll_y - 1
            assert output._follow_output is False

            await pilot.pause(0.6)  # past the old 0.5s suppression window
            output.write("write landing long after the wheel-up")
            await pilot.pause()
            assert output.scroll_offset.y == pos  # still not yanked

    async def test_wheel_up_between_write_and_refresh_wins(self):
        """A write queues its follow scroll after the refresh; a wheel-up
        landing in that gap must cancel it, not be yanked back by it."""
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output, 200)
            await pilot.pause()
            await pilot.pause()
            assert output.scroll_offset.y == output.max_scroll_y

            output.write("streamed line")  # queues a follow scroll
            input_widget._queue_wheel_scroll("up")  # lands before refresh
            input_widget._flush_wheel_scroll()
            await pilot.pause()
            assert output._follow_output is False
            assert output.scroll_offset.y == output.max_scroll_y - 2

    async def test_wheel_down_flush_reenables_follow_only_at_bottom(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            input_widget = app.query_one("#command-input", CommandInput)
            _fill(output, 200)
            await pilot.pause()
            await pilot.pause()

            # Up ten lines, then down three: still off the bottom, so
            # follow must stay off.
            for _ in range(10):
                input_widget._queue_wheel_scroll("up")
            input_widget._flush_wheel_scroll()
            for _ in range(3):
                input_widget._queue_wheel_scroll("down")
            input_widget._flush_wheel_scroll()
            await pilot.pause()
            assert output._follow_output is False
            pos = output.scroll_offset.y
            output.write("mid-content write")
            await pilot.pause()
            assert output.scroll_offset.y == pos

            # Down to the bottom: follow re-engages.
            for _ in range(20):
                input_widget._queue_wheel_scroll("down")
            input_widget._flush_wheel_scroll()
            await pilot.pause()
            assert output._follow_output is True
            assert output.scroll_offset.y == output.max_scroll_y
            output.write("after wheel-down the follow is live again")
            await pilot.pause()
            assert output.scroll_offset.y == output.max_scroll_y

    async def test_mouse_wheel_drives_follow_state(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            x, y = output.region.x + 5, output.region.y + 5
            _wheel(app, x, y, down=False, times=1)
            await pilot.pause()
            assert output._follow_output is False
            _wheel(app, x, y, down=True, times=1)
            await pilot.pause()
            assert output._follow_output is True
            assert output.scroll_offset.y == output.max_scroll_y


class TestStreamingBatching:
    """NEW-5: rapid line streams are batched into few RichLog writes — one
    write per line repaints per line and flickers at the bottom edge."""

    async def test_rapid_lines_flush_in_batches(self, monkeypatch):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            writes: list[str] = []

            def collect(renderable) -> None:
                writes.append(
                    (
                        renderable.plain
                        if hasattr(renderable, "plain")
                        else str(renderable)
                    ),
                )

            output = app.query_one("#output", OutputLog)
            original_write = app._write_output
            original_update = output.update_block

            def write(renderable):
                collect(renderable)
                original_write(renderable)

            def update(block, **kwargs):
                collect(block)
                original_update(block, **kwargs)

            monkeypatch.setattr(app, "_write_output", write)
            monkeypatch.setattr(output, "update_block", update)

            async def stream(_command: str):
                for i in range(50):
                    yield f"stream-line {i:02d}\n"

            app.agent.run_stream = stream
            await app.run_agent("go")

            line_writes = [w for w in writes if "stream-line" in w]
            assert 0 < len(line_writes) < 50
            joined = "\n".join(writes)
            assert "stream-line 00" in joined
            assert "stream-line 49" in joined
            expected = "".join(f"stream-line {i:02d}\n" for i in range(50))
            assert app.agent.last_output == expected


class _EventStreamAgent:
    def __init__(self, agent, stream):
        self._agent = agent
        self._stream = stream

    def __getattr__(self, name):
        return getattr(self._agent, name)

    async def run_events(self, command):
        for event in self._stream:
            yield event


class TestCollapsibleToolOutput:
    @pytest.mark.parametrize("width", [40, 80])
    async def test_success_is_one_line_and_preserves_hidden_result(
        self,
        width,
    ):
        app = _make_app()
        async with app.run_test(size=(width, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            block = app._write_tool_output(
                "read_file",
                "First line\nhidden-marker\n[bold]literal[/bold]",
                arguments={"path": "目录/" + "配置" * 40 + ".py"},
                success=True,
                duration_ms=120,
            )
            await pilot.pause()
            assert block.line_count == 1
            assert not block.expanded
            assert "hidden-marker" not in output.lines[0].text
            output.toggle_tool(block.key)
            await pilot.pause()
            text = "\n".join(strip.text for strip in output.lines)
            assert "hidden-marker" in text
            assert "[bold]literal[/bold]" in text
            assert "Arguments" in text
            output.toggle_tool(block.key)
            await pilot.pause()
            assert len(output.lines) == 1

    async def test_click_toggles_header_without_stealing_input_focus(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            input_widget = app.query_one("#command-input", CommandInput)
            output.clear()
            block = app._write_tool_output(
                "read_file",
                "summary\nhidden-click-marker",
                success=True,
            )
            await pilot.pause()
            await pilot.click("#output", offset=(3, 1))
            await pilot.pause()
            assert block.expanded
            assert app.focused is input_widget
            await pilot.click("#output", offset=(3, 1))
            await pilot.pause()
            assert not block.expanded
            assert len(output.lines) == 1

    async def test_shortcut_toggles_all_and_keeps_input_draft(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            input_widget = app.query_one("#command-input", CommandInput)
            output.clear()
            blocks = [
                app._write_tool_output("read_file", f"head\nbody-{index}")
                for index in range(3)
            ]
            input_widget.text = "keep my draft"
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert all(block.expanded for block in blocks)
            assert input_widget.text == "keep my draft"
            await pilot.press("ctrl+o")
            await pilot.pause()
            assert all(not block.expanded for block in blocks)
            assert len(output.lines) == 3

    async def test_failures_start_expanded(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            block = app._write_tool_output(
                "run_command",
                "Error: exit code 1\ncritical-error-marker",
                success=False,
            )
            await pilot.pause()
            assert block.expanded
            assert "✗" in output.lines[0].text
            assert any(
                "critical-error-marker" in strip.text for strip in output.lines
            )

    async def test_expanded_diff_keeps_syntax_styles_and_copy_text(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            block = app._write_tool_output(
                "write_file",
                "Wrote config.py\n--- diff ---\n@@ -1 +1 @@\n-old\n+new",
                success=True,
            )
            output.toggle_tool(block.key)
            await pilot.pause()
            added_index = next(
                index
                for index, strip in enumerate(output.lines)
                if strip.text.startswith("+new")
            )
            assert any(
                segment.style.color for segment in output.lines[added_index]
            )
            assert output.get_selection(
                Selection(Offset(0, added_index), Offset(4, added_index)),
            ) == ("+new", "\n")

    async def test_toggle_above_view_keeps_content_anchor_while_writing(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            block = app._write_tool_output(
                "read_file",
                "head\n" + "detail\n" * 10,
            )
            _fill(output)
            await pilot.pause()
            output.scroll_to(y=40, animate=False, immediate=True)
            output._follow_output = False
            anchor = output.lines[int(output.scroll_offset.y)].text
            output.toggle_tool(block.key)
            await pilot.pause()
            assert output.lines[int(output.scroll_offset.y)].text == anchor
            scroll_y = output.scroll_offset.y
            output.write("streaming tail")
            await pilot.pause()
            assert output.scroll_offset.y == scroll_y
            output.toggle_tool(block.key)
            await pilot.pause()
            assert output.lines[int(output.scroll_offset.y)].text == anchor

    async def test_toggle_updates_later_offsets_without_losing_text(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            first = app._write_tool_output("read_file", "head\nfirst-body")
            output.write("middle message")
            second = app._write_tool_output("read_file", "head\nsecond-body")
            output.write("final message")
            for key in (first.key, second.key, first.key, second.key):
                output.toggle_tool(key)
                await pilot.pause()
            assert [strip.text for strip in output.lines][1::2] == [
                "middle message",
                "final message",
            ]
            assert len(output.lines) == 4

    async def test_evicted_and_cleared_blocks_cannot_restore_old_output(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            output.max_lines = 8
            block = app._write_tool_output("read_file", "head\nstale-marker")
            _fill(output, 20)
            await pilot.pause()
            assert not output._tool_blocks
            output.toggle_tool(block.key)
            assert len(output.lines) == 8
            new_block = app._write_tool_output("read_file", "head\nstale-two")
            output.clear()
            output.write("fresh output")
            output.toggle_tool(new_block.key)
            await pilot.pause()
            assert [strip.text for strip in output.lines] == ["fresh output"]

    async def test_expanding_past_line_limit_prunes_hidden_storage(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            output.max_lines = 8
            block = app._write_tool_output("read_file", "detail\n" * 30)
            output.toggle_tool(block.key)
            await pilot.pause()
            assert len(output.lines) == 8
            assert not output._tool_blocks
            output.write("new tail")
            await pilot.pause()
            assert output.lines[-1].text == "new tail"

    async def test_structured_tool_is_not_duplicated_in_reply_or_transcript(
        self,
    ):
        app = _make_app()
        stream = [
            AgentEvent(AgentEventType.MESSAGE_DELTA, {"text": "Working…"}),
            AgentEvent(
                AgentEventType.TOOL_STARTED,
                {
                    "tool_call_id": "one",
                    "arguments": {"path": "config.py"},
                },
            ),
            AgentEvent(
                AgentEventType.TOOL_FINISHED,
                {
                    "tool_call_id": "one",
                    "name": "read_file",
                    "result": "head\nhidden-event-marker",
                    "success": True,
                    "duration_ms": 50,
                },
            ),
            AgentEvent(
                AgentEventType.MESSAGE_DELTA,
                {
                    "kind": "tool",
                    "tool_call_id": "one",
                    "name": "read_file",
                    "text": "\n[read_file] →\nhead\nhidden-event-marker\n",
                },
            ),
            AgentEvent(AgentEventType.MESSAGE_DELTA, {"text": "\nDone."}),
        ]
        app.agent = _EventStreamAgent(app.agent, stream)
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            await app.run_agent("go")
            await pilot.pause()
            assert len(output._tool_blocks) == 1
            block = next(iter(output._tool_blocks.values()))
            assert block.arguments == {"path": "config.py"}
            assert app.agent.last_output == "Working…\nDone."
            assert not any(
                "hidden-event-marker" in strip.text for strip in output.lines
            )
            output.toggle_tool(block.key)
            await pilot.pause()
            assert (
                sum("read_file" in strip.text for strip in output.lines) == 1
            )
            assert any(
                "hidden-event-marker" in strip.text for strip in output.lines
            )

    async def test_assistant_text_resembling_a_tool_is_not_folded(self):
        app = _make_app()
        text = "[read_file] → this is an explanation, not a tool call\n"
        app.agent = _EventStreamAgent(
            app.agent,
            [AgentEvent(AgentEventType.MESSAGE_DELTA, {"text": text})],
        )
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            await app.run_agent("go")
            await pilot.pause()
            assert not output._tool_blocks
            assert app.agent.last_output == text

    async def test_full_diff_replaces_truncated_structured_preview(self):
        app = _make_app()
        result = (
            "Wrote file\n--- diff ---\n" + "+change\n" * 1200 + "+last-line"
        )
        stream = [
            AgentEvent(
                AgentEventType.TOOL_FINISHED,
                {
                    "tool_call_id": "write",
                    "name": "write_file",
                    "result": result[:8000],
                    "result_truncated": True,
                    "success": True,
                },
            ),
            AgentEvent(
                AgentEventType.MESSAGE_DELTA,
                {
                    "tool_call_id": "write",
                    "name": "write_file",
                    "kind": "tool",
                    "text": f"\n[write_file] →\n{result}\n",
                },
            ),
        ]
        app.agent = _EventStreamAgent(app.agent, stream)
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            await app.run_agent("go")
            await pilot.pause()
            block = next(iter(output._tool_blocks.values()))
            assert block.result == result
            assert not block.result_truncated
            assert not block.expanded
            output.toggle_tool(block.key)
            await pilot.pause()
            assert any("+last-line" in strip.text for strip in output.lines)

    @pytest.mark.parametrize(
        "error",
        ["Error: failure", "Blocked: hook", "Cancelled"],
    )
    async def test_legacy_tool_fallback_and_errors(self, error):
        app = _make_app()

        async def stream(command):
            yield "\n[read_file] →\nhead\nlegacy-hidden-marker\n"
            yield f"\n[run_command] →\n{error}\nlegacy-error-marker\n"
            yield "Final answer\n"

        app.agent.run_stream = stream
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            await app.run_agent("go")
            await pilot.pause()
            first, second = output._tool_blocks.values()
            assert not first.expanded
            assert second.expanded
            text = "\n".join(strip.text for strip in output.lines)
            assert "legacy-hidden-marker" not in text
            assert "legacy-error-marker" in text
            assert app.agent.last_output == "Final answer\n"


class TestConfirmSerialization:
    """M9c: a second confirm must queue, not clobber the first."""

    async def test_overlapping_confirms_both_resolve(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            tool_def = MagicMock()
            tool_def.name = "write_file"
            results: dict[str, str] = {}

            async def confirm(key: str, path: str) -> None:
                results[key] = await app._tui_confirm_callback(
                    tool_def,
                    {"path": path},
                    False,
                )

            task_a = asyncio.create_task(confirm("a", "a.txt"))
            await pilot.pause()
            task_b = asyncio.create_task(confirm("b", "b.txt"))
            await pilot.pause()

            # First prompt pending; second waits on the lock (no clobber).
            assert app._confirm_future is not None
            assert not app._confirm_future.done()
            assert not task_b.done()

            app.on_command_input_submitted(CommandInput.Submitted("y"))
            await asyncio.wait_for(task_a, timeout=2)
            assert results["a"] == "y"

            # Second prompt becomes pending after the first resolves.
            assert await _wait_for(
                lambda: app._confirm_future is not None
                and not app._confirm_future.done(),
            )
            app.on_command_input_submitted(CommandInput.Submitted("n"))
            await asyncio.wait_for(task_b, timeout=2)
            assert results["b"] == "n"
            assert app._confirm_future is None


class TestQuitCommands:
    """M9d: /quit and /q must exit cleanly (the CLI dispatcher raises
    SystemExit, which must never run inside the TUI event loop)."""

    @pytest.mark.parametrize("cmd", ["/exit", "/quit", "/q"])
    async def test_quit_command_exits_app(self, cmd):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            app._handle_command(cmd)
            await pilot.pause()
            assert app._exit is True


class TestOffLoopWork:
    """M9a/M9b: blocking work runs off the event loop; thread writes are
    marshaled back to the UI thread."""

    async def test_shell_escape_runs_and_writes_output(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            app._handle_command("!echo m9b_shell_marker")
            assert await _wait_for(
                lambda: any(
                    "m9b_shell_marker" in s.text for s in output.lines
                ),
            )

    async def test_device_command_runs_in_worker_thread(self, monkeypatch):
        import dashscope_agenticx.cli as cli_module

        started = threading.Event()
        release = threading.Event()
        called: dict[str, int] = {}

        def fake_handle(cmd, agent, config):
            called["thread"] = threading.get_ident()
            started.set()
            release.wait(timeout=5)
            return True

        monkeypatch.setattr(cli_module, "_handle_slash_command", fake_handle)

        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            app._handle_command("/camera capture")
            # The loop keeps turning while the (blocking) handler runs.
            assert await _wait_for(started.is_set, timeout=2)
            assert called["thread"] != threading.get_ident()
            release.set()
            await pilot.pause()

    async def test_tts_thread_error_is_marshaled_to_output(self, monkeypatch):
        import dashscope_agenticx.ui.tts as tts_module

        monkeypatch.setattr(tts_module, "is_available", lambda: (True, None))
        monkeypatch.setattr(
            tts_module,
            "speak_text",
            lambda **kwargs: "boom-marker",
        )

        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            app._start_tts_speak("hello")
            assert await _wait_for(
                lambda: any("boom-marker" in s.text for s in output.lines),
            )


class TestExpertUI:
    @pytest.mark.parametrize(
        "name",
        [
            "basic-chat",
            "dashscope-sdk-expert",
            "bailian-expert",
            None,
            "[red]spoof[/red]",
        ],
    )
    async def test_banner_shows_recorded_identity(
        self,
        tmp_path,
        monkeypatch,
        name,
    ):
        from dashscope_agenticx.config import Config

        workspace = tmp_path / ".agenticx"
        workspace.mkdir()
        if name is not None:
            (workspace / "expert.toml").write_text(f'name = "{name}"\n')
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            workspace,
        )
        agent = MagicMock()
        agent.session_path = None
        app = AgenticXApp(config=Config(session_persist=False), agent=agent)

        async with app.run_test(size=(130, 35)):
            output = app.query_one("#output", RichLog)
            visible = " ".join(strip.text for strip in output.lines)
            expected = (
                name if name and "spoof" not in name else "(not recorded)"
            )
            assert f"Expert: {expected}" in visible
            assert "spoof" not in visible

    @pytest.mark.parametrize(
        "command",
        ["/expert", "/experts", "/example", "/examples"],
    )
    @pytest.mark.parametrize(
        "arguments",
        [[], ["list"], ["download", "basic-chat"], ["restore"]],
    )
    async def test_expert_commands_run_in_worker_and_capture_output(
        self,
        monkeypatch,
        command,
        arguments,
    ):
        import dashscope_agenticx.cli.examples as expert_handler

        calls = []
        worker_threads = []

        def fake_handler(args):
            calls.append(args)
            worker_threads.append(threading.get_ident())
            if args and args[0] == "download":
                input("Continue Expert download? [Y/n]: ")
            expert_handler.console.print("Expert action finished")

        monkeypatch.setattr(
            expert_handler,
            "_handle_example_command",
            fake_handler,
        )
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            app._handle_command(" ".join([command, *arguments]))
            if arguments and arguments[0] == "download":
                assert await _wait_for(lambda: app._inline_input_active)
                assert any(
                    "Continue Expert download?" in strip.text
                    for strip in output.lines
                )
                await pilot.press("enter")
            assert await _wait_for(
                lambda: any(
                    "Expert action finished" in strip.text
                    for strip in output.lines
                ),
            )
            assert calls == [arguments]
            assert worker_threads[0] != threading.get_ident()
            assert not any("Error:" in strip.text for strip in output.lines)


class TestSetupWizard:
    @pytest.mark.parametrize(
        "add_provider,default_mode",
        [(False, False), (True, False), (False, True)],
    )
    async def test_setup_guidance_is_visible_in_tui(
        self,
        monkeypatch,
        tmp_path,
        add_provider,
        default_mode,
    ):
        import dashscope_agenticx.cli.handlers_provider as provider_handler
        import dashscope_agenticx.dev as dev
        import dashscope_agenticx.extensions as extensions
        from dashscope_agenticx.config import Config

        global_dir = tmp_path / "global"
        workspace_dir = tmp_path / "workspace" / ".agenticx"
        for name, path in (
            ("CONFIG_DIR", global_dir),
            ("CONFIG_FILE", global_dir / "config.toml"),
            ("WORKSPACE_DIR", workspace_dir),
            ("WORKSPACE_CONFIG_FILE", workspace_dir / "config.toml"),
        ):
            monkeypatch.setattr("dashscope_agenticx.config." + name, path)
        monkeypatch.setattr(
            extensions,
            "GLOBAL_EXTENSIONS_FILE",
            global_dir / "custom-extensions.toml",
        )
        monkeypatch.setattr(
            extensions,
            "WORKSPACE_EXTENSIONS_FILE",
            workspace_dir / "custom-extensions.toml",
        )
        monkeypatch.setattr(
            extensions,
            "_current",
            extensions.CustomExtensions(),
        )
        models = {"tongyi": ["qwen3.8-flash"]}
        monkeypatch.setattr(provider_handler, "PROVIDER_MODELS", models)
        monkeypatch.setattr(dev, "PROVIDER_MODELS", models)

        def reload_extensions():
            extensions._current = extensions.load_extensions()

        monkeypatch.setattr(dev, "_hot_reload", reload_extensions)
        monkeypatch.setattr(
            "dashscope_agenticx.tools.platform.register_platform_tools",
            lambda config, connect_mcp_fn=None: None,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.tools.platform.unregister_capability_tools",
            lambda key: None,
        )
        config = Config(tongyi_api_key="sk-test", session_persist=False)
        agent = MagicMock()
        agent.session_path = None
        app = AgenticXApp(config=config, agent=agent)

        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            app._handle_command("/setup")
            assert await _wait_for(lambda: app._inline_input_active)
            visible = lambda: " ".join(strip.text for strip in output.lines)
            assert "Experts (optional)" not in visible()
            answers = (
                ("", "", "")
                if default_mode
                else (
                    (
                        "tui-user",
                        "2",
                        "tui-provider",
                        "y",
                        "openai",
                        "https://local.example/v1",
                        "tui-model",
                        "n",
                        "0",
                        "0",
                        "0",
                    )
                    if add_provider
                    else ("tui-user", "2", "", "", "n", "", "0")
                )
            )
            for answer in answers:
                assert await _wait_for(lambda: app._inline_input_active)
                current_prompt = app._inline_input_future
                input_widget = app.query_one("#command-input", CommandInput)
                input_widget.text = answer
                await pilot.press("enter")
                assert await _wait_for(
                    lambda: app._inline_input_future is not current_prompt,
                )

            assert await _wait_for(
                lambda: "Experts (optional)" in visible(),
            ), visible()
            assert await _wait_for(
                lambda: "Setup complete" in visible(),
            ), visible()
            assert visible().index("Experts (optional)") < visible().index(
                "Setup complete",
            )
            assert "/expert download" not in visible()
            assert "Error:" not in visible()
            assert "Downloading expert" not in visible()
            username = "anonymous" if default_mode else "tui-user"
            assert (
                f'user_name = "{username}"'
                in (workspace_dir / "config.toml").read_text()
            )
            if default_mode:
                assert "Experts are selected separately" in visible()
                assert (
                    "basic-chat" in visible()
                    and "dashscope-sdk-expert" in visible()
                )
                assert "bailian-expert" in visible()
                assert "Expert: skipped" in visible()
                assert "Default configuration" in visible()
                assert "Provider: tongyi" in visible()
                assert "Key source" not in visible()
                assert "Available models" not in visible()
                assert config.enabled_capabilities == [
                    "bailian.mcp",
                    "bailian.cli",
                ]
            if add_provider:
                assert "Add Provider" in visible()
                assert "Ready now: /provider" not in visible()
                assert "Available models:" not in visible()
                assert config.provider == "tui-provider"
                assert config.model == "tui-model"
                assert (workspace_dir / "custom-extensions.toml").is_file()


class TestCmdCCopySelection:
    """Cmd+C (super+c) must copy the full Textual selection to the system
    clipboard and clear the highlight so the terminal cannot overwrite it."""

    async def test_super_c_copies_selection_and_clears_highlight(
        self,
        monkeypatch,
    ):
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", "darwin")
        copied: dict[str, str] = {}

        def fake_copy(text):
            copied["text"] = text
            return "pbcopy"

        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            fake_copy,
        )
        app = _make_app()
        notes = []
        app.notify = lambda msg, **kw: notes.append(str(msg))
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            TestSelection()._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await TestSelection()._drag_select(app, pilot, output)
            expected = app.screen.get_selected_text()
            assert expected.strip()
            # Selection highlight exists before Cmd+C
            assert app.screen.selections

            # Trigger super+c (Cmd+C)
            await pilot.press("super+c")
            await pilot.pause()

            # Full text was copied
            assert copied["text"] == expected
            assert notes == []
            # Selection highlight cleared to prevent terminal overwrite
            assert not app.screen.selections

    async def test_super_c_without_selection_does_nothing(self, monkeypatch):
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", "darwin")
        copied: dict[str, str] = {}
        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            lambda text: copied.update(text=text) or "pbcopy",
        )
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            # No selection made
            await pilot.press("super+c")
            await pilot.pause()
            assert "text" not in copied

    async def test_super_c_falls_back_to_osc52_without_clipboard_tool(
        self,
        monkeypatch,
    ):
        """No native clipboard tool → fall back to OSC 52 (App-level write)."""
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", "darwin")
        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            lambda text: None,
        )
        app = _make_app()
        osc: list[str] = []
        app.copy_to_clipboard = osc.append
        notes = []
        app.notify = lambda message, **kwargs: notes.append((message, kwargs))
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            TestSelection()._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await TestSelection()._drag_select(app, pilot, output)
            expected = app.screen.get_selected_text()
            assert expected.strip()
            await pilot.press("super+c")
            await pilot.pause()
            assert osc == [expected]
            assert len(notes) == 1
            assert "OSC 52" in notes[0][0]
            assert notes[0][1]["severity"] == "warning"

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    async def test_ctrl_c_with_selection_copies_instead_of_cancel(
        self,
        monkeypatch,
        platform,
    ):
        """Windows/Linux retain Ctrl+C selection copying."""
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", platform)
        copied: dict[str, str] = {}

        def fake_copy(text):
            copied["text"] = text
            return "pbcopy"

        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            fake_copy,
        )
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            TestSelection()._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await TestSelection()._drag_select(app, pilot, output)
            expected = app.screen.get_selected_text()
            assert expected.strip()
            app.action_smart_quit()  # Ctrl+C with selection → copy, not quit
            await pilot.pause()
            assert copied["text"] == expected
            assert not app.screen.selections
            assert app._ctrl_c_once is False

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    async def test_ctrl_c_keypress_with_selection_reaches_copy(
        self,
        monkeypatch,
        platform,
    ):
        """Real key dispatch: Screen has its own ctrl+c→copy_text binding
        (OSC 52 only — silently no-ops in PyCharm/JediTerm). Our priority
        binding must win so Ctrl+C copies the full selection via pbcopy."""
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", platform)
        copied: dict[str, str] = {}

        def fake_copy(text):
            copied["text"] = text
            return "pbcopy"

        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            fake_copy,
        )
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            TestSelection()._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await TestSelection()._drag_select(app, pilot, output)
            expected = app.screen.get_selected_text()
            assert expected.strip()
            await pilot.press("ctrl+c")
            await pilot.pause()
            assert copied.get("text") == expected

    @pytest.mark.parametrize("state", ["draft", "processing", "idle"])
    async def test_macos_ctrl_c_with_selection_cancels_without_copying(
        self,
        monkeypatch,
        state,
    ):
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", "darwin")
        native_copy = MagicMock(return_value="pbcopy")
        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            native_copy,
        )
        app = _make_app()
        app.copy_to_clipboard = MagicMock()
        app.exit = MagicMock()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            TestSelection()._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await TestSelection()._drag_select(app, pilot, output)
            assert app.screen.get_selected_text().strip()
            input_widget = app.query_one("#command-input", CommandInput)
            if state == "draft":
                input_widget.text = "draft input"
            elif state == "processing":
                app._processing = True
                app._agent_task = MagicMock()
                app._agent_task.done.return_value = False
            await pilot.press("ctrl+c")
            await pilot.pause()
            native_copy.assert_not_called()
            app.copy_to_clipboard.assert_not_called()
            app.exit.assert_not_called()
            if state == "draft":
                assert input_widget.text == ""
                assert app._ctrl_c_once is False
            elif state == "processing":
                app._agent_task.cancel.assert_called_once_with()
                assert app._ctrl_c_once is True
                app._processing = False
                app._agent_task = None
            else:
                assert app._ctrl_c_once is True
                await pilot.press("ctrl+c")
                await pilot.pause()
                app.exit.assert_called_once_with()
                native_copy.assert_not_called()
                app.copy_to_clipboard.assert_not_called()

    async def test_successful_copy_does_not_invoke_notify(
        self,
        monkeypatch,
    ):
        """Successful selection and copying are silent."""
        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            lambda text: "pbcopy",
        )
        app = _make_app()

        def boom(message, **kwargs):
            raise RuntimeError("toast must not be shown")

        app.notify = boom
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            TestSelection()._scroll_to_filler(output, "line 030")
            await pilot.pause()
            await TestSelection()._drag_select(app, pilot, output)
            assert app.screen.get_selected_text().strip()
            assert app.action_copy_selection()
            await pilot.pause()
            assert not app.screen.selections

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    async def test_paint_path_has_no_highlight_after_ctrl_c(
        self,
        monkeypatch,
        platform,
    ):
        """The styles-cache paint path (not just render_line) must stop
        emitting the selection background once the copy clears the
        selection — a stale cache would leave the box visible."""
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", platform)
        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            lambda text: "pbcopy",
        )
        app = _make_app()
        app.notify = lambda *args, **kwargs: None
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", RichLog)
            _fill(output)
            await pilot.pause()
            TestSelection()._scroll_to_filler(output, "line 030")
            await pilot.pause()

            def painted_bgs() -> set:
                bgs = set()
                for strip in output.render_lines(output.region):
                    for segment in strip:
                        style = segment.style
                        if style and style.bgcolor:
                            bgs.add(style.bgcolor.triplet.hex)
                return bgs

            base_bgs = painted_bgs()
            await TestSelection()._drag_select(app, pilot, output)
            assert app.screen.selections
            # Textual 8 composites the configured selection color with the
            # widget background, so the painted RGB need not equal the raw CSS
            # variable. Assert a distinct painted background appears instead.
            selected_bgs = painted_bgs()
            assert selected_bgs - base_bgs, "highlight missing pre-ctrl+c"
            await pilot.press("ctrl+c")
            await pilot.pause()
            await pilot.pause()
            assert not app.screen.selections
            assert not (
                painted_bgs() - base_bgs
            ), "highlight stuck post-ctrl+c"


class TestTurnStatsLine:
    """_turn_stats_line: per-turn totals vs per-call average vs session total."""

    def _app(self, token_usage: dict, api_calls: int):
        app = _make_app()
        app.agent.executor.get_stats.return_value = {
            "token_usage": token_usage,
            "api_calls": api_calls,
            "prompt_composition": {
                "system": 0,
                "user": 0,
                "assistant": 0,
                "tools": 0,
            },
        }
        app.agent.turn_tool_calls = 0
        app.agent.turn_subagents = 0
        app.agent.turn_mcp_calls = 0
        app.agent.turn_skills = 0
        return app

    @staticmethod
    def _snap(input_tokens=0, output_tokens=0, cached_tokens=0, api_calls=0):
        return {
            "input_tokens": input_tokens,
            "output_tokens": output_tokens,
            "total_tokens": input_tokens + output_tokens,
            "cached_tokens": cached_tokens,
            "api_calls": api_calls,
        }

    def _line(self, app, snap):
        spinner = SimpleNamespace(_out_tokens=0)
        return app._turn_stats_line(
            time.monotonic() - 1.0,
            snap,
            spinner,
            prompt_snap=None,
        )

    def test_first_turn_shows_plain_values_without_slash(self):
        cur = self._snap(22303, 574, cached_tokens=9472, api_calls=2)
        app = self._app(cur, api_calls=2)
        line = self._line(app, self._snap())
        assert "↑22303 ↓574 tokens" in line
        assert "cached" not in line
        assert "2 api" in line
        assert "/" not in line.split("tokens")[0]

    def test_second_turn_keeps_header_compact_and_totals_in_details(self):
        prev = self._snap(22303, 574, cached_tokens=9472, api_calls=2)
        cur = self._snap(34507, 981, cached_tokens=18944, api_calls=4)
        app = self._app(cur, api_calls=4)
        line = self._line(app, prev)
        assert "↑12204 ↓407 tokens" in line
        assert "34507" not in line
        assert "2 api" in line
        statistics = app._turn_statistics(
            time.monotonic() - 1.0,
            prev,
            SimpleNamespace(_out_tokens=0),
        )
        rows = {
            label: (turn, session) for label, turn, session in statistics.rows
        }
        assert rows["Input tokens"] == ("12,204", "34,507")
        assert rows["Output tokens"] == ("407", "981")
        assert rows["Cached tokens (included in input)"] == ("9,472", "18,944")
        assert rows["Total tokens (input + output)"] == ("12,611", "35,488")

    def test_single_api_turn_has_no_average(self):
        cur = self._snap(12120, 407, cached_tokens=9472, api_calls=1)
        app = self._app(cur, api_calls=1)
        line = self._line(app, self._snap())
        assert "1 api" in line
        assert "均值" not in line

    def test_missing_usage_is_not_presented_as_zero_tokens(self):
        cur = self._snap(api_calls=1)
        app = self._app(cur, api_calls=1)
        app.agent.executor.get_stats.return_value["usage_calls"] = 0
        statistics = app._turn_statistics(
            time.monotonic(),
            self._snap(),
            SimpleNamespace(_out_tokens=25),
        )
        assert "↓~25 tokens" in statistics.summary
        assert "usage unavailable" in statistics.summary
        rows = {
            label: (turn, session) for label, turn, session in statistics.rows
        }
        assert rows["Input tokens"] == ("—", "—")
        assert rows["Output tokens"] == ("—", "—")
        assert rows["Cache rate (cached / input)"] == ("—", "—")
        assert rows["API calls"] == ("1", "1")

    def test_partial_usage_and_prompt_character_units_are_explicit(self):
        cur = self._snap(100, 20, 40, api_calls=2)
        app = self._app(cur, api_calls=2)
        app.agent.executor.get_stats.return_value["usage_calls"] = 1
        prompt = {"system": 1000, "user": 300, "assistant": 200, "tools": 2500}
        app.agent.executor.get_stats.return_value[
            "prompt_composition"
        ] = prompt
        statistics = app._turn_statistics(
            time.monotonic(),
            self._snap(),
            SimpleNamespace(_out_tokens=30),
            {key: 0 for key in prompt},
        )
        assert "partial usage" in statistics.summary
        assert "prompt" not in statistics.summary
        rows = {
            label: (turn, session) for label, turn, session in statistics.rows
        }
        assert rows["Input tokens"] == (
            "100 (reported only)",
            "100 (reported only)",
        )
        assert rows["Total tokens (input + output)"] == (
            "120 (reported only)",
            "120 (reported only)",
        )
        assert rows["Prompt characters (all API requests)"] == (
            "4,000 chars",
            "4,000 chars",
        )
        assert rows["  tools characters"] == ("2,500 chars", "2,500 chars")

    def test_tool_prompt_details_do_not_inflate_total_characters(self):
        app = self._app(self._snap(100, 20), 1)
        app.agent.executor.get_stats.return_value["prompt_composition"][
            "tools"
        ] = 600
        app.agent.executor.get_stats.return_value["prompt_details"] = {
            "tool_result_chars": 300,
            "tool_call_args_chars": 50,
            "tools_schema_chars": 250,
        }
        statistics = app._turn_statistics(
            time.monotonic(),
            self._snap(),
            SimpleNamespace(_out_tokens=0),
            {"tools": 0},
        )
        rows = {
            label: (turn, session) for label, turn, session in statistics.rows
        }
        assert rows["Prompt characters (all API requests)"] == (
            "600 chars",
            "600 chars",
        )
        assert rows["    tool results characters"] == (
            "300 chars",
            "300 chars",
        )
        assert rows["    tool schema characters"] == ("250 chars", "250 chars")
        assert rows["    tool arguments characters"] == (
            "50 chars",
            "50 chars",
        )

    def test_observed_failures_and_overlapping_tool_durations(self):
        from dashscope_agenticx.ui.tool_output import ToolOutput

        app = self._app(self._snap(), 0)
        blocks = [
            ToolOutput("first", "ok", success=True, duration_ms=1000),
            ToolOutput("second", "Error", success=False, duration_ms=500),
        ]
        activity = app._turn_activity(blocks)
        assert activity["tools"] == 2 and activity["failures"] == 1
        app._session_activity = {
            key: value * 2 for key, value in activity.items()
        }
        statistics = app._turn_statistics(
            time.monotonic(),
            self._snap(),
            SimpleNamespace(_out_tokens=0),
            activity=activity,
        )
        rows = {
            label: (turn, session) for label, turn, session in statistics.rows
        }
        assert rows["Failed / blocked tools"] == ("1", "2")
        assert rows["Tool duration (sum; may overlap)"] == ("1.50s", "3.00s")

    async def test_refused_duplicate_is_displayed_and_counted_as_blocked(self):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            block = app._write_tool_trail(
                "[run_command] → ## ⛔ Not executed: this call already failed identically",
            )
            await pilot.pause()
            assert block.success is False and block.expanded
            assert app._turn_activity([block])["failures"] == 1

    def test_unknown_tool_duration_is_not_zero_seconds(self):
        from dashscope_agenticx.ui.tool_output import ToolOutput

        app = self._app(self._snap(), 0)
        unknown = ToolOutput("read_file", "ok", success=True)
        assert app._tool_duration_value(app._turn_activity([unknown])) == "—"
        known = ToolOutput("other", "ok", success=True, duration_ms=500)
        assert (
            app._tool_duration_value(app._turn_activity([unknown, known]))
            == "0.50s (reported only)"
        )


class TestTranscriptBlocks:
    async def test_markdown_turns_and_statistics_share_output(self):
        from dashscope_agenticx.ui.transcript import MarkdownOutput, TurnHeader

        app = _make_app()
        reply = "## 正文\n\n**重要**、`code`\n\n- 列表项\n\n| 类型 | 内容 |\n| --- | --- |\n| 文件 | 配置 |\n\n```python\nprint('hello')\n```"

        async def stream(command):
            for chunk in (reply[:25], reply[25:70], reply[70:]):
                yield chunk

        app.agent.run_stream = stream
        async with app.run_test(size=(80, 40)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            await app.run_agent(
                "expanded file contents",
                display_input="查看 @config.py",
            )
            await pilot.pause()
            blocks = list(output._output_blocks.values())
            headers = [
                block for block in blocks if isinstance(block, TurnHeader)
            ]
            markdown = [
                block for block in blocks if isinstance(block, MarkdownOutput)
            ]
            assert len(headers) == len(markdown) == 1
            assert headers[0].status == "Finished"
            assert headers[0].user_input == "查看 @config.py"
            assert markdown[0].text == app.agent.last_output == reply
            rendered = "\n".join(strip.text for strip in output.lines)
            assert rendered.count("agenticx> 查看 @config.py") == 1
            assert "**重要**" not in rendered
            assert "重要" in rendered and "print('hello')" in rendered
            assert "| --- |" not in rendered
            statistics = next(iter(output._statistics_blocks.values()))
            assert not statistics.expanded
            await pilot.press("ctrl+g")
            await pilot.pause()
            assert statistics.expanded
            assert any("This turn" in strip.text for strip in output.lines)
            await pilot.press("ctrl+g")
            await pilot.pause()
            assert not statistics.expanded

    async def test_click_statistics_preserves_draft(self):
        from dashscope_agenticx.ui.transcript import TurnStatistics

        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            statistics = TurnStatistics(1, "Stats", [("Metric", "10", "20")])
            app._write_output(statistics)
            draft = app.query_one("#command-input", CommandInput)
            draft.text = "keep draft"
            await pilot.pause()
            await pilot.click("#output", offset=(3, 1))
            await pilot.pause()
            assert statistics.expanded
            assert draft.text == "keep draft"
            assert app.focused is draft

    async def test_stream_reflow_waits_for_selection_to_clear(self):
        from dashscope_agenticx.ui.transcript import MarkdownOutput

        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            block = MarkdownOutput("**original** selection text")
            app._write_output(block)
            await pilot.pause()
            selected_line = next(
                index
                for index, strip in enumerate(output.lines)
                if "original" in strip.text
            )
            selection = Selection(
                Offset(0, selected_line),
                Offset(8, selected_line),
            )
            app.screen.selections = {output: selection}
            await pilot.pause()
            expected = app.screen.get_selected_text()
            assert expected == "original"
            block.text = (
                "# New heading\n\nOriginal source changed\n\n**tail-marker**"
            )
            output.update_block(block)
            await pilot.pause()
            assert app.screen.get_selected_text() == expected
            assert block.key in output._pending_updates
            assert not any(
                "tail-marker" in strip.text for strip in output.lines
            )
            app.screen.clear_selection()
            await pilot.pause()
            assert not output._pending_updates
            assert any("tail-marker" in strip.text for strip in output.lines)

    async def test_partially_pruned_markdown_keeps_updating(self):
        from dashscope_agenticx.ui.transcript import MarkdownOutput

        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            output.max_lines = 8
            source = "\n\n".join(
                f"paragraph {index:02d}" for index in range(15)
            )
            block = MarkdownOutput(source)
            app._write_output(block)
            await pilot.pause()
            assert block.key in output._output_blocks
            assert block.start_line < output._start_line
            block.text += "\n\nretained-tail-marker"
            output.update_block(block)
            await pilot.pause()
            assert len(output.lines) == 8
            assert any(
                "retained-tail-marker" in strip.text for strip in output.lines
            )
            assert not any(
                "paragraph 00" in strip.text for strip in output.lines
            )
            _fill(output, 10)
            await pilot.pause()
            assert block.key not in output._output_blocks
            block.text += "\n\nshould-not-resurrect"
            output.update_block(block)
            assert not any(
                "should-not-resurrect" in strip.text for strip in output.lines
            )

    @pytest.mark.parametrize("failure", [asyncio.CancelledError, RuntimeError])
    async def test_interrupted_reply_keeps_partial_markdown(self, failure):
        from dashscope_agenticx.ui.transcript import TurnHeader

        app = _make_app()

        async def stream(command):
            yield "**partial reply**"
            raise failure("interrupted")

        app.agent.run_stream = stream
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            await app.run_agent("test interruption")
            await pilot.pause()
            assert app.agent.last_output == "**partial reply**"
            assert any("partial reply" in strip.text for strip in output.lines)
            header = next(
                block
                for block in output._output_blocks.values()
                if isinstance(block, TurnHeader)
            )
            assert header.status == (
                "Interrupted" if failure is asyncio.CancelledError else "Error"
            )
            assert not app._processing

    async def test_pycharm_reflow_does_not_resume_follow_after_scroll_up(
        self,
        monkeypatch,
    ):
        from dashscope_agenticx.ui.transcript import MarkdownOutput

        monkeypatch.setattr("dashscope_agenticx.ui.tui._IS_JEDITERM", True)
        app = _make_app()
        app.agent.provider.default_max_tokens = None
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            _fill(output, 100)
            await pilot.pause()

            async def stream(command):
                yield "first segment\n\n" * 20
                await asyncio.sleep(0.9)
                yield "second segment"
                output._follow_output = False
                output.scroll_to(y=30, animate=False, immediate=True)
                await pilot.pause()
                yield "\n\nfinal segment"

            app.agent.run_stream = stream
            await app.run_agent("scroll test")
            await pilot.pause()
            assert output._follow_output is False
            assert output.scroll_offset.y == 30
            blocks = [
                block
                for block in output._output_blocks.values()
                if isinstance(block, MarkdownOutput)
            ]
            assert len(blocks) == 1
            assert "final segment" in blocks[0].text

    async def test_submitted_query_is_echoed_once_in_numbered_turn(self):
        app = _make_app()

        async def stream(command):
            yield "done"

        app.agent.run_stream = stream
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            app._handle_command("查看目录")
            await app._agent_task
            await pilot.pause()
            rendered = "\n".join(strip.text for strip in output.lines)
            assert rendered.count("agenticx> 查看目录") == 1
            assert "Turn 1" in rendered

    async def test_queued_query_does_not_echo_again_when_executed(self):
        app = _make_app()

        async def stream(command):
            yield "done"

        app.agent.run_stream = stream
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            app._processing = True
            app._handle_command("queued query")
            app._processing = False
            command = app._pending_commands.pop()
            app._handle_command(command, _echo=False)
            await app._agent_task
            await pilot.pause()
            rendered = "\n".join(strip.text for strip in output.lines)
            assert rendered.count("agenticx> queued query") == 1
            assert "Turn 1" in rendered


class TestSelectionActions:
    def _select_multiscreen_output(self, app):
        output = app.query_one("#output", OutputLog)
        output.clear()
        output._follow_output = False
        _fill(output, 120)
        output.scroll_to(y=35, animate=False, immediate=True)
        app.screen.selections = {
            output: Selection.from_offsets(Offset(0, 5), Offset(18, 55)),
        }
        return output

    @pytest.mark.parametrize("delay", [0.02, 0.25, 0.65])
    async def test_cmd_c_settles_full_selection_after_native_overwrite(
        self,
        macos_clipboard,
        delay,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            full_selection = "\n".join(
                f"selected line {index}" for index in range(60)
            )
            visible = "\n".join(full_selection.splitlines()[-20:])
            assert app._copy_text(
                full_selection,
                after_refresh=True,
                native_candidates=(visible,),
            )
            app.set_timer(delay, lambda: macos_clipboard.native_copy(visible))
            await pilot.pause(delay + 0.2)
            assert macos_clipboard.text == full_selection
            assert macos_clipboard.writes == [full_selection, full_selection]

    async def test_delayed_copy_cannot_overwrite_a_newer_copy(
        self,
        macos_clipboard,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            app._copy_text("old selection", after_refresh=True)
            assert await _wait_for(
                lambda: app._clipboard_settle_timer is not None,
            )
            app._copy_text("new selection")
            await pilot.pause(0.25)
            assert macos_clipboard.text == "new selection"
            assert macos_clipboard.writes == ["old selection", "new selection"]

    async def test_native_cmd_c_without_forwarding_copies_full_selection(
        self,
        macos_clipboard,
    ):
        app = _make_app()
        notes = []
        app.notify = lambda message, **kwargs: notes.append(str(message))
        async with app.run_test(size=(80, 30)) as pilot:
            output = self._select_multiscreen_output(app)
            await pilot.pause()
            full = app.screen.get_selected_text()
            app.on_text_selected(events.TextSelected())
            await pilot.pause(0.25)
            assert macos_clipboard.writes == []
            assert macos_clipboard.reads == 0
            visible = output.native_copy_candidates()[0]
            assert len(full.splitlines()) > len(visible.splitlines())
            macos_clipboard.native_copy(visible)
            await pilot.pause(0.25)
            assert macos_clipboard.text == full
            assert macos_clipboard.writes == [full]
            assert not app.screen.selections
            assert notes == []

    @pytest.mark.parametrize("forwarded", [False, True])
    async def test_rolling_selection_ending_in_empty_container_copies_full_text(
        self,
        macos_clipboard,
        forwarded,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            output._follow_output = False
            _fill(output, 120)
            output.scroll_to(y=0, animate=False, immediate=True)
            await pilot.pause()
            await pilot.mouse_down(output, offset=Offset(4, 2))
            _wheel(
                app,
                output.region.x + 4,
                output.region.y + 2,
                down=True,
                times=20,
            )
            await pilot.pause()
            await pilot.mouse_up("#input-container", offset=Offset(20, 0))
            await pilot.pause()
            assert output in app.screen.selections
            assert len(app.screen.selections) > 1
            full = app.screen.get_selected_text()
            assert len(full.splitlines()) > output.region.height
            visible = output.native_copy_candidates()[0]
            if forwarded:
                await pilot.press("super+c")
            macos_clipboard.native_copy(visible)
            await pilot.pause(0.25)
            assert macos_clipboard.text == full
            assert macos_clipboard.writes
            assert all(text == full for text in macos_clipboard.writes)
            assert not app.screen.selections

    @pytest.mark.parametrize("forwarded", [False, True])
    async def test_native_copy_includes_toast_and_cropped_screen_edges(
        self,
        macos_clipboard,
        forwarded,
    ):
        from dashscope_agenticx.utils.clipboard import matches_native_selection

        app = _make_app()
        async with app.run_test(size=(80, 30), notifications=True) as pilot:
            output = app.query_one("#output", OutputLog)
            output.clear()
            output._follow_output = False
            for index in range(120):
                output.write(f"原生复制 {index:03d} " + "中文换行测试 " * 15)
            output.scroll_to(y=0, animate=False, immediate=True)
            await pilot.pause()
            await pilot.mouse_down(output, offset=Offset(4, 2))
            _wheel(
                app,
                output.region.x + 4,
                output.region.y + 2,
                down=True,
                times=20,
            )
            await pilot.pause()
            await pilot.mouse_up("#input-container", offset=Offset(20, 0))
            app.notify("Selected native screen\n" + "overlay content\n" * 8)
            await pilot.pause()
            full = app.screen.get_selected_text()
            request = app._clipboard_request
            assert request is not None
            frame = app._native_screen_candidate(request.screen_selection)
            assert "Selected" in frame
            assert not matches_native_selection(
                frame,
                output.native_copy_candidates(),
            )
            assert len(full.splitlines()) > len(frame.splitlines())
            assert macos_clipboard.writes == []
            if forwarded:
                await pilot.press("super+c")
            macos_clipboard.native_copy(frame)
            await pilot.pause(0.25)
            assert macos_clipboard.text == full
            assert all(text == full for text in macos_clipboard.writes)

    @pytest.mark.parametrize("forwarded", [False, True])
    async def test_native_copy_watcher_preserves_unrelated_clipboard(
        self,
        macos_clipboard,
        forwarded,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            self._select_multiscreen_output(app)
            await pilot.pause()
            app.on_text_selected(events.TextSelected())
            if forwarded:
                await pilot.press("super+c")
            initial_writes = macos_clipboard.writes.copy()
            macos_clipboard.native_copy("unrelated clipboard from another app")
            await pilot.pause(0.25)
            assert (
                macos_clipboard.text == "unrelated clipboard from another app"
            )
            assert macos_clipboard.writes == initial_writes
            assert app._clipboard_settle_timer is None

    async def test_native_copy_watcher_rejects_selected_text_outside_output(
        self,
        macos_clipboard,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = self._select_multiscreen_output(app)
            spinner = app.query_one("#spinner")
            spinner.update("other selected text")
            app.screen.selections = {
                **app.screen.selections,
                spinner: Selection(None, None),
            }
            await pilot.pause()
            app.on_text_selected(events.TextSelected())
            assert app._clipboard_request is None
            visible = output.native_copy_candidates()[0]
            macos_clipboard.native_copy(visible)
            await pilot.pause(0.25)
            assert macos_clipboard.text == visible
            assert macos_clipboard.writes == []

    async def test_native_copy_watcher_stops_when_other_widget_selection_changes(
        self,
        macos_clipboard,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = self._select_multiscreen_output(app)
            container = app.query_one("#input-container")
            app.screen.selections = {
                **app.screen.selections,
                container: Selection(None, None),
            }
            await pilot.pause()
            app.on_text_selected(events.TextSelected())
            assert app._clipboard_request is not None
            visible = output.native_copy_candidates()[0]
            app.screen.selections = {
                **app.screen.selections,
                container: Selection.from_offsets(Offset(0, 0), Offset(1, 0)),
            }
            macos_clipboard.native_copy(visible)
            await pilot.pause(0.25)
            assert macos_clipboard.text == visible
            assert macos_clipboard.writes == []
            assert app._clipboard_request is None

    @pytest.mark.parametrize("reason", ["clear", "focus", "new_selection"])
    async def test_native_copy_watcher_stops_when_selection_is_not_active(
        self,
        macos_clipboard,
        reason,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = self._select_multiscreen_output(app)
            await pilot.pause()
            app.on_text_selected(events.TextSelected())
            visible = output.native_copy_candidates()[0]
            if reason == "clear":
                app.screen.clear_selection()
            elif reason == "focus":
                app.app_focus = False
            else:
                app.screen.selections = {
                    output: Selection.from_offsets(
                        Offset(0, 70),
                        Offset(10, 75),
                    ),
                }
            macos_clipboard.native_copy(visible)
            await pilot.pause(0.25)
            assert macos_clipboard.text == visible
            assert macos_clipboard.writes == []
            assert macos_clipboard.reads == 0
            assert app._clipboard_settle_timer is None

    async def test_native_copy_read_race_does_not_overwrite_new_content(
        self,
        macos_clipboard,
        monkeypatch,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = self._select_multiscreen_output(app)
            await pilot.pause()
            app.on_text_selected(events.TextSelected())
            visible = output.native_copy_candidates()[0]
            macos_clipboard.native_copy(visible)

            def racing_read():
                macos_clipboard.native_copy("new unrelated clipboard")
                return visible

            monkeypatch.setattr(macos_clipboard, "read_text", racing_read)
            await pilot.pause(0.15)
            assert macos_clipboard.text == "new unrelated clipboard"
            assert macos_clipboard.writes == []

    async def test_macos_ctrl_c_does_not_trigger_native_copy_watch(
        self,
        macos_clipboard,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            self._select_multiscreen_output(app)
            await pilot.pause()
            app.on_text_selected(events.TextSelected())
            app.query_one("#command-input", CommandInput).text = "draft"
            await pilot.press("ctrl+c")
            await pilot.pause(0.25)
            assert app.query_one("#command-input", CommandInput).text == ""
            assert macos_clipboard.text == "existing clipboard"
            assert macos_clipboard.writes == []
            assert macos_clipboard.reads == 0

    async def test_cmd_c_copy_is_not_rewritten_when_clipboard_is_unchanged(
        self,
        macos_clipboard,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            app._copy_text("full selection", after_refresh=True)
            assert await _wait_for(lambda: macos_clipboard.reads == 1)
            assert macos_clipboard.writes == ["full selection"]
            assert macos_clipboard.reads == 1

    async def test_native_overwrite_before_revision_snapshot_is_repaired(
        self,
        macos_clipboard,
        monkeypatch,
    ):
        def overwritten_copy(text):
            macos_clipboard.copy(text)
            macos_clipboard.native_copy("visible fragment")
            return "pbcopy"

        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            overwritten_copy,
        )
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            app._copy_text(
                "full selection",
                after_refresh=True,
                native_candidates=("visible fragment",),
            )
            monkeypatch.setattr(
                "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
                macos_clipboard.copy,
            )
            await pilot.pause(0.25)
            assert macos_clipboard.text == "full selection"
            assert macos_clipboard.writes == [
                "full selection",
                "full selection",
            ]

    @pytest.mark.parametrize("copied", [False, True])
    async def test_cmd_c_then_immediate_blur_repairs_only_native_copy(
        self,
        macos_clipboard,
        copied,
    ):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            output = self._select_multiscreen_output(app)
            await pilot.pause()
            full = app.screen.get_selected_text()
            app.on_text_selected(events.TextSelected())
            if copied:
                macos_clipboard.native_copy(output.native_copy_candidates()[0])
            app.post_message(events.AppBlur())
            await pilot.pause(0.2)
            assert app.app_focus is False
            assert app._clipboard_settle_timer is None
            if copied:
                assert macos_clipboard.text == full
                assert macos_clipboard.writes == [full]
            else:
                assert macos_clipboard.text == "existing clipboard"
                assert macos_clipboard.writes == []
                assert macos_clipboard.reads == 0

    async def test_cmd_c_settle_window_is_bounded(self, macos_clipboard):
        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            app._copy_text(
                "full selection",
                after_refresh=True,
                native_candidates=("part",),
            )
            assert await _wait_for(
                lambda: app._clipboard_settle_timer is not None,
            )
            app._clipboard_request.expires = time.monotonic() - 1
            await pilot.pause(0.2)
            assert app._clipboard_settle_timer is None
            macos_clipboard.native_copy("part")
            await pilot.pause(0.2)
            assert macos_clipboard.text == "part"
            assert macos_clipboard.writes == ["full selection"]

    async def test_failed_native_repair_stops_retrying(
        self,
        macos_clipboard,
        monkeypatch,
    ):
        app = _make_app()
        notes = []
        app.notify = lambda message, **kwargs: notes.append(str(message))
        async with app.run_test(size=(80, 30)) as pilot:
            app._copy_text(
                "full selection",
                after_refresh=True,
                native_candidates=("visible fragment",),
            )
            monkeypatch.setattr(
                "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
                lambda text: None,
            )
            macos_clipboard.native_copy("visible fragment")
            await pilot.pause(0.25)
            assert macos_clipboard.text == "visible fragment"
            assert app._clipboard_settle_timer is None
            assert sum("retry failed" in note for note in notes) == 1

    async def test_click_copy_uses_full_snapshot_without_keyboard(
        self,
        monkeypatch,
    ):
        from dashscope_agenticx.ui.selection_actions import SelectionActions

        copied = []
        monkeypatch.setattr(
            "dashscope_agenticx.utils.clipboard.copy_to_clipboard",
            lambda text: copied.append(text) or "pbcopy",
        )
        app = _make_app()
        notes = []
        app.notify = lambda message, **kwargs: notes.append(str(message))
        async with app.run_test(size=(80, 30)) as pilot:
            output = app.query_one("#output", OutputLog)
            _fill(output)
            await pilot.pause()
            await TestSelection()._drag_select(app, pilot, output)
            expected = app.screen.get_selected_text()
            toolbar = app.query_one("#selection-actions", SelectionActions)
            await pilot.pause()
            assert toolbar.display and toolbar.selection_text == expected
            assert not copied
            app.screen.clear_selection()
            await pilot.pause()
            await pilot.click(
                "#selection-actions",
                offset=(
                    len(f"Selected {len(expected.splitlines())} lines · ") + 1,
                    0,
                ),
            )
            await pilot.pause()
            assert copied == [expected]
            assert not toolbar.display
            assert notes == []

    async def test_quote_snapshot_preserves_existing_draft(self):
        from dashscope_agenticx.ui.selection_actions import SelectionActions

        app = _make_app()
        async with app.run_test(size=(80, 30)) as pilot:
            input_widget = app.query_one("#command-input", CommandInput)
            input_widget.text = "existing draft"
            app.on_selection_actions_requested(
                SelectionActions.Requested("quote", "first\nsecond"),
            )
            await pilot.pause()
            assert input_widget.text == "existing draft\n> first\n> second\n"

    def test_pycharm_hint_only_offers_cmd_c_and_copy(self, monkeypatch):
        monkeypatch.setattr("dashscope_agenticx.ui.tui._IS_JEDITERM", True)
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", "darwin")
        hint = _make_app()._copy_shortcut_hint()
        assert (
            "Cmd+C" in hint
            and "Ctrl+C" not in hint
            and "if intercepted, click Copy" in hint
        )

    def test_macos_hint_keeps_fallback(self, monkeypatch):
        monkeypatch.setattr("dashscope_agenticx.ui.tui._IS_JEDITERM", False)
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", "darwin")
        hint = _make_app()._copy_shortcut_hint()
        assert (
            hint.startswith("Cmd+C")
            and "Ctrl+C" not in hint
            and "click Copy" in hint
        )

    @pytest.mark.parametrize("platform", ["win32", "linux"])
    def test_non_macos_hint_keeps_ctrl_c(self, monkeypatch, platform):
        monkeypatch.setattr("dashscope_agenticx.ui.tui.sys.platform", platform)
        assert _make_app()._copy_shortcut_hint() == "Ctrl+C to copy"
