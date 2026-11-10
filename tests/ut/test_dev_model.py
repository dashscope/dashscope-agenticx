# -*- coding: utf-8 -*-
from __future__ import annotations

import types

import pytest

from dashscope_agenticx.config import PROVIDER_MODELS, register_custom_model
from dashscope_agenticx.dev import _infer_provider_from_model, _model_add


class TestInferProviderFromModel:
    @pytest.mark.parametrize(
        "model,expected",
        [
            ("qwen3.7-plus", "tongyi"),
            ("qwen-vl-max", "tongyi"),
            ("claude-sonnet-4", "anthropic"),
            ("claude-opus-4", "anthropic"),
            ("gpt-4o", "openai"),
            ("gpt-4o-mini", "openai"),
        ],
    )
    def test_known_prefixes(self, model, expected):
        assert _infer_provider_from_model(model) == expected

    def test_unknown_prefix(self):
        assert _infer_provider_from_model("unknown-model") is None


class TestModelAdd:
    def test_add_with_inferred_provider(self):
        config = types.SimpleNamespace(
            custom_models=[],
            save_workspace=lambda: None,
        )
        original = list(PROVIDER_MODELS.get("openai", []))
        try:
            _model_add(config, "openai", "gpt-test")
            assert "gpt-test" in PROVIDER_MODELS["openai"]
            assert "openai:gpt-test" in config.custom_models
        finally:
            PROVIDER_MODELS["openai"] = original

    def test_add_existing_model_is_no_op(self):
        config = types.SimpleNamespace(
            custom_models=[],
            save_workspace=lambda: None,
        )
        existing = PROVIDER_MODELS["openai"][0]
        _model_add(config, "openai", existing)
        assert "openai:" + existing not in config.custom_models

    def test_uppercase_name_is_normalized(self):
        config = types.SimpleNamespace(
            custom_models=[],
            save_workspace=lambda: None,
        )
        original = list(PROVIDER_MODELS.get("tongyi", []))
        try:
            _model_add(config, "tongyi", "Qwen9.9-Test")
            assert "qwen9.9-test" in PROVIDER_MODELS["tongyi"]
            assert "Qwen9.9-Test" not in PROVIDER_MODELS["tongyi"]
            assert "tongyi:qwen9.9-test" in config.custom_models
        finally:
            PROVIDER_MODELS["tongyi"] = original

    def test_case_insensitive_existing_is_no_op(self):
        config = types.SimpleNamespace(
            custom_models=[],
            save_workspace=lambda: None,
        )
        existing = PROVIDER_MODELS["tongyi"][0]
        _model_add(config, "tongyi", existing.upper())
        assert not any(e.startswith("tongyi:") for e in config.custom_models)

    def test_register_custom_model_trims_and_dedups(self):
        config = types.SimpleNamespace(
            custom_models=[],
            save_workspace=lambda: None,
        )
        original = list(PROVIDER_MODELS.get("openai", []))
        try:
            register_custom_model(config, "openai", " GPT-X ")
            register_custom_model(config, "openai", "gpt-x")
            assert config.custom_models == ["openai:gpt-x"]
            assert PROVIDER_MODELS["openai"].count("gpt-x") == 1
        finally:
            PROVIDER_MODELS["openai"] = original


class TestProviderWizardModelStep:
    def test_unlisted_model_is_registered_normalized(self, monkeypatch):
        import dashscope_agenticx.cli.handlers_provider as hp

        monkeypatch.setattr(hp, "find_provider", lambda name: None)
        monkeypatch.setattr(
            hp,
            "_wizard_key_step",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            hp,
            "_read_choice",
            lambda prompt, default="": default,
        )
        monkeypatch.setattr(
            hp,
            "_rebuild_provider",
            lambda agent, config: True,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: types.SimpleNamespace(errors=[], providers=[]),
        )

        def fake_pick(
            label,
            options,
            default,
            custom_hint="",
            option_labels=None,
        ):
            if "available models" in label.lower():
                return "Qwen9.9-Test"
            return default

        monkeypatch.setattr(hp, "_numbered_pick", fake_pick)

        config = types.SimpleNamespace(
            provider="tongyi",
            model="qwen3.7-max",
            protocol="openai",
            base_url="",
            custom_models=[],
            save_global=lambda: None,
            save_workspace=lambda: None,
        )
        original = list(PROVIDER_MODELS.get("tongyi", []))
        try:
            hp._provider_wizard(types.SimpleNamespace(), config)
            assert config.model == "qwen9.9-test"
            assert "tongyi:qwen9.9-test" in config.custom_models
            assert "qwen9.9-test" in PROVIDER_MODELS["tongyi"]
        finally:
            PROVIDER_MODELS["tongyi"] = original

    def test_listed_model_not_reregistered(self, monkeypatch):
        import dashscope_agenticx.cli.handlers_provider as hp

        monkeypatch.setattr(hp, "find_provider", lambda name: None)
        monkeypatch.setattr(
            hp,
            "_wizard_key_step",
            lambda config, **kwargs: None,
        )
        monkeypatch.setattr(
            hp,
            "_read_choice",
            lambda prompt, default="": default,
        )
        monkeypatch.setattr(
            hp,
            "_rebuild_provider",
            lambda agent, config: True,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: types.SimpleNamespace(errors=[], providers=[]),
        )
        monkeypatch.setattr(
            hp,
            "_numbered_pick",
            lambda label, options, default, custom_hint="", option_labels=None: default,
        )

        config = types.SimpleNamespace(
            provider="tongyi",
            model="qwen3.7-max",
            protocol="openai",
            base_url="",
            custom_models=[],
            save_global=lambda: None,
            save_workspace=lambda: None,
        )
        hp._provider_wizard(types.SimpleNamespace(), config)
        assert config.model == "qwen3.7-max"
        assert config.custom_models == []
