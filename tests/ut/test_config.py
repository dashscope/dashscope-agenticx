# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.config module."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

from dashscope_agenticx.config import (
    AUDIO_MODELS,
    CONFIG_DIR,
    VISION_MODELS,
    WORKSPACE_DIR,
    Config,
    MCPServerConfig,
    SubagentConfig,
    is_vision_model,
)
from dashscope_agenticx.utils.crypto import decrypt_value, encrypt_value
from dashscope_agenticx.utils.toml import (
    parse_value,
    parse_value_raw,
    toml_str,
)


@pytest.fixture(autouse=True)
def _seed_modality_sets():
    """Populate VISION_MODELS/AUDIO_MODELS with a representative set
    before each test, mirroring what apply_extensions() does at runtime
    when the user's custom-extensions.toml declares vision/audio models."""
    VISION_MODELS.clear()
    VISION_MODELS.update(
        {
            "qwen-vl-max",
            "qwen-vl-plus",
            "qwen-omni-turbo",
            "claude-sonnet-4",
            "claude-opus-4",
            "claude-haiku-4",
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-4-vision",
            "glm-4v",
            "glm-4v-plus",
        },
    )
    AUDIO_MODELS.clear()
    AUDIO_MODELS.update({"qwen-omni-turbo"})
    yield
    VISION_MODELS.clear()
    AUDIO_MODELS.clear()


# ---------------------------------------------------------------------------
# is_vision_model
# ---------------------------------------------------------------------------


class TestIsVisionModel:
    @pytest.mark.parametrize(
        "model",
        [
            "qwen-vl-max",
            "qwen-vl-plus",
            "qwen-omni-turbo",
            "claude-sonnet-4",
            "claude-sonnet-4-20250514",
            "claude-opus-4",
            "claude-haiku-4",
            "gpt-4o",
            "gpt-4-turbo",
            "gpt-4-vision",
            "glm-4v",
        ],
    )
    def test_vision_models(self, model):
        assert is_vision_model(model) is True

    @pytest.mark.parametrize(
        "model",
        [
            "qwen-max",
            "qwen-plus",
            "qwen3-max",
            "glm-4",
            "o1",
            "o3-mini",
        ],
    )
    def test_non_vision_models(self, model):
        assert is_vision_model(model) is False


# ---------------------------------------------------------------------------
# _parse_value / _parse_value_raw
# ---------------------------------------------------------------------------


class TestParseValue:
    def test_basic(self):
        assert parse_value('key = "value"') == "value"

    def test_single_quotes(self):
        assert parse_value("key = 'value'") == "value"

    def test_no_quotes(self):
        assert parse_value("key = value") == "value"

    def test_numeric(self):
        assert parse_value("max_turns = 50") == "50"

    def test_raw_preserves_brackets(self):
        assert parse_value_raw('key = ["a", "b"]') == '["a", "b"]'


# ---------------------------------------------------------------------------
# encrypt / decrypt round-trip
# ---------------------------------------------------------------------------


class TestEncryptDecrypt:
    def test_round_trip(self):
        plaintext = "sk-abc123xyz"
        encrypted = encrypt_value(plaintext)
        assert encrypted.startswith("ENC:")
        assert decrypt_value(encrypted) == plaintext

    def test_empty_string(self):
        assert encrypt_value("") == ""
        assert decrypt_value("") == ""

    def test_plain_text_passthrough(self):
        """Legacy unencrypted values are returned as-is."""
        assert decrypt_value("sk-plain") == "sk-plain"

    def test_value_from_another_machine_is_treated_as_unset(
        self,
        capsys,
        monkeypatch,
    ):
        # The machine key derives from hostname + MAC; a config.toml copied
        # from another machine (or a renamed host) holds ENC: values this
        # machine cannot read. Startup must not die on them — an empty slot
        # routes the user back through key entry.
        import dashscope_agenticx.utils.crypto as crypto

        real_key = crypto._machine_key  # pylint: disable=protected-access
        monkeypatch.setattr(crypto, "_machine_key", lambda: b"\x01" * 32)
        foreign = encrypt_value("sk-from-another-machine")
        monkeypatch.setattr(crypto, "_machine_key", real_key)

        assert decrypt_value(foreign) == ""
        assert "could not be decrypted" in capsys.readouterr().err

    def test_malformed_enc_value_is_treated_as_unset(self, capsys):
        assert decrypt_value("ENC:not-valid-base64!!!") == ""
        assert "could not be decrypted" in capsys.readouterr().err

    def test_load_global_survives_a_foreign_key(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        import dashscope_agenticx.utils.crypto as crypto

        real_key = crypto._machine_key  # pylint: disable=protected-access
        monkeypatch.setattr(crypto, "_machine_key", lambda: b"\x01" * 32)
        foreign = encrypt_value("sk-from-another-machine")
        monkeypatch.setattr(crypto, "_machine_key", real_key)

        cfg_dir = tmp_path / ".agenticx"
        cfg_dir.mkdir()
        cfg_file = cfg_dir / "config.toml"
        cfg_file.write_text(
            f'tongyi_api_key = "{foreign}"\ntts_model = "cosyvoice-x"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_FILE", cfg_file)

        loaded = Config()
        loaded._load_global()  # must not raise UnicodeDecodeError
        assert loaded.tongyi_api_key == ""
        # Non-secret fields still load.
        assert loaded.tts_model == "cosyvoice-x"
        assert "could not be decrypted" in capsys.readouterr().err


# ---------------------------------------------------------------------------
# MCPServerConfig / SubagentConfig
# ---------------------------------------------------------------------------


class TestDataclasses:
    def test_mcp_server_config(self):
        cfg = MCPServerConfig(service="test-service", url="http://example.com")
        assert cfg.service == "test-service"
        assert cfg.url == "http://example.com"

    def test_subagent_config_defaults(self):
        cfg = SubagentConfig()
        assert cfg.model == ""
        assert cfg.temperature == 0.0
        assert cfg.max_turns == 0

    def test_subagent_config_to_dict(self):
        cfg = SubagentConfig(model="qwen-max", temperature=0.5, max_turns=10)
        d = cfg.to_dict()
        assert d == {"model": "qwen-max", "temperature": 0.5, "max_turns": 10}

    def test_subagent_config_to_dict_partial(self):
        cfg = SubagentConfig(model="qwen-max")
        d = cfg.to_dict()
        assert d == {"model": "qwen-max"}

    def test_subagent_config_from_dict(self):
        cfg = SubagentConfig.from_dict(
            {"model": "gpt-4o", "temperature": 0.7, "max_turns": 20},
        )
        assert cfg.model == "gpt-4o"
        assert cfg.temperature == 0.7
        assert cfg.max_turns == 20


# ---------------------------------------------------------------------------
# Config defaults
# ---------------------------------------------------------------------------


class TestConfigDefaults:
    def test_model_names_normalized_on_construction_and_assignment(self):
        config = Config(
            model=" QWEN3.8-FLASH ",
            thinking_model=" QWEN3.8-MAX ",
            asr_model=" ParaFormer-V2 ",
            tts_model=" CosyVoice-V2 ",
            custom_models=[
                "tongyi: QWEN3.8-FLASH ",
                "tongyi:qwen3.8-flash",
                " Custom-Model ",
            ],
        )
        assert config.model == "qwen3.8-flash"
        assert config.thinking_model == "qwen3.8-max"
        assert config.asr_model == "paraformer-v2"
        assert config.tts_model == "cosyvoice-v2"
        assert config.custom_models == ["tongyi:qwen3.8-flash", "custom-model"]
        config.model = " QWEN3.8-MAX "
        config.tts_model = " COSYVOICE-V3 "
        assert (
            config.model == "qwen3.8-max"
            and config.tts_model == "cosyvoice-v3"
        )
        subagent = SubagentConfig(model=" QWEN3.8-FLASH ")
        assert subagent.model == "qwen3.8-flash"
        subagent.model = " QWEN3.8-MAX "
        assert subagent.model == "qwen3.8-max"

    def test_default_values(self):
        cfg = Config()
        assert cfg.provider == "tongyi"
        assert cfg.model == "qwen3.8-flash"
        assert cfg.auto_approve is False
        assert cfg.max_turns == 50
        assert cfg.session_persist is True
        assert cfg.mcp_servers == []
        assert cfg.custom_models == []
        assert cfg.user_directives == []

    def test_api_key_property(self):
        cfg = Config(provider="tongyi")
        cfg.tongyi_api_key = "sk-test"
        assert cfg.api_key == "sk-test"

    def test_api_key_setter(self):
        cfg = Config(provider="anthropic")
        cfg.api_key = "sk-anthropic"
        assert cfg.anthropic_api_key == "sk-anthropic"


# ---------------------------------------------------------------------------
# Config.load: environment variable overrides
# ---------------------------------------------------------------------------


class TestConfigLoad:
    @pytest.mark.parametrize("source", ["default", "environment", "workspace"])
    def test_model_names_normalized_before_validation(
        self,
        tmp_path,
        monkeypatch,
        source,
    ):
        workspace = tmp_path / "workspace.toml"
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "global.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            workspace,
        )
        monkeypatch.delenv("AGENTICX_MODEL", raising=False)
        monkeypatch.delenv("AGENTICX_PROVIDER", raising=False)
        for name in (
            "DASHSCOPE_API_KEY",
            "ANTHROPIC_API_KEY",
            "OPENAI_API_KEY",
        ):
            monkeypatch.delenv(name, raising=False)
        if source == "workspace":
            workspace.write_text('model = " QWEN3.8-MAX "\n', encoding="utf-8")
        elif source == "environment":
            monkeypatch.setenv("AGENTICX_MODEL", " QWEN3.8-MAX ")
        config = Config.load(default_model=" QWEN3.8-MAX ")
        assert config.model == "qwen3.8-max"

    def test_custom_model_names_normalized_before_validation(
        self,
        tmp_path,
        monkeypatch,
    ):
        workspace = tmp_path / "workspace.toml"
        workspace.write_text(
            'model = " MY-MODEL "\ncustom_models = "tongyi: MY-MODEL ,tongyi:my-model"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "global.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            workspace,
        )
        monkeypatch.delenv("AGENTICX_MODEL", raising=False)
        monkeypatch.delenv("AGENTICX_PROVIDER", raising=False)
        config = Config.load()
        assert config.model == "my-model" and config.custom_models == [
            "tongyi:my-model",
        ]

    def test_dashscope_api_key_sets_tongyi(self, tmp_path, monkeypatch):
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dash")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.provider == "tongyi"
        assert cfg.tongyi_api_key == "sk-dash"

    def test_anthropic_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-anth")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-sonnet-4-20250514"

    def test_openai_api_key(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OPENAI_API_KEY", "sk-openai")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.provider == "openai"
        assert cfg.model == "gpt-4o"

    def test_agenticx_model_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTICX_MODEL", "custom-model")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.model is not None

    def test_agenticx_provider_override(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTICX_PROVIDER", "tongyi")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.provider == "tongyi"

    def test_agenticx_mcp_servers(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTICX_MCP_SERVERS", "time,code-interpreter")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        services = [s.service for s in cfg.mcp_servers]
        assert "time" in services
        assert "code-interpreter" in services

    def test_memory_disabled_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTICX_MEMORY_DISABLED", "1")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.memory_enabled is False

    def test_dashscope_provider_alias(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTICX_PROVIDER", "dashscope")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.provider == "tongyi"

    def test_invalid_model_fallback(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTICX_MODEL", "nonexistent-model")
        monkeypatch.setenv("AGENTICX_PROVIDER", "tongyi")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        from dashscope_agenticx.config import PROVIDER_MODELS

        assert cfg.model in PROVIDER_MODELS["tongyi"]

    def test_custom_base_url_preserves_model(self, tmp_path, monkeypatch):
        monkeypatch.setenv("AGENTICX_MODEL", "qwen3.7-max")
        monkeypatch.setenv("AGENTICX_PROVIDER", "anthropic")
        monkeypatch.setenv(
            "OPENAI_BASE_URL",
            "https://example.com/apps/anthropic",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.model == "qwen3.7-max"
        assert cfg.base_url == "https://example.com/apps/anthropic"

    def test_extension_provider_model_valid(self, tmp_path, monkeypatch):
        from dashscope_agenticx.extensions import (
            CustomExtensions,
            CustomProvider,
        )

        def _fake_load():
            return CustomExtensions(
                providers=[
                    CustomProvider(
                        name="bailian-anthropic",
                        base_url="https://example.com/apps/anthropic",
                        api_key_env="DASHSCOPE_API_KEY",
                        default_model="qwen3.7-max",
                        models=["qwen3.5-plus"],
                        protocol="anthropic",
                    ),
                ],
            )

        monkeypatch.setattr(
            "dashscope_agenticx.extensions.load_extensions",
            _fake_load,
        )
        monkeypatch.setenv("AGENTICX_PROVIDER", "bailian-anthropic")
        monkeypatch.setenv("AGENTICX_MODEL", "qwen3.5-plus")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.provider == "bailian-anthropic"
        assert cfg.model == "qwen3.5-plus"

    def test_extension_provider_invalid_model_fallback(
        self,
        tmp_path,
        monkeypatch,
    ):
        from dashscope_agenticx.extensions import (
            CustomExtensions,
            CustomProvider,
        )

        def _fake_load():
            return CustomExtensions(
                providers=[
                    CustomProvider(
                        name="bailian-anthropic",
                        base_url="https://example.com/apps/anthropic",
                        api_key_env="DASHSCOPE_API_KEY",
                        default_model="qwen3.7-max",
                        models=["qwen3.5-plus"],
                        protocol="anthropic",
                    ),
                ],
            )

        monkeypatch.setattr(
            "dashscope_agenticx.extensions.load_extensions",
            _fake_load,
        )
        monkeypatch.setenv("AGENTICX_PROVIDER", "bailian-anthropic")
        monkeypatch.setenv("AGENTICX_MODEL", "not-a-real-model")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config.load()
        assert cfg.provider == "bailian-anthropic"
        assert cfg.model == "qwen3.7-max"


# ---------------------------------------------------------------------------
# Config workspace config loading
# ---------------------------------------------------------------------------


class TestConfigWorkspaceLoad:
    def test_load_workspace_config(self, tmp_path, monkeypatch):
        ws_config = tmp_path / "config.toml"
        ws_config.write_text(
            'model = "qwen-max"\n'
            'provider = "tongyi"\n'
            "max_turns = 30\n"
            'user_name = "testuser"\n'
            'enabled_capabilities = "bailian.cli,bailian.mcp"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_config,
        )

        cfg = Config.load()
        assert cfg.model == "qwen-max"
        assert cfg.provider == "tongyi"
        assert cfg.max_turns == 30
        assert cfg.user_name == "testuser"
        assert cfg.enabled_capabilities == ["bailian.cli", "bailian.mcp"]

    def test_workspace_provider_persists_against_api_key_env(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Saved workspace provider should not be overwritten by API-key env vars."""
        monkeypatch.setenv("DASHSCOPE_API_KEY", "sk-dash")
        ws_config = tmp_path / "config.toml"
        ws_config.write_text(
            'provider = "anthropic"\n' 'model = "claude-sonnet-4-20250514"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_config,
        )

        cfg = Config.load()
        assert cfg.provider == "anthropic"
        assert cfg.model == "claude-sonnet-4-20250514"
        assert cfg.tongyi_api_key == "sk-dash"

    def test_load_mcp_servers_from_workspace(self, tmp_path, monkeypatch):
        ws_config = tmp_path / "config.toml"
        ws_config.write_text(
            "[[mcp_servers]]\n"
            'service = "time"\n'
            'url = "http://localhost:8080"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_config,
        )

        cfg = Config.load()
        assert len(cfg.mcp_servers) == 1
        assert cfg.mcp_servers[0].service == "time"
        assert cfg.mcp_servers[0].url == "http://localhost:8080"

    def test_load_subagents_from_workspace(self, tmp_path, monkeypatch):
        ws_config = tmp_path / "config.toml"
        ws_config.write_text(
            "[[subagents]]\n"
            'key = "researcher"\n'
            'model = "qwen-max"\n'
            "temperature = 0.7\n"
            "max_turns = 10\n",
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_config,
        )

        cfg = Config.load()
        assert "researcher" in cfg.subagents
        sub = cfg.subagents["researcher"]
        assert sub.model == "qwen-max"
        assert sub.temperature == 0.7
        assert sub.max_turns == 10

    def test_user_directives_json(self, tmp_path, monkeypatch):
        ws_config = tmp_path / "config.toml"
        ws_config.write_text(
            'user_directives = ["rule one", "rule two"]\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_config,
        )

        cfg = Config.load()
        assert cfg.user_directives == ["rule one", "rule two"]


# ---------------------------------------------------------------------------
# Config save
# ---------------------------------------------------------------------------


class TestConfigSave:
    def test_save_workspace_roundtrip(self, tmp_path, monkeypatch):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )

        cfg = Config()
        cfg.user_name = "testuser"
        cfg.model = "qwen-max"
        cfg.max_turns = 25
        cfg.session_persist = False
        cfg.save_workspace()

        assert ws_file.exists()
        content = ws_file.read_text()
        assert 'user_name = "testuser"' in content
        assert 'model = "qwen-max"' in content
        assert "max_turns = 25" in content
        assert "session_persist = false" in content

    def test_save_global_roundtrip(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / ".agenticx"
        cfg_file = cfg_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_DIR", cfg_dir)
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_FILE", cfg_file)

        cfg = Config(provider="tongyi")
        cfg.tongyi_api_key = "sk-test123"
        cfg.save_global()

        assert cfg_file.exists()
        content = cfg_file.read_text()
        # provider is now workspace-level, not saved in global config
        assert 'provider = "tongyi"' not in content
        assert "tongyi_api_key" in content
        assert "sk-test123" not in content
        assert "KEYRING:v1:config:tongyi_api_key" in content

    def test_save_merges_when_global_and_workspace_paths_coincide(
        self,
        tmp_path,
        monkeypatch,
    ):
        """Running agenticx from $HOME makes both scopes the same file; save() must
        not let workspace settings wipe the API keys."""
        shared_dir = tmp_path / ".agenticx"
        shared_file = shared_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_DIR", shared_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            shared_file,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            shared_dir,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            shared_file,
        )

        cfg = Config(provider="tongyi")
        cfg.tongyi_api_key = "sk-test123"
        cfg.model = "qwen-max"
        cfg.save()

        content = shared_file.read_text()
        assert "tongyi_api_key" in content
        assert 'model = "qwen-max"' in content


# ---------------------------------------------------------------------------
# Dual-LLM / loop-mode fields (thinking_provider, thinking_model, loop_mode)
# ---------------------------------------------------------------------------


class TestThinkingAndLoopFields:
    def test_thinking_provider_default(self):
        cfg = Config()
        assert cfg.thinking_provider == ""

    def test_thinking_model_default(self):
        cfg = Config()
        assert cfg.thinking_model == ""

    def test_loop_mode_default(self):
        cfg = Config()
        assert cfg.loop_mode == "auto"

    def test_all_three_defaults_together(self):
        cfg = Config()
        assert cfg.thinking_provider == ""
        assert cfg.thinking_model == ""
        assert cfg.loop_mode == "auto"

    def test_thinking_provider_roundtrip(self, tmp_path, monkeypatch):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config()
        cfg.thinking_provider = "anthropic"
        cfg.save_workspace()

        loaded = Config()
        loaded._load_workspace_from(ws_file)
        assert loaded.thinking_provider == "anthropic"

    def test_thinking_model_roundtrip(self, tmp_path, monkeypatch):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config()
        cfg.thinking_model = "claude-sonnet-4-20250514"
        cfg.save_workspace()

        loaded = Config()
        loaded._load_workspace_from(ws_file)
        assert loaded.thinking_model == "claude-sonnet-4-20250514"

    def test_loop_mode_roundtrip(self, tmp_path, monkeypatch):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config()
        cfg.loop_mode = "structured"
        cfg.save_workspace()

        loaded = Config()
        loaded._load_workspace_from(ws_file)
        assert loaded.loop_mode == "structured"

    def test_all_three_roundtrip_together(self, tmp_path, monkeypatch):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )

        cfg = Config()
        cfg.thinking_provider = "anthropic"
        cfg.thinking_model = "claude-opus-4-20250514"
        cfg.loop_mode = "structured"
        cfg.save_workspace()

        content = ws_file.read_text()
        assert 'thinking_provider = "anthropic"' in content
        assert 'thinking_model = "claude-opus-4-20250514"' in content
        assert 'loop_mode = "structured"' in content

        loaded = Config()
        loaded._load_workspace_from(ws_file)
        assert loaded.thinking_provider == "anthropic"
        assert loaded.thinking_model == "claude-opus-4-20250514"
        assert loaded.loop_mode == "structured"

    def test_defaults_not_written_to_file(self, tmp_path, monkeypatch):
        """Default values should not be written to the config file."""
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )

        cfg = Config()
        cfg.save_workspace()

        content = ws_file.read_text()
        assert "thinking_provider" not in content
        assert "thinking_model" not in content
        assert "loop_mode" not in content

    def test_defaults_survive_empty_load(self, tmp_path, monkeypatch):
        """Loading a config file without these fields preserves defaults."""
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        ws_file.parent.mkdir(parents=True, exist_ok=True)
        ws_file.write_text('provider = "tongyi"\nmodel = "qwen3.7-plus"\n')

        cfg = Config()
        cfg._load_workspace_from(ws_file)
        assert cfg.thinking_provider == ""
        assert cfg.thinking_model == ""
        assert cfg.loop_mode == "auto"


# ---------------------------------------------------------------------------
# toml_str escaping helper
# ---------------------------------------------------------------------------


class TestTomlStr:
    def test_plain_string(self):
        assert toml_str("hello") == '"hello"'

    def test_escapes_double_quote(self):
        assert toml_str('say "hi"') == '"say \\"hi\\""'

    def test_escapes_backslash(self):
        assert toml_str("C:\\Users\\x") == '"C:\\\\Users\\\\x"'

    def test_escapes_newline_and_tab(self):
        assert toml_str("a\nb\tc") == '"a\\nb\\tc"'

    def test_escapes_control_char_as_unicode(self):
        assert toml_str("a\x00b\x7fc") == '"a\\u0000b\\u007Fc"'

    def test_roundtrip_via_tomllib(self):
        from dashscope_agenticx.utils.toml import tomllib

        value = 'quote " backslash \\ newline\n tab\t end'
        parsed = tomllib.loads(f"key = {toml_str(value)}\n")
        assert parsed["key"] == value


# ---------------------------------------------------------------------------
# C1: array-of-tables ([[subagents]] / [[mcp_servers]]) must be written LAST,
# so trailing bare scalars are not parsed as part of the last table.
# ---------------------------------------------------------------------------


class TestConfigSaveOrdering:
    def _reload(self, ws_file):
        loaded = Config()
        loaded._load_workspace_from(ws_file)
        return loaded

    def test_scalars_survive_with_subagents_and_mcp_servers(
        self,
        tmp_path,
        monkeypatch,
    ):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )

        cfg = Config()
        cfg.subagents["researcher"] = SubagentConfig(
            model="qwen-max",
            temperature=0.7,
            max_turns=10,
        )
        cfg.mcp_servers.append(
            MCPServerConfig(service="time", url="http://localhost:8080"),
        )
        cfg.thinking_provider = "anthropic"
        cfg.thinking_model = "claude-opus-4-20250514"
        cfg.loop_mode = "structured"
        cfg.examples_repo = "https://example.com/repo.git"
        cfg.examples_branch = "dev"
        cfg.save_workspace()

        # Array-of-tables headers must appear after every bare scalar key.
        content = ws_file.read_text()
        first_table = min(
            content.index("[[subagents]]"),
            content.index("[[mcp_servers]]"),
        )
        for scalar in (
            "thinking_provider",
            "thinking_model",
            "loop_mode",
            "examples_repo",
            "examples_branch",
        ):
            assert content.index(f"{scalar} =") < first_table

        loaded = self._reload(ws_file)
        assert loaded.thinking_provider == "anthropic"
        assert loaded.thinking_model == "claude-opus-4-20250514"
        assert loaded.loop_mode == "structured"
        assert loaded.examples_repo == "https://example.com/repo.git"
        assert loaded.examples_branch == "dev"
        assert loaded.subagents["researcher"].model == "qwen-max"
        assert loaded.subagents["researcher"].temperature == 0.7
        assert loaded.subagents["researcher"].max_turns == 10
        assert len(loaded.mcp_servers) == 1
        assert loaded.mcp_servers[0].service == "time"
        assert loaded.mcp_servers[0].url == "http://localhost:8080"

    def test_scalars_survive_with_only_subagents(self, tmp_path, monkeypatch):
        """Without mcp_servers the trailing scalars used to land inside the
        last [[subagents]] entry and were silently dropped on load."""
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )

        cfg = Config()
        cfg.subagents["writer"] = SubagentConfig(model="qwen-max")
        cfg.thinking_model = "claude-opus-4-20250514"
        cfg.save_workspace()

        loaded = self._reload(ws_file)
        assert loaded.subagents["writer"].model == "qwen-max"
        assert loaded.thinking_model == "claude-opus-4-20250514"


# ---------------------------------------------------------------------------
# C2: TOML string escaping — values containing " \ or newlines must not
# corrupt the config file and must round-trip through save+load.
# ---------------------------------------------------------------------------


class TestConfigSaveEscaping:
    def test_workspace_special_chars_roundtrip(self, tmp_path, monkeypatch):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )

        cfg = Config()
        cfg.user_name = 'quote " backslash \\ newline\nend'
        cfg.skill_registry = "C:\\Users\\test\\registry.toml"
        cfg.user_directives = ['带"引号"的规则', "back\\slash\nline"]
        cfg.subagents['res"earcher'] = SubagentConfig(model='qwen-"max"')
        cfg.mcp_servers.append(
            MCPServerConfig(service='ti"me', url="http://ex\\ample.com"),
        )
        cfg.thinking_provider = "anthropic"
        cfg.save_workspace()

        loaded = Config()
        loaded._load_workspace_from(ws_file)
        assert loaded.user_name == cfg.user_name
        assert loaded.skill_registry == cfg.skill_registry
        assert loaded.user_directives == cfg.user_directives
        assert loaded.subagents['res"earcher'].model == 'qwen-"max"'
        assert loaded.mcp_servers[0].service == 'ti"me'
        assert loaded.mcp_servers[0].url == "http://ex\\ample.com"
        # Trailing scalars still load (C1 + C2 combined).
        assert loaded.thinking_provider == "anthropic"

    def test_global_special_chars_roundtrip(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / ".agenticx"
        cfg_file = cfg_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_DIR", cfg_dir)
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_FILE", cfg_file)

        cfg = Config()
        cfg.tongyi_api_key = "sk-test123"
        cfg.tts_enabled = True
        cfg.tts_model = 'weird "model" \\ name'
        cfg.save_global()

        loaded = Config()
        loaded._load_global()
        assert loaded.tongyi_api_key == "sk-test123"
        assert loaded.tts_model == 'weird "model" \\ name'

    def test_legacy_enc_migrates_to_keyring_reference(
        self,
        tmp_path,
        monkeypatch,
    ):
        cfg_dir = tmp_path / ".agenticx"
        cfg_file = cfg_dir / "config.toml"
        cfg_dir.mkdir()
        legacy = encrypt_value("sk-legacy")
        cfg_file.write_text(
            f'tongyi_api_key = "{legacy}"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_DIR", cfg_dir)
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_FILE", cfg_file)

        loaded = Config()
        loaded._load_global()
        assert loaded.tongyi_api_key == "sk-legacy"
        loaded.save_global()

        content = cfg_file.read_text(encoding="utf-8")
        assert "sk-legacy" not in content
        assert "ENC:" not in content
        assert "KEYRING:v1:config:tongyi_api_key" in content


# ---------------------------------------------------------------------------
# C3: config saves must be atomic — temp file in the same directory, then
# os.replace; a crash mid-write must not truncate the existing file.
# ---------------------------------------------------------------------------


class TestConfigAtomicSave:
    def test_save_workspace_uses_tmp_then_replace(self, tmp_path, monkeypatch):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )

        calls = []
        real_replace = os.replace

        def spy_replace(src, dst):
            calls.append((src, dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)

        Config().save_workspace()

        assert len(calls) == 1
        src, dst = calls[0]
        assert Path(dst) == ws_file
        assert Path(src).parent == ws_dir
        assert str(src).endswith(".tmp")
        assert ws_file.exists()
        assert not (ws_dir / "config.toml.tmp").exists()

    def test_save_global_uses_tmp_then_replace(self, tmp_path, monkeypatch):
        cfg_dir = tmp_path / ".agenticx"
        cfg_file = cfg_dir / "config.toml"
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_DIR", cfg_dir)
        monkeypatch.setattr("dashscope_agenticx.config.CONFIG_FILE", cfg_file)

        calls = []
        real_replace = os.replace

        def spy_replace(src, dst):
            calls.append((src, dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)

        cfg = Config()
        cfg.tongyi_api_key = "sk-test123"
        cfg.save_global()

        assert len(calls) == 1
        src, dst = calls[0]
        assert Path(dst) == cfg_file
        assert Path(src).parent == cfg_dir
        assert str(src).endswith(".tmp")
        assert cfg_file.exists()
        assert not (cfg_dir / "config.toml.tmp").exists()

    def test_crash_during_replace_preserves_existing_file(
        self,
        tmp_path,
        monkeypatch,
    ):
        ws_dir = tmp_path / ".agenticx"
        ws_file = ws_dir / "config.toml"
        ws_dir.mkdir(parents=True)
        ws_file.write_text('model = "old-model"\n', encoding="utf-8")
        monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", ws_dir)
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )

        def boom(src, dst):
            raise OSError("模拟崩溃")

        monkeypatch.setattr(os, "replace", boom)

        cfg = Config()
        cfg.model = "new-model"
        with pytest.raises(OSError):
            cfg.save_workspace()

        # Old content untouched, temp file cleaned up.
        assert ws_file.read_text(encoding="utf-8") == 'model = "old-model"\n'
        assert not (ws_dir / "config.toml.tmp").exists()


# ---------------------------------------------------------------------------
# tui_mouse: the wheel must scroll the output log, not the whole screen
# ---------------------------------------------------------------------------


class TestTuiMouse:
    def test_defaults_on_windows_too(self, monkeypatch):
        """Regression guard for the ``os.name != "nt"`` default.

        With capture off the wheel reaches the console host, which scrolls
        the entire alternate screen: the input box moves with the output and
        every notch repaints slowly.
        """
        monkeypatch.setattr(os, "name", "nt")
        assert Config().tui_mouse is True

    def test_workspace_config_can_still_disable_it(
        self,
        tmp_path,
        monkeypatch,
    ):
        ws_file = tmp_path / "config.toml"
        ws_file.write_text("tui_mouse = false\n", encoding="utf-8")
        monkeypatch.setattr(
            "dashscope_agenticx.config.CONFIG_FILE",
            tmp_path / "nonexistent.toml",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )
        assert Config.load().tui_mouse is False

    def test_save_does_not_pin_the_default(self, tmp_path, monkeypatch):
        """An enabled default must stay out of the file, so it can change."""
        ws_file = tmp_path / "config.toml"
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
            ws_file,
        )

        Config().save_workspace()
        assert "tui_mouse" not in ws_file.read_text(encoding="utf-8")

        Config(tui_mouse=False).save_workspace()
        assert "tui_mouse = false" in ws_file.read_text(encoding="utf-8")


class TestLegacyDirectoryMigration:
    @staticmethod
    def _configure_paths(
        monkeypatch,
        config_module,
        tmp_path,
        *,
        global_sources=(),
        workspace_sources=(),
    ):
        """Install ordered legacy sources and isolated destinations."""
        new_global = tmp_path / "config" / "AgenticX"
        new_workspace = tmp_path / "workspace" / ".agenticx"
        monkeypatch.delenv("AGENTICX_DISABLE_MIGRATION", raising=False)
        monkeypatch.setattr(config_module, "CONFIG_DIR", new_global)
        monkeypatch.setattr(config_module, "WORKSPACE_DIR", new_workspace)
        for kind, sources in (
            ("GLOBAL", tuple(global_sources)),
            ("WORKSPACE", tuple(workspace_sources)),
        ):
            monkeypatch.setattr(
                config_module,
                f"LEGACY_{kind}_DIRS",
                sources,
                raising=False,
            )
            # Prevent the superseded singular path from touching the real home
            # when these tests are collected during the source migration.
            monkeypatch.setattr(
                config_module,
                f"LEGACY_{kind}_DIR",
                tmp_path / f"unused-{kind.lower()}-singular",
                raising=False,
            )
        return new_global, new_workspace

    def test_migrates_workspace_and_global_directories_once(
        self,
        tmp_path,
        monkeypatch,
        capsys,
    ):
        import dashscope_agenticx.config as config_module

        old_global = tmp_path / "legacy-home" / ".acli"
        old_workspace = tmp_path / "legacy-workspace" / ".acli"
        old_global.mkdir(parents=True)
        old_workspace.mkdir(parents=True)
        (old_global / "config.toml").write_text("model = 'old'\n")
        (old_workspace / "session").mkdir()
        (old_workspace / "session" / "state.json").write_text("{}")
        new_global, new_workspace = self._configure_paths(
            monkeypatch,
            config_module,
            tmp_path,
            global_sources=(old_global,),
            workspace_sources=(old_workspace,),
        )

        migrations = config_module.migrate_legacy_directories()
        assert migrations == [
            (old_global, new_global),
            (old_workspace, new_workspace),
        ]
        assert (new_global / "config.toml").is_file()
        assert (new_workspace / "session" / "state.json").is_file()
        assert not old_global.exists()
        assert not old_workspace.exists()
        assert "AgenticX migrated" in capsys.readouterr().err

        assert config_module.migrate_legacy_directories() == []

    def test_workspace_prefers_agenticcli_over_acli(
        self,
        tmp_path,
        monkeypatch,
    ):
        import dashscope_agenticx.config as config_module

        preferred = tmp_path / "legacy-workspace" / ".agenticcli"
        fallback = tmp_path / "legacy-workspace" / ".acli"
        preferred.mkdir(parents=True)
        fallback.mkdir()
        (preferred / "config.toml").write_text("model = 'preferred'\n")
        (fallback / "config.toml").write_text("model = 'fallback'\n")
        _, new_workspace = self._configure_paths(
            monkeypatch,
            config_module,
            tmp_path,
            workspace_sources=(preferred, fallback),
        )

        assert config_module.migrate_legacy_directories() == [
            (preferred, new_workspace),
        ]
        assert (new_workspace / "config.toml").read_text() == (
            "model = 'preferred'\n"
        )
        assert (fallback / "config.toml").read_text() == "model = 'fallback'\n"

    def test_workspace_falls_back_directly_to_acli(
        self,
        tmp_path,
        monkeypatch,
    ):
        import dashscope_agenticx.config as config_module

        preferred = tmp_path / "legacy-workspace" / ".agenticcli"
        fallback = tmp_path / "legacy-workspace" / ".acli"
        fallback.mkdir(parents=True)
        (fallback / "config.toml").write_text("model = 'fallback'\n")
        _, new_workspace = self._configure_paths(
            monkeypatch,
            config_module,
            tmp_path,
            workspace_sources=(preferred, fallback),
        )

        assert config_module.migrate_legacy_directories() == [
            (fallback, new_workspace),
        ]
        assert (new_workspace / "config.toml").read_text() == (
            "model = 'fallback'\n"
        )

    def test_destination_collision_preserves_sources_without_merging(
        self,
        tmp_path,
        monkeypatch,
    ):
        import dashscope_agenticx.config as config_module

        preferred = tmp_path / "legacy-workspace" / ".agenticcli"
        fallback = tmp_path / "legacy-workspace" / ".acli"
        preferred.mkdir(parents=True)
        fallback.mkdir()
        (preferred / "source-only.txt").write_text("preferred source")
        (fallback / "fallback-only.txt").write_text("fallback source")
        _, new_workspace = self._configure_paths(
            monkeypatch,
            config_module,
            tmp_path,
            workspace_sources=(preferred, fallback),
        )
        new_workspace.mkdir(parents=True)
        (new_workspace / "existing.txt").write_text("existing destination")

        assert config_module.migrate_legacy_directories() == []
        assert (new_workspace / "existing.txt").read_text() == (
            "existing destination"
        )
        assert not (new_workspace / "source-only.txt").exists()
        assert not (new_workspace / "fallback-only.txt").exists()
        assert (
            preferred / "source-only.txt"
        ).read_text() == "preferred source"
        assert (
            fallback / "fallback-only.txt"
        ).read_text() == "fallback source"

    def test_cross_device_rename_failure_safely_copies_and_preserves_source(
        self,
        tmp_path,
        monkeypatch,
    ):
        import errno

        import dashscope_agenticx.config as config_module

        old_workspace = tmp_path / "legacy-workspace" / ".agenticcli"
        nested = old_workspace / "sessions" / "nested"
        nested.mkdir(parents=True)
        (old_workspace / "config.toml").write_text("model = 'cross-device'\n")
        (nested / "state.json").write_text('{"messages": ["hello"]}\n')
        _, new_workspace = self._configure_paths(
            monkeypatch,
            config_module,
            tmp_path,
            workspace_sources=(old_workspace,),
        )
        real_rename = Path.rename

        def fail_cross_device(source, destination):
            if source == old_workspace:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real_rename(source, destination)

        monkeypatch.setattr(Path, "rename", fail_cross_device)

        assert config_module.migrate_legacy_directories() == [
            (old_workspace, new_workspace),
        ]
        for source_file in (
            old_workspace / "config.toml",
            nested / "state.json",
        ):
            relative = source_file.relative_to(old_workspace)
            assert source_file.is_file()
            assert (new_workspace / relative).read_bytes() == (
                source_file.read_bytes()
            )

    def test_global_prefers_agenticcli_platformdirs_over_acli(
        self,
        tmp_path,
        monkeypatch,
    ):
        import dashscope_agenticx.config as config_module

        platform_source = tmp_path / "legacy-config" / "AgenticCLI"
        fallback = tmp_path / "legacy-home" / ".acli"
        platform_source.mkdir(parents=True)
        fallback.mkdir(parents=True)
        (platform_source / "config.toml").write_text("model = 'platform'\n")
        (fallback / "config.toml").write_text("model = 'acli'\n")
        new_global, _ = self._configure_paths(
            monkeypatch,
            config_module,
            tmp_path,
            global_sources=(platform_source, fallback),
        )

        assert config_module.migrate_legacy_directories() == [
            (platform_source, new_global),
        ]
        assert (
            new_global / "config.toml"
        ).read_text() == "model = 'platform'\n"
        assert (fallback / "config.toml").read_text() == "model = 'acli'\n"

    def test_copy_verification_failure_preserves_source_without_publishing(
        self,
        tmp_path,
        monkeypatch,
    ):
        import errno

        import dashscope_agenticx.config as config_module

        old_workspace = tmp_path / "legacy-workspace" / ".agenticcli"
        old_workspace.mkdir(parents=True)
        source_file = old_workspace / "config.toml"
        source_file.write_text("model = 'preserve-me'\n")
        _, new_workspace = self._configure_paths(
            monkeypatch,
            config_module,
            tmp_path,
            workspace_sources=(old_workspace,),
        )
        real_rename = Path.rename
        real_manifest = config_module._tree_manifest

        def fail_cross_device(source, destination):
            if source == old_workspace:
                raise OSError(errno.EXDEV, "Invalid cross-device link")
            return real_rename(source, destination)

        def fail_staging_verification(root):
            manifest = real_manifest(root)
            if root.name.startswith(".agenticx.migration-"):
                manifest["verification"] = ("file", "mismatch")
            return manifest

        monkeypatch.setattr(Path, "rename", fail_cross_device)
        monkeypatch.setattr(
            config_module,
            "_tree_manifest",
            fail_staging_verification,
        )

        assert config_module.migrate_legacy_directories() == []
        assert source_file.read_text() == "model = 'preserve-me'\n"
        assert not new_workspace.exists()
        assert not list(
            new_workspace.parent.glob(".agenticx.migration-*"),
        )
