# -*- coding: utf-8 -*-
"""S9: CommandInput password_mode must mask typed characters."""

import pytest
from textual.app import App

from dashscope_agenticx.ui.tui import CommandInput


class _PasswordApp(App):
    def __init__(self):
        super().__init__()
        self.submitted = []

    def compose(self):
        yield CommandInput()

    def on_command_input_submitted(self, event):
        self.submitted.append(event.text)


@pytest.fixture
async def app_and_pilot():
    app = _PasswordApp()
    async with app.run_test() as pilot:
        yield app, pilot


class TestPasswordMasking:
    async def test_typed_chars_are_masked(self, app_and_pilot):
        app, pilot = app_and_pilot
        widget = app.query_one(CommandInput)
        widget.password_mode = True
        widget.focus()
        await pilot.press("s", "e", "c", "r", "e", "t")
        assert widget.text == "******"
        assert widget._password_real == "secret"

    async def test_backspace_removes_masked_char(self, app_and_pilot):
        app, pilot = app_and_pilot
        widget = app.query_one(CommandInput)
        widget.password_mode = True
        widget.focus()
        await pilot.press("a", "b", "c", "backspace")
        assert widget.text == "**"
        assert widget._password_real == "ab"

    async def test_enter_submits_real_text_and_clears_mask(
        self,
        app_and_pilot,
    ):
        app, pilot = app_and_pilot
        widget = app.query_one(CommandInput)
        widget.password_mode = True
        widget.focus()
        await pilot.press("p", "w", "enter")
        await pilot.pause()
        assert app.submitted == ["pw"]
        assert widget.text == ""
        assert widget._password_real == ""
        assert widget.password_mode is False

    async def test_normal_mode_still_echoes_plaintext(self, app_and_pilot):
        app, pilot = app_and_pilot
        widget = app.query_one(CommandInput)
        widget.focus()
        await pilot.press("h", "i")
        assert widget.text == "hi"
