# -*- coding: utf-8 -*-
"""The startup banner surfaces the embedded host's guide link."""

# pylint: disable=protected-access,unused-argument

import pytest

from dashscope_agenticx.cli.constants import STARTUP_LOGO
from dashscope_agenticx.cli.startup import _print_banner
from dashscope_agenticx.config import Config

GUIDE = "https://help.aliyun.com/en/model-studio/dashscope-sdk-expert"


@pytest.mark.parametrize(
    "name",
    ["basic-chat", "dashscope-sdk-expert", "bailian-expert"],
)
def test_banner_shows_selected_expert(tmp_path, monkeypatch, capsys, name):
    workspace = tmp_path / ".agenticx"
    workspace.mkdir()
    (workspace / "expert.toml").write_text(f'name = "{name}"\n')
    monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", workspace)

    _print_banner(Config())

    output = capsys.readouterr().out
    assert f"Expert: {name}" in output


@pytest.mark.parametrize("contents", [None, 'name = "[red]spoof[/red]"'])
def test_banner_does_not_guess_expert(tmp_path, monkeypatch, capsys, contents):
    workspace = tmp_path / ".agenticx"
    workspace.mkdir()
    if contents is not None:
        (workspace / "expert.toml").write_text(contents)
    monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", workspace)

    _print_banner(Config())

    output = capsys.readouterr().out
    assert "Expert: (not recorded)" in output
    assert "spoof" not in output


def test_banner_uses_agenticx_logo(capsys):
    _print_banner()
    out = capsys.readouterr().out
    assert out.startswith(STARTUP_LOGO + "\n")
    assert STARTUP_LOGO.splitlines()[0].endswith("__  __")
    assert STARTUP_LOGO.splitlines()[4].endswith("/_/\\_\\")
    assert "AgenticX" in out
    assert "_____|___|" not in out


def test_banner_shows_guide_url(capsys):
    config = Config()
    config._embedded_guide_url = GUIDE
    _print_banner(config)
    out = capsys.readouterr().out
    assert "Guide:" in out
    assert GUIDE in out


def test_banner_omits_guide_url_by_default(capsys):
    _print_banner(Config())
    assert "Guide:" not in capsys.readouterr().out


def test_embedded_run_stores_guide_url(monkeypatch):
    from dashscope_agenticx.ui import embedded

    captured = {}
    monkeypatch.setattr(
        Config,
        "load",
        classmethod(lambda cls, **kw: Config()),
    )

    async def fake_oneshot(config, prompt, system_prompt):
        captured["config"] = config

    monkeypatch.setattr(embedded, "_run_oneshot_embedded", fake_oneshot)
    embedded.run(command="hi", guide_url=GUIDE)
    assert captured["config"]._embedded_guide_url == GUIDE


def test_embedded_run_defaults_to_no_guide_url(monkeypatch):
    from dashscope_agenticx.ui import embedded

    captured = {}
    monkeypatch.setattr(
        Config,
        "load",
        classmethod(lambda cls, **kw: Config()),
    )

    async def fake_oneshot(config, prompt, system_prompt):
        captured["config"] = config

    monkeypatch.setattr(embedded, "_run_oneshot_embedded", fake_oneshot)
    embedded.run(command="hi")
    assert getattr(captured["config"], "_embedded_guide_url", "") == ""
