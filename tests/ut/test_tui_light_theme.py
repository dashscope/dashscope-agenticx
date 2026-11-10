# -*- coding: utf-8 -*-
"""Light-theme fixes for the TUI.

1. ``_apply_theme`` switches the base Textual theme (``textual-light`` /
   ``textual-dark``) based on the configured background color's luminance,
   so base design tokens and the ANSI→truecolor filter match the background.
2. The light preset's ``muted`` color is darkened for WCAG AA contrast.
3. Tool-trail lines and diff highlighting pick light-aware styles when the
   active theme is light.
"""

from __future__ import annotations

import copy
from unittest.mock import MagicMock

import pytest

from dashscope_agenticx.cli.constants import STARTUP_LOGO, THEME_PRESETS
from dashscope_agenticx.ui.tui import (
    AgenticXApp,
    _app_theme_for_background,
    _diff_syntax_theme,
    _relative_luminance,
    _tool_trail_style,
)


def _make_app(theme: dict | None = None) -> AgenticXApp:
    config = MagicMock()
    config.theme = theme if theme is not None else {}
    config.enabled_capabilities = []
    config.provider = "test"
    config.model = "test-model"
    config.user_name = "tester"
    config.tts_enabled = False
    config._embedded_prompt_symbol = None
    agent = MagicMock()
    agent.session_path = None
    return AgenticXApp(config=config, agent=agent)


def _contrast(fg: str, bg: str) -> float:
    l1 = _relative_luminance(fg)
    l2 = _relative_luminance(bg)
    assert l1 is not None and l2 is not None
    hi, lo = max(l1, l2), min(l1, l2)
    return (hi + 0.05) / (lo + 0.05)


class TestRelativeLuminance:
    def test_white_and_black(self):
        assert _relative_luminance("#ffffff") == pytest.approx(1.0)
        assert _relative_luminance("#000000") == pytest.approx(0.0)

    def test_unparseable_returns_none(self):
        assert _relative_luminance("not-a-color") is None


@pytest.mark.parametrize("theme", ["dark", "light"])
async def test_startup_uses_shared_agenticx_logo(theme):
    from dashscope_agenticx.ui.tui import OutputLog

    app = _make_app(copy.deepcopy(THEME_PRESETS[theme]))
    async with app.run_test(size=(80, 30)) as pilot:
        await pilot.pause()
        output = app.query_one("#output", OutputLog)
        expected = STARTUP_LOGO.splitlines()
        rendered = [
            strip.text.rstrip() for strip in output.lines[: len(expected)]
        ]
        assert rendered == expected
        assert "_____|___|" not in "\n".join(expected)


class TestAppThemePicker:
    def test_white_background_picks_light(self):
        assert _app_theme_for_background("#ffffff") == "textual-light"

    def test_black_background_picks_dark(self):
        assert _app_theme_for_background("#000000") == "textual-dark"

    def test_missing_or_unparseable_defaults_dark(self):
        assert _app_theme_for_background(None) == "textual-dark"
        assert _app_theme_for_background("") == "textual-dark"
        assert _app_theme_for_background("bogus") == "textual-dark"

    def test_boundary_sanity(self):
        # Luminance just below 0.5 → dark, just above → light.
        assert _relative_luminance("#bbbbbb") < 0.5
        assert _app_theme_for_background("#bbbbbb") == "textual-dark"
        assert _relative_luminance("#bcbcbc") > 0.5
        assert _app_theme_for_background("#bcbcbc") == "textual-light"

    def test_all_presets_pick_expected_theme(self):
        expected = {"light": "textual-light"}
        for name, preset in THEME_PRESETS.items():
            want = expected.get(name, "textual-dark")
            assert (
                _app_theme_for_background(preset["background"]) == want
            ), name


class TestLightPreset:
    def test_muted_color(self):
        assert THEME_PRESETS["light"]["muted"] == "#595959"

    def test_muted_contrast_on_white_meets_aa(self):
        assert _contrast(THEME_PRESETS["light"]["muted"], "#ffffff") >= 4.5

    def test_text_contrast_on_white_meets_aa(self):
        assert _contrast(THEME_PRESETS["light"]["text"], "#ffffff") >= 4.5


class TestStylePickers:
    def test_tool_trail_style_light_uses_muted(self):
        theme = THEME_PRESETS["light"]
        assert _tool_trail_style(theme, light=True) == theme["muted"]

    def test_tool_trail_style_light_fallback_without_muted(self):
        assert _tool_trail_style({}, light=True) == "#595959"
        assert _tool_trail_style(None, light=True) == "#595959"

    def test_tool_trail_style_dark_keeps_dim_cyan(self):
        assert (
            _tool_trail_style(THEME_PRESETS["dark"], light=False) == "dim cyan"
        )
        assert _tool_trail_style(None, light=False) == "dim cyan"

    def test_tool_trail_light_style_contrast_on_white(self):
        style = _tool_trail_style(THEME_PRESETS["light"], light=True)
        assert _contrast(style, "#ffffff") >= 4.5

    def test_diff_syntax_theme_light_vs_dark(self):
        assert _diff_syntax_theme(light=True) == "ansi_light"
        assert _diff_syntax_theme(light=False) == "ansi_dark"

    def test_diff_syntax_themes_resolve_in_rich(self):
        from rich.syntax import ANSISyntaxTheme, Syntax

        for name in ("ansi_light", "ansi_dark"):
            assert isinstance(
                Syntax("x", "diff").get_theme(name),
                ANSISyntaxTheme,
            )


class TestApplyThemeSwitchesAppTheme:
    async def test_startup_light_background_sets_textual_light(self):
        app = _make_app(theme=copy.deepcopy(THEME_PRESETS["light"]))
        async with app.run_test(size=(80, 30)):
            assert app.theme == "textual-light"
            assert app.current_theme.dark is False
            assert app._is_light_mode() is True

    async def test_startup_dark_background_sets_textual_dark(self):
        app = _make_app(theme=copy.deepcopy(THEME_PRESETS["dark"]))
        async with app.run_test(size=(80, 30)):
            assert app.theme == "textual-dark"
            assert app.current_theme.dark is True
            assert app._is_light_mode() is False

    async def test_reapply_flips_theme_when_background_changes(self):
        app = _make_app(theme=copy.deepcopy(THEME_PRESETS["dark"]))
        async with app.run_test(size=(80, 30)):
            assert app.theme == "textual-dark"
            app.config.theme = copy.deepcopy(THEME_PRESETS["light"])
            app._apply_theme()
            assert app.theme == "textual-light"
            app.config.theme = copy.deepcopy(THEME_PRESETS["monokai"])
            app._apply_theme()
            assert app.theme == "textual-dark"

    async def test_theme_set_light_command_switches_app_theme(self):
        """Regression for the reported bug: /theme set light stayed on the
        dark base theme, leaving $foreground near-white on white."""
        app = _make_app(theme=copy.deepcopy(THEME_PRESETS["dark"]))
        async with app.run_test(size=(80, 30)):
            assert app.theme == "textual-dark"
            app._handle_command("/theme set light")
            assert app.config.theme == THEME_PRESETS["light"]
            assert app.theme == "textual-light"

    async def test_css_variables_follow_light_theme(self):
        """Base $foreground must come from textual-light, not textual-dark."""
        app = _make_app(theme=copy.deepcopy(THEME_PRESETS["light"]))
        async with app.run_test(size=(80, 30)):
            variables = app.get_css_variables()
            # textual-light foreground is dark; textual-dark's is #e0e0e0.
            assert _relative_luminance(variables["foreground"]) < 0.5
            # Config overrides still land on top of the light base theme.
            assert variables["surface"] == THEME_PRESETS["light"]["background"]
            assert variables["text-muted"] == THEME_PRESETS["light"]["muted"]
