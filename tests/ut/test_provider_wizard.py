# -*- coding: utf-8 -*-
"""Regressions for the provider/key wizards when config.provider is stale.

A provider persisted from a directory that had a custom-extensions.toml
(e.g. `zhipu`) is not loadable elsewhere. Both wizards used to dead-end on
it: startup demanded an API key for a provider it could not build, and
/provider offered that same unbuildable name as the Enter default, so a
bare Enter returned "Unknown provider: zhipu; cancelled".
"""

from io import StringIO
from unittest.mock import MagicMock, patch

import pytest
from rich.console import Console

import dashscope_agenticx.cli.handlers_key as handlers_key
import dashscope_agenticx.cli.handlers_provider as handlers_provider
from dashscope_agenticx.config import Config


def _config(provider: str, model: str = "gml-image") -> Config:
    cfg = Config()
    cfg.provider = provider
    cfg.model = model
    cfg.protocol = "openai"
    cfg.tongyi_api_key = ""
    return cfg


@pytest.fixture(autouse=True)
def no_config_writes(monkeypatch):
    """Keep the wizards off the real config files.

    A successful /provider run ends in save_global() + save_workspace(),
    which would rewrite ~/.agenticx/config.toml and the repo's .agenticx/config.toml.
    """
    monkeypatch.setattr(Config, "save_global", lambda self: None)
    monkeypatch.setattr(Config, "save_workspace", lambda self: None)


@pytest.fixture
def captured(monkeypatch):
    """Route both wizards' rich console into a string buffer."""
    buf = StringIO()
    console = Console(file=buf, width=200, force_terminal=False)
    monkeypatch.setattr(handlers_key, "console", console)
    monkeypatch.setattr(handlers_provider, "console", console)
    return buf


class TestStartupKeyWizard:
    def test_skips_a_provider_that_cannot_be_built(self, captured):
        cfg = _config("zhipu")
        with (
            patch.object(
                handlers_key,
                "build_profiles_from_config",
                return_value=[],
            ),
            patch("builtins.input") as mock_input,
        ):
            assert handlers_key.ensure_provider_key(cfg, MagicMock()) is True

        mock_input.assert_not_called()
        out = captured.getvalue()
        assert "'zhipu' is not available here" in out
        assert "run /provider" in out

    def test_does_not_invent_an_env_var_nothing_reads(self, captured):
        cfg = _config("zhipu")
        with (
            patch.object(
                handlers_key,
                "build_profiles_from_config",
                return_value=[],
            ),
            patch("builtins.input", return_value="3"),
        ):
            handlers_key.ensure_provider_key(cfg, MagicMock())

        assert "ZHIPU_API_KEY" not in captured.getvalue()

    def test_still_prompts_for_a_known_provider(self, captured):
        cfg = _config("tongyi", "qwen3.8-max")
        with (
            patch.object(
                handlers_key,
                "build_profiles_from_config",
                return_value=[],
            ),
            patch("builtins.input", return_value="3") as mock_input,
        ):
            assert handlers_key.ensure_provider_key(cfg, MagicMock()) is True

        mock_input.assert_called_once()
        out = captured.getvalue()
        assert "No API Key detected for tongyi" in out
        assert "DASHSCOPE_API_KEY" in out


class TestProviderWizard:
    def test_setup_advanced_endpoint_and_protocol_are_validated(self, captured, monkeypatch):
        from dashscope_agenticx.cli.wizard import WizardContext, WizardResult

        config = Config(provider="tongyi", model="qwen3.8-max")
        answers = iter(["anthropic", "", "y", "not-an-endpoint", "https://custom.example/v1", "invalid", "anthropic"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
        monkeypatch.setattr(handlers_provider, "PROVIDER_MODELS", {"tongyi": ["qwen3.8-max"], "anthropic": ["claude-test"]})
        monkeypatch.setattr(handlers_provider, "_wizard_key_step", lambda *args, **kwargs: WizardResult("completed"))

        result = handlers_provider._provider_wizard(MagicMock(), config, context=WizardContext(setup=True))

        assert result.handled
        assert (config.provider, config.model, config.protocol, config.base_url) == (
            "anthropic", "claude-test", "anthropic", "https://custom.example/v1",
        )
        assert "absolute HTTP(S)" in captured.getvalue()
        assert "Choose openai, anthropic or dashscope" in captured.getvalue()

    def test_cancellation_restores_standalone_config(self, captured, monkeypatch):
        config = Config(provider="tongyi", model="qwen3.8-max", base_url="https://old.example/v1")
        answers = iter(["anthropic"])

        def read_input(prompt):
            try:
                return next(answers)
            except StopIteration:
                raise EOFError

        monkeypatch.setattr("builtins.input", read_input)
        monkeypatch.setattr(handlers_provider, "PROVIDER_MODELS", {"tongyi": ["qwen3.8-max"], "anthropic": ["claude-test"]})
        saved = []
        config.save_workspace = lambda: saved.append(True)
        config.save_global = lambda: saved.append(True)
        assert handlers_provider._provider_wizard(MagicMock(), config)
        assert (config.provider, config.model, config.base_url) == ("tongyi", "qwen3.8-max", "https://old.example/v1")
        assert not saved

    def test_extension_key_write_failure_is_not_completed(self, captured, monkeypatch):
        from dashscope_agenticx.cli.wizard import WizardContext
        from dashscope_agenticx.extensions import CustomExtensions, CustomProvider

        provider = CustomProvider(name="failure-provider", base_url="https://local.example/v1")
        monkeypatch.setattr("dashscope_agenticx.extensions._current", CustomExtensions(providers=[provider]))
        monkeypatch.setattr("builtins.input", lambda prompt: "1")
        monkeypatch.setattr(handlers_provider, "_read_secret", lambda prompt: "sk-test")
        monkeypatch.setattr(handlers_key, "_set_extension_provider_token", lambda *args, **kwargs: False)
        assert handlers_provider._wizard_key_step(Config(provider="failure-provider"), context=WizardContext(setup=True)).status == "failed"

    def test_unavailable_provider_is_not_the_enter_default(self, captured):
        cfg = _config("zhipu")
        with (
            patch("builtins.input", return_value=""),
            patch.object(handlers_provider, "_wizard_key_step"),
        ):
            assert handlers_provider._provider_wizard(MagicMock(), cfg) is True

        out = captured.getvalue()
        assert "'zhipu' is not available here" in out
        assert "Enter cannot keep it" in out
        menu = out.split("Available providers:")[1].split("No provider")[0]
        assert "✓" not in menu
        assert "Unknown provider: zhipu" not in out
        assert "No provider chosen; cancelled" in out
        assert cfg.provider == "zhipu"

    def test_picking_a_listed_provider_recovers_the_config(self, captured):
        cfg = _config("zhipu")
        with (
            patch("builtins.input", side_effect=["1", "", ""]),
            patch.object(handlers_provider, "_wizard_key_step"),
            patch.object(
                handlers_provider,
                "_rebuild_provider",
                return_value=True,
            ),
        ):
            assert handlers_provider._provider_wizard(MagicMock(), cfg) is True

        assert (cfg.provider, cfg.model) == ("tongyi", "qwen3.8-flash")

    def test_enter_still_keeps_an_available_provider(self, captured):
        cfg = _config("tongyi", "qwen3.8-max")
        with (
            patch("builtins.input", return_value=""),
            patch.object(handlers_provider, "_wizard_key_step"),
            patch.object(
                handlers_provider,
                "_rebuild_provider",
                return_value=True,
            ),
        ):
            handlers_provider._provider_wizard(MagicMock(), cfg)

        out = captured.getvalue()
        assert "is not available here" not in out
        menu = out.split("Available providers:")[1].split("tongyi available")[
            0
        ]
        assert "✓ tongyi" in menu
        assert cfg.provider == "tongyi"

    def test_menu_offers_inline_provider_registration(self, captured):
        cfg = _config("zhipu")
        with (
            patch("builtins.input", return_value=""),
            patch.object(handlers_provider, "_wizard_key_step"),
        ):
            handlers_provider._provider_wizard(MagicMock(), cfg)

        out = captured.getvalue()
        menu = out.split("Available providers:")[1].split(
            "Choose (number or name)",
        )[0]
        assert "Enter a new provider name" in menu
        assert "/dev provider add" not in menu


class TestModelCommand:
    def test_shortlist_limits_each_modality_and_retains_current_model(self, monkeypatch):
        from collections import Counter

        monkeypatch.setattr("dashscope_agenticx.config.VISION_MODELS", {"vision-1", "vision-2", "vision-3", "multi-1", "multi-2", "multi-3"})
        monkeypatch.setattr("dashscope_agenticx.config.AUDIO_MODELS", {"audio-1", "audio-2", "audio-3", "multi-1", "multi-2", "multi-3"})
        models = ["TEXT-1", "text-2", "text-3", "vision-1", "vision-2", "vision-3",
                  "audio-1", "audio-2", "audio-3", "multi-1", "multi-2", "multi-3"]
        original = list(models)
        selected = handlers_provider._shortlist_models("custom", models, " TEXT-3 ")
        counts = Counter(handlers_provider._model_modality("custom", name) for name in selected)
        assert counts == {"text": 2, "vision": 2, "audio": 2, "multimodal": 2}
        assert selected[:2] == ["text-3", "text-1"]
        assert models == original

    @pytest.mark.parametrize("choice,expected", [("2", "qwen3.8-max"), (" QWEN3.7-MAX ", "qwen3.7-max")])
    def test_model_picker_keeps_raw_ids_and_hidden_models(self, captured, monkeypatch, choice, expected):
        from dashscope_agenticx.cli.wizard import WizardContext

        config = Config(tongyi_api_key="sk-test")
        answers = iter(["", choice, "n"])
        monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
        models = {"tongyi": ["qwen3.8-flash", "qwen3.8-max", "qwen3.7-max", "qwen3-vl-plus", "qwen3-vl-flash", "qwen-vl-max"]}
        monkeypatch.setattr(handlers_provider, "PROVIDER_MODELS", models)
        monkeypatch.setattr(handlers_provider, "register_custom_model", lambda *args, **kwargs: pytest.fail("Registered known model"))
        result = handlers_provider._provider_wizard(MagicMock(), config, context=WizardContext(setup=True))
        assert result.status == "completed" and config.model == expected
        output = captured.getvalue()
        assert "qwen3.8-flash (text)" in output and "qwen3-vl-plus (vision)" in output
        assert "qwen3.7-max" not in output.split("Up to 2 models per modality")[0]

    def test_bare_model_shows_current_and_usage(self, captured):
        cfg = _config("tongyi", "qwen3.8-max")
        with patch.object(
            handlers_provider,
            "_rebuild_provider",
        ) as rebuild:
            assert (
                handlers_provider.handle_model_command(
                    "/model",
                    MagicMock(),
                    cfg,
                )
                is True
            )

        rebuild.assert_not_called()
        out = captured.getvalue()
        assert "tongyi/qwen3.8-max" in out
        assert "/model <name>" in out
        assert cfg.model == "qwen3.8-max"

    def test_switch_to_a_listed_model(self, captured):
        cfg = _config("tongyi", "qwen3.8-max")
        with patch.object(
            handlers_provider,
            "_rebuild_provider",
            return_value=True,
        ) as rebuild:
            handlers_provider.handle_model_command(
                "/model qwen-plus",
                MagicMock(),
                cfg,
            )

        rebuild.assert_called_once()
        assert cfg.model == "qwen-plus"
        assert "✓ Switched: tongyi/qwen-plus" in captured.getvalue()

    def test_unlisted_model_is_registered_for_builtin_providers(
        self,
        captured,
    ):
        # register_custom_model is patched out: the real one mutates the
        # global PROVIDER_MODELS, which would leak into other tests.
        cfg = _config("tongyi", "qwen3.8-max")
        with (
            patch.object(
                handlers_provider,
                "_rebuild_provider",
                return_value=True,
            ),
            patch.object(
                handlers_provider,
                "register_custom_model",
            ) as register,
        ):
            handlers_provider.handle_model_command(
                "/model my-new-model",
                MagicMock(),
                cfg,
            )

        register.assert_called_once_with(cfg, "tongyi", "my-new-model")
        assert cfg.model == "my-new-model"
        assert "Registered tongyi/my-new-model" in captured.getvalue()

    def test_model_name_is_normalized(self, captured):
        cfg = _config("tongyi", "qwen3.8-max")
        with patch.object(
            handlers_provider,
            "_rebuild_provider",
            return_value=True,
        ):
            handlers_provider.handle_model_command(
                "/model Qwen-Plus ",
                MagicMock(),
                cfg,
            )

        assert cfg.model == "qwen-plus"
        assert "normalized to qwen-plus" in captured.getvalue()

    def test_failed_rebuild_reports_no_switch(self, captured):
        cfg = _config("tongyi", "qwen3.8-max")
        with patch.object(
            handlers_provider,
            "_rebuild_provider",
            return_value=False,
        ):
            handlers_provider.handle_model_command(
                "/model qwen-plus",
                MagicMock(),
                cfg,
            )

        assert "✓ Switched" not in captured.getvalue()
