# -*- coding: utf-8 -*-
from __future__ import annotations

import copy
import types

from dashscope_agenticx.cli.constants import THEME_PRESETS
from dashscope_agenticx.cli.handlers_config import _handle_theme_command


def _config():
    return types.SimpleNamespace(theme={}, save_workspace=lambda: None)


def test_theme_set_then_override_does_not_mutate_presets():
    """Regression: /theme set aliased the global THEME_PRESETS dict, so a
    later /theme <key> <color> override mutated the preset itself."""
    snapshot = copy.deepcopy(THEME_PRESETS)
    config = _config()

    _handle_theme_command("/theme set dark", config)
    assert config.theme == THEME_PRESETS["dark"]
    assert config.theme is not THEME_PRESETS["dark"]

    _handle_theme_command("/theme background #000000", config)
    assert config.theme["background"] == "#000000"
    assert THEME_PRESETS == snapshot


def test_theme_shorthand_then_override_does_not_mutate_presets():
    snapshot = copy.deepcopy(THEME_PRESETS)
    config = _config()

    _handle_theme_command("/theme light", config)
    _handle_theme_command("/theme accent #123456", config)
    assert config.theme["accent"] == "#123456"
    assert THEME_PRESETS == snapshot
