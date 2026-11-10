# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.extensions: provider secret persistence and key resolution."""

from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from dashscope_agenticx.extensions import (
    CustomProvider,
    CustomTool,
    set_provider_secret,
)
from dashscope_agenticx.utils.crypto import decrypt_value, encrypt_value


class TestShellToolArgumentSecurity:
    @staticmethod
    def handler(template):
        from dashscope_agenticx.extensions import (
            CustomExtensions,
            CustomShellTool,
            _register_custom_shell_tools,
        )
        from dashscope_agenticx.tools.registry import (
            ToolRegistry,
            bind_tool_registry,
        )

        registry = ToolRegistry()
        extensions = CustomExtensions(
            shell_tools=[
                CustomShellTool(
                    name="security_check",
                    description="Test shell tool",
                    command_template=template,
                ),
            ],
        )
        with bind_tool_registry(registry):
            _register_custom_shell_tools(extensions)
        return registry.get("security_check").func

    @pytest.mark.parametrize("quote", ["", "'", '"'])
    async def test_quoted_parameters_are_literal_arguments(
        self,
        tmp_path,
        quote,
    ):
        import shlex
        import sys

        marker = tmp_path / "unexpected-shell-execution"
        payload = f"$(printf injected > {marker})"
        code = "import sys; print(sys.argv[1])"
        template = (
            f"{shlex.quote(sys.executable)} -c {shlex.quote(code)} "
            f"{quote}{{{{text}}}}{quote}"
        )

        output = await self.handler(template)(text=payload)

        assert output == payload
        assert not marker.exists()

    @pytest.mark.parametrize(
        "payload",
        [
            "text with spaces",
            "quote' and double\"",
            "line one\nline two",
            "{{other}}",
            "C:\\Program Files\\a & b",
            "x; echo unexpected",
        ],
    )
    async def test_substitution_does_not_change_argument_boundaries(
        self,
        payload,
    ):
        from types import SimpleNamespace

        with patch(
            "subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout="ok",
                stderr="",
            ),
        ) as runner:
            output = await self.handler('echo "{{text}}" {{other}}')(
                text=payload,
                other="separate",
            )

        assert output == "ok"
        assert runner.call_args.args[0] == ["echo", payload, "separate"]
        assert runner.call_args.kwargs["shell"] is False

    @pytest.mark.parametrize(
        "template",
        [
            "echo {{text}} | cat",
            "sh -c {{text}}",
            "cmd /c {{text}}",
            "powershell.exe -Command {{text}}",
            "{{program}} {{text}}",
            "python -c {{text}}",
            "python -m {{text}}",
        ],
    )
    async def test_dynamic_shell_or_executable_code_is_rejected(
        self,
        template,
    ):
        with patch("subprocess.run") as runner:
            output = await self.handler(template)(
                text="payload",
                program="echo",
            )

        assert output.startswith("Execution failed:")
        runner.assert_not_called()

    async def test_constant_shell_commands_keep_their_existing_behavior(self):
        from types import SimpleNamespace

        template = "echo hello | cat"
        with patch(
            "subprocess.run",
            return_value=SimpleNamespace(
                returncode=0,
                stdout="hello",
                stderr="",
            ),
        ) as runner:
            output = await self.handler(template)()

        assert output == "hello"
        assert runner.call_args.args[0] == template
        assert runner.call_args.kwargs["shell"] is True


class TestResolveApiKey:
    def test_env_set_returns_env_value(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "sk-env")
        p = CustomProvider(
            name="kimi",
            base_url="https://api.moonshot.cn/v1",
            api_key_env="MOONSHOT_API_KEY",
        )
        assert p.resolve_api_key() == "sk-env"

    def test_env_empty_falls_back_to_enc(self, monkeypatch):
        monkeypatch.setenv("MOONSHOT_API_KEY", "")
        enc = encrypt_value("sk-enc")
        p = CustomProvider(
            name="kimi",
            base_url="https://api.moonshot.cn/v1",
            api_key_env="MOONSHOT_API_KEY",
            api_key_enc=enc,
        )
        assert p.resolve_api_key() == "sk-enc"

    def test_no_env_uses_enc(self, monkeypatch):
        monkeypatch.delenv("MOONSHOT_API_KEY", raising=False)
        enc = encrypt_value("sk-enc")
        p = CustomProvider(
            name="kimi",
            base_url="https://api.moonshot.cn/v1",
            api_key_enc=enc,
        )
        assert p.resolve_api_key() == "sk-enc"

    def test_env_unset_falls_back_to_enc(self, monkeypatch):
        # env var name declared but not exported at all
        monkeypatch.delenv("MISSING_KEY", raising=False)
        enc = encrypt_value("sk-enc")
        p = CustomProvider(
            name="kimi",
            base_url="https://api.moonshot.cn/v1",
            api_key_env="MISSING_KEY",
            api_key_enc=enc,
        )
        assert p.resolve_api_key() == "sk-enc"

    def test_no_env_no_enc_returns_empty(self):
        p = CustomProvider(name="kimi", base_url="https://api.moonshot.cn/v1")
        assert p.resolve_api_key() == ""

    def test_auth_false_returns_placeholder(self):
        p = CustomProvider(
            name="ollama",
            base_url="http://localhost:11434/v1",
            auth=False,
        )
        assert p.resolve_api_key() == "ollama"

    def test_auth_false_ignores_env_and_enc(self, monkeypatch):
        monkeypatch.setenv("OLLAMA_API_KEY", "ignored")
        enc = encrypt_value("also-ignored")
        p = CustomProvider(
            name="ollama",
            base_url="http://localhost:11434/v1",
            api_key_env="OLLAMA_API_KEY",
            api_key_enc=enc,
            auth=False,
        )
        assert p.resolve_api_key() == "ollama"


class TestResolvedProtocol:
    @pytest.mark.parametrize(
        "raw,expected",
        [
            ("openai", "openai"),
            ("openai_compatible", "openai"),
            ("OpenAI", "openai"),
            ("anthropic", "anthropic"),
            ("dashscope", "dashscope"),
            ("DashScope", "dashscope"),
            ("", "openai"),
        ],
    )
    def test_normalization(self, raw, expected):
        p = CustomProvider(name="p", base_url="https://x", protocol=raw)
        assert p.resolved_protocol() == expected


class TestVisionAudioModelsLoading:
    def test_custom_models_normalized_and_deduplicated(self):
        provider = CustomProvider(
            name="kimi",
            base_url="https://local.example/v1",
            default_model=" KIMI-K2 ",
            models=[" KIMI-K2 ", "kimi-k2", " KIMI-K3 ", " "],
            vision_models=[" KIMI-VL ", "kimi-vl"],
            audio_models=[" KIMI-AUDIO ", "kimi-audio"],
        )
        assert provider.default_model == "kimi-k2"
        assert provider.resolved_models() == ["kimi-k2", "kimi-k3"]
        assert provider.vision_models == [
            "kimi-vl",
        ] and provider.audio_models == ["kimi-audio"]
        assert (
            CustomTool(
                name="vision",
                description="Vision",
                endpoint="",
                type="vision",
                model=" KIMI-VL ",
            ).model
            == "kimi-vl"
        )

    @pytest.mark.parametrize("uppercase", [False, True])
    def test_load_vision_models_from_toml(
        self,
        tmp_path,
        monkeypatch,
        uppercase,
    ):
        """Per-provider vision_models lists are still supported for
        backwards compatibility (tomls that haven't migrated to the
        capability mechanism)."""
        transform = str.upper if uppercase else str.lower
        toml = tmp_path / "custom-extensions.toml"
        toml.write_text(
            "[[providers]]\n"
            'name = "tongyi"\n'
            'base_url = "https://dashscope.aliyuncs.com/compatible-mode/v1"\n'
            'api_key_env = "DASHSCOPE_API_KEY"\n'
            f'default_model = "{transform("qwen3.8-flash")}"\n'
            f'models = ["{transform("qwen3.8-max")}"]\n'
            f'vision_models = ["{transform("qwen3-vl-plus")}"]\n'
            f'audio_models = ["{transform("qwen-audio-3.1-realtime-plus")}"]\n',
            encoding="utf-8",
        )
        # Point GLOBAL_EXTENSIONS_FILE at our temp toml and the workspace file
        # at a missing path so only our temp file is loaded.
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.GLOBAL_EXTENSIONS_FILE",
            toml,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.WORKSPACE_EXTENSIONS_FILE",
            tmp_path / "nope.toml",
        )

        from dashscope_agenticx.extensions import load_extensions

        ext = load_extensions()
        prov = next((p for p in ext.providers if p.name == "tongyi"), None)
        assert prov is not None
        assert prov.default_model == "qwen3.8-flash"
        assert prov.models == ["qwen3.8-max"]
        assert "qwen3-vl-plus" in prov.vision_models
        assert "qwen-audio-3.1-realtime-plus" in prov.audio_models

    def test_load_vision_capability_from_toml(self, tmp_path, monkeypatch):
        """type="vision" tools in [[capabilities.tools]] are the new way to
        declare vision models. Each tool carries provider + model fields."""
        toml = tmp_path / "custom-extensions.toml"
        toml.write_text(
            "[[capabilities]]\n"
            'key = "tongyi.vision"\n'
            'display = "通义视觉"\n'
            'api_key_env = "DASHSCOPE_API_KEY"\n\n'
            "[[capabilities.tools]]\n"
            'name = "understand_image"\n'
            'type = "vision"\n'
            'provider = "tongyi"\n'
            'model = "qwen3-vl-plus"\n'
            'description = "用视觉模型理解图片"\n'
            'permission = "auto"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.GLOBAL_EXTENSIONS_FILE",
            toml,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.WORKSPACE_EXTENSIONS_FILE",
            tmp_path / "nope.toml",
        )

        from dashscope_agenticx.extensions import load_extensions

        ext = load_extensions()
        cap = next(
            (c for c in ext.capabilities if c.key == "tongyi.vision"),
            None,
        )
        assert cap is not None
        assert len(cap.tools) == 1
        tool = cap.tools[0]
        assert tool.type == "vision"
        assert tool.provider == "tongyi"
        assert tool.model == "qwen3-vl-plus"
        # endpoint not required for vision tools
        assert tool.endpoint == ""

    def test_validate_tool_vision_requires_provider_and_model(self):
        from dashscope_agenticx.extensions import _validate_tool

        # missing provider
        err = _validate_tool({"name": "t", "type": "vision", "model": "m"})
        assert "provider" in err
        # missing model
        err = _validate_tool({"name": "t", "type": "vision", "provider": "p"})
        assert "model" in err
        # valid
        err = _validate_tool(
            {"name": "t", "type": "vision", "provider": "p", "model": "m"},
        )
        assert err == ""

    def test_validate_tool_vision_skips_endpoint_check(self):
        from dashscope_agenticx.extensions import _validate_tool

        # HTTP tools require endpoint; vision tools don't
        err = _validate_tool({"name": "t"})
        assert "endpoint" in err
        err = _validate_tool(
            {"name": "t", "type": "vision", "provider": "p", "model": "m"},
        )
        assert err == ""

    def test_sync_modality_sets_includes_capability_vision_models(
        self,
        tmp_path,
    ):
        """_sync_modality_sets scans capabilities' type="vision" tools and
        adds their model to VISION_MODELS so is_vision_model() works for
        the @image.png REPL flow."""
        from dashscope_agenticx.config import VISION_MODELS
        from dashscope_agenticx.extensions import (
            CustomCapability,
            CustomExtensions,
            CustomTool,
            _sync_modality_sets,
        )

        ext = CustomExtensions()
        cap = CustomCapability(key="tongyi.vision")
        cap.tools.append(
            CustomTool(
                name="understand_image",
                description="",
                endpoint="",
                type="vision",
                provider="tongyi",
                model="qwen3-vl-plus",
            ),
        )
        ext.capabilities.append(cap)

        VISION_MODELS.clear()
        _sync_modality_sets(ext)
        assert "qwen3-vl-plus" in VISION_MODELS

    def test_sync_modality_sets_populates_global_sets(self, tmp_path):
        from dashscope_agenticx.config import AUDIO_MODELS, VISION_MODELS
        from dashscope_agenticx.extensions import (
            CustomExtensions,
            _sync_modality_sets,
        )

        ext = CustomExtensions()
        ext.providers.append(
            CustomProvider(
                name="tongyi",
                base_url="https://x",
                vision_models=["qwen3-vl-plus"],
                audio_models=["qwen-audio-3.1-realtime-plus"],
            ),
        )
        ext.providers.append(
            CustomProvider(
                name="openai",
                base_url="https://y",
                vision_models=["qwen3-vl-plus"],
                audio_models=[],
            ),
        )

        VISION_MODELS.clear()
        AUDIO_MODELS.clear()

        _sync_modality_sets(ext)

        assert "qwen3-vl-plus" in VISION_MODELS
        assert "qwen-audio-3.1-realtime-plus" in AUDIO_MODELS

    def test_vision_models_default_empty(self):
        p = CustomProvider(name="p", base_url="https://x")
        assert p.vision_models == []
        assert p.audio_models == []


class TestBuildVisionTool:
    def test_build_vision_tool_returns_callable(self):
        from dashscope_agenticx.extensions import (
            CustomCapability,
            CustomTool,
            build_vision_tool,
        )

        cap = CustomCapability(key="tongyi.vision")
        tool = CustomTool(
            name="understand_image",
            description="",
            endpoint="",
            type="vision",
            provider="tongyi",
            model="qwen3-vl-plus",
        )
        call_fn = build_vision_tool(cap, tool)
        assert callable(call_fn)

    @pytest.mark.asyncio
    async def test_vision_tool_calls_provider_chat_with_image_block(
        self,
        tmp_path: Path,
    ):
        from dashscope_agenticx.extensions import (
            CustomCapability,
            CustomTool,
            build_vision_tool,
        )

        cap = CustomCapability(key="tongyi.vision")
        tool = CustomTool(
            name="understand_image",
            description="",
            endpoint="",
            type="vision",
            provider="tongyi",
            model="qwen3-vl-plus",
        )
        call_fn = build_vision_tool(cap, tool)

        # Prepare a real image file
        img = tmp_path / "pic.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake body")

        # Mock the provider chain
        mock_response = MagicMock()
        mock_response.content = "A cat."
        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(return_value=mock_response)

        with patch(
            "dashscope_agenticx.providers._create_provider",
            return_value=mock_provider,
        ) as m_create:
            result = await call_fn(image_path=str(img), question="what?")

        assert result == "A cat."
        # Verify profile passed to _create_provider
        profile = m_create.call_args.args[0]
        assert profile.model == "qwen3-vl-plus"
        assert profile.provider == "tongyi"
        # Verify the messages sent to provider.chat contain an image_url block
        messages = mock_provider.chat.call_args.args[0]
        assert messages[0]["role"] == "user"
        blocks = messages[0]["content"]
        assert any(b.get("type") == "image_url" for b in blocks)
        image_block = next(b for b in blocks if b.get("type") == "image_url")
        assert image_block["image_url"]["url"].startswith(
            "data:image/png;base64,",
        )
        text_block = next(b for b in blocks if b.get("type") == "text")
        assert text_block["text"] == "what?"

    @pytest.mark.asyncio
    async def test_vision_tool_unsupported_extension_returns_error(
        self,
        tmp_path: Path,
    ):
        from dashscope_agenticx.extensions import (
            CustomCapability,
            CustomTool,
            build_vision_tool,
        )

        cap = CustomCapability(key="tongyi.vision")
        tool = CustomTool(
            name="understand_image",
            description="",
            endpoint="",
            type="vision",
            provider="tongyi",
            model="qwen3-vl-plus",
        )
        call_fn = build_vision_tool(cap, tool)

        bad = tmp_path / "file.txt"
        bad.write_bytes(b"not an image")
        result = await call_fn(image_path=str(bad))
        assert "failed to read image" in result
        assert "Unsupported image extension" in result

    @pytest.mark.asyncio
    async def test_vision_tool_provider_failure_returns_error(
        self,
        tmp_path: Path,
    ):
        from dashscope_agenticx.extensions import (
            CustomCapability,
            CustomTool,
            build_vision_tool,
        )

        cap = CustomCapability(key="tongyi.vision")
        tool = CustomTool(
            name="understand_image",
            description="",
            endpoint="",
            type="vision",
            provider="tongyi",
            model="qwen3-vl-plus",
        )
        call_fn = build_vision_tool(cap, tool)

        img = tmp_path / "pic.png"
        img.write_bytes(b"png")

        mock_provider = MagicMock()
        mock_provider.chat = AsyncMock(side_effect=RuntimeError("boom"))

        with patch(
            "dashscope_agenticx.providers._create_provider",
            return_value=mock_provider,
        ):
            result = await call_fn(image_path=str(img))
        assert "Vision model call failed" in result
        assert "boom" in result


class TestSetProviderSecret:
    def _write_toml(self, path: Path, body: str) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(body, encoding="utf-8")

    def test_write_enc_keeps_env(self, tmp_path):
        toml = tmp_path / "custom-extensions.toml"
        self._write_toml(
            toml,
            "[[providers]]\n"
            'name = "kimi"\n'
            'base_url = "https://api.moonshot.cn/v1"\n'
            'api_key_env = "MOONSHOT_API_KEY"\n'
            'default_model = "kimi-k3"\n',
        )
        enc = encrypt_value("sk-secret")
        assert set_provider_secret(toml, "kimi", api_key_enc=enc) is True

        from dashscope_agenticx.utils.toml import tomllib

        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        prov = data["providers"][0]
        assert prov["api_key"] == enc
        # env should still be present so env var wins when set
        assert prov["api_key_env"] == "MOONSHOT_API_KEY"
        assert decrypt_value(prov["api_key"]) == "sk-secret"

    def test_switch_to_env_removes_enc(self, tmp_path):
        toml = tmp_path / "custom-extensions.toml"
        enc = encrypt_value("sk-old")
        self._write_toml(
            toml,
            "[[providers]]\n"
            'name = "kimi"\n'
            'base_url = "https://api.moonshot.cn/v1"\n'
            f'api_key = "{enc}"\n',
        )
        assert (
            set_provider_secret(toml, "kimi", api_key_env="MOONSHOT_API_KEY")
            is True
        )

        from dashscope_agenticx.utils.toml import tomllib

        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        prov = data["providers"][0]
        assert prov["api_key_env"] == "MOONSHOT_API_KEY"
        assert "api_key" not in prov

    def test_clear_removes_both(self, tmp_path):
        toml = tmp_path / "custom-extensions.toml"
        enc = encrypt_value("sk-old")
        self._write_toml(
            toml,
            "[[providers]]\n"
            'name = "kimi"\n'
            'base_url = "https://api.moonshot.cn/v1"\n'
            'api_key_env = "MOONSHOT_API_KEY"\n'
            f'api_key = "{enc}"\n',
        )
        assert set_provider_secret(toml, "kimi") is True

        from dashscope_agenticx.utils.toml import tomllib

        data = tomllib.loads(toml.read_text(encoding="utf-8"))
        prov = data["providers"][0]
        assert "api_key_env" not in prov
        assert "api_key" not in prov

    def test_unknown_provider_returns_false(self, tmp_path):
        toml = tmp_path / "custom-extensions.toml"
        self._write_toml(
            toml,
            '[[providers]]\nname = "kimi"\nbase_url = "https://x"\n',
        )
        assert set_provider_secret(toml, "ghost", api_key_enc="ENC:x") is False

    def test_missing_file_returns_false(self, tmp_path):
        assert (
            set_provider_secret(
                tmp_path / "nope.toml",
                "kimi",
                api_key_enc="ENC:x",
            )
            is False
        )


# ---------------------------------------------------------------------------
# C2: TOML string escaping in the /dev writers — values containing " \ ' or
# newlines must not corrupt custom-extensions.toml and must round-trip.
# ---------------------------------------------------------------------------


class TestAppendSkillEscaping:
    def test_special_chars_roundtrip(self, tmp_path):
        from dashscope_agenticx.utils.toml import tomllib

        from dashscope_agenticx.extensions import CustomSkill, append_skill

        target = tmp_path / "custom-extensions.toml"
        skill = CustomSkill(
            name='code "review"',
            description="审查代码 \\ 找出问题",
            prompt_template='请 review {{path}}:\n关注 "安全" 问题',
            arguments=['pa"th', "C:\\src"],
            mcp_service='ser"vice',
        )
        append_skill(target, skill)

        data = tomllib.loads(target.read_text(encoding="utf-8"))
        s = data["skills"][0]
        assert s["name"] == 'code "review"'
        assert s["description"] == "审查代码 \\ 找出问题"
        assert s["prompt_template"] == '请 review {{path}}:\n关注 "安全" 问题'
        assert s["arguments"] == ['pa"th', "C:\\src"]
        assert s["mcp_service"] == 'ser"vice'


class TestAppendShellToolEscaping:
    def test_special_chars_roundtrip(self, tmp_path):
        from dashscope_agenticx.utils.toml import tomllib

        from dashscope_agenticx.extensions import (
            CustomShellTool,
            append_shell_tool,
        )

        target = tmp_path / "custom-extensions.toml"
        tool = CustomShellTool(
            name='find "files"',
            description="查找文件 \\ 快速",
            # Single quote + double quote + backslash — the old literal-string
            # write ('...') broke on the single quote alone.
            command_template="grep -rn '{{pattern}}' \"C:\\Program Files\"",
            params=[
                {
                    "name": "pattern",
                    "type": "string",
                    "required": True,
                    "description": '匹配 "模式"\n支持正则',
                },
            ],
            permission="auto",
        )
        append_shell_tool(target, tool)

        data = tomllib.loads(target.read_text(encoding="utf-8"))
        t = data["shell_tools"][0]
        assert t["name"] == 'find "files"'
        assert t["description"] == "查找文件 \\ 快速"
        assert (
            t["command_template"]
            == "grep -rn '{{pattern}}' \"C:\\Program Files\""
        )
        assert t["params"][0]["description"] == '匹配 "模式"\n支持正则'
        assert t["params"][0]["required"] is True
        assert t["permission"] == "auto"


class TestTomlKvEscaping:
    def test_scalar_string(self):
        from dashscope_agenticx.extensions import _toml_kv

        line = _toml_kv("key", 'a "b" \\ c\nd')
        assert line == 'key = "a \\"b\\" \\\\ c\\nd"'

    def test_list_of_strings_escaped_per_element(self):
        from dashscope_agenticx.utils.toml import tomllib

        from dashscope_agenticx.extensions import _toml_kv

        values = ['q"q', "b\\b", "n\nn"]
        parsed = tomllib.loads(_toml_kv("key", values) + "\n")
        assert parsed["key"] == values

    def test_inline_table_string_values(self):
        from dashscope_agenticx.utils.toml import tomllib

        from dashscope_agenticx.extensions import _toml_kv

        parsed = tomllib.loads(_toml_kv("key", {"name": 'v"\\x'}) + "\n")
        assert parsed["key"] == {"name": 'v"\\x'}

    def test_list_of_inline_tables(self):
        from dashscope_agenticx.utils.toml import tomllib

        from dashscope_agenticx.extensions import _toml_kv

        params = [{"name": "p", "description": '带"引号"', "required": True}]
        parsed = tomllib.loads(_toml_kv("params", params) + "\n")
        assert parsed["params"] == params

    def test_non_string_scalars_unchanged(self):
        from dashscope_agenticx.extensions import _toml_kv

        assert _toml_kv("flag", True) == "flag = true"
        assert _toml_kv("count", 42) == "count = 42"
        assert _toml_kv("ratio", 0.5) == "ratio = 0.5"
