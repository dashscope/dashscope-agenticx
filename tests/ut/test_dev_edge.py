# -*- coding: utf-8 -*-
"""Edge-case tests for the develop module: /dev dispatch surface, extension
add/list/remove flows (provider/capability/skill/tool), model registry,
debug utilities, help-menu completeness, and shell escape."""

from __future__ import annotations

import types
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import dashscope_agenticx.dev as dev
from dashscope_agenticx.config import PROVIDER_MODELS, Config
from dashscope_agenticx.dev import (
    _apply_custom_models,
    _choose_target,
    _debug_call,
    _debug_schema,
    _debug_tools,
    _dev_log,
    _dev_reload,
    _format_models,
    _model_list,
    _model_remove,
    _prompt,
    _test_provider,
    handle_dev_command,
)


@pytest.fixture
def out(monkeypatch):
    """Replace the dev console with a mock; returns a callable giving the
    concatenated text of everything printed so far."""
    mock = MagicMock()
    monkeypatch.setattr(dev, "console", mock)

    def _text() -> str:
        parts = []
        for call in mock.print.call_args_list:
            for a in call.args:
                parts.append(getattr(a, "markup", None) or str(a))
        return "\n".join(parts)

    return _text


@pytest.fixture
def no_reload(monkeypatch):
    calls = []
    monkeypatch.setattr(
        dev,
        "_hot_reload",
        lambda config=None: calls.append(config),
    )
    return calls


@pytest.fixture
def clean_extensions(monkeypatch):
    """All extension reads see empty state; writes are captured."""
    empty = SimpleNamespace(
        providers=[],
        capabilities=[],
        skills=[],
        shell_tools=[],
    )
    monkeypatch.setattr(
        "dashscope_agenticx.extensions.load_extensions",
        lambda: empty,
    )
    return empty


def _config(**kw):
    base = dict(custom_models=[], save_workspace=lambda: None)
    base.update(kw)
    return SimpleNamespace(**base)


def _script_prompts(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr(dev, "_prompt", lambda *a, **k: next(it, ""))


def _script_inputs(monkeypatch, values):
    it = iter(values)
    monkeypatch.setattr("builtins.input", lambda *a, **k: next(it, ""))


# ---------------------------------------------------------------------------
# model registry
# ---------------------------------------------------------------------------


class TestFormatModels:
    def test_renders_table_with_providers(self):
        text = _format_models()
        assert "| Provider | Model |" in text
        for prov in PROVIDER_MODELS:
            assert f"`{prov}`" in text


class TestApplyCustomModels:
    def test_merges_and_skips_malformed(self):
        config = _config(
            custom_models=[
                "openai:gpt-edge-x",  # valid
                "no-colon-entry",  # malformed
                "ghost:model",  # unknown provider
                ":empty",  # empty provider
                "openai:",  # empty model
            ],
        )
        original = list(PROVIDER_MODELS["openai"])
        try:
            _apply_custom_models(config)
            assert "gpt-edge-x" in PROVIDER_MODELS["openai"]
            assert PROVIDER_MODELS["openai"].count("gpt-edge-x") == 1
            # Re-applying must not duplicate.
            _apply_custom_models(config)
            assert PROVIDER_MODELS["openai"].count("gpt-edge-x") == 1
        finally:
            PROVIDER_MODELS["openai"] = original


class TestModelRemove:
    def test_not_custom_rejected(self, out):
        config = _config(custom_models=[])
        _model_remove(config, "openai", "gpt-4o")
        assert "is not a custom model" in out()

    def test_remove_syncs_runtime_registry(self, out):
        config = _config(custom_models=["openai:gpt-edge-y"])
        PROVIDER_MODELS["openai"].append("gpt-edge-y")
        try:
            _model_remove(config, "openai", "gpt-edge-y")
            assert config.custom_models == []
            assert "gpt-edge-y" not in PROVIDER_MODELS["openai"]
            assert "Removed" in out()
        finally:
            if "gpt-edge-y" in PROVIDER_MODELS["openai"]:
                PROVIDER_MODELS["openai"].remove("gpt-edge-y")

    def test_remove_normalizes_case_like_add(self, out):
        """add stores the lowercased name; remove must match it regardless
        of the casing the user types."""
        config = _config(custom_models=["openai:gpt-edge-z"])
        PROVIDER_MODELS["openai"].append("gpt-edge-z")
        try:
            _model_remove(config, "openai", "GPT-Edge-Z")
            assert config.custom_models == []
            assert "gpt-edge-z" not in PROVIDER_MODELS["openai"]
        finally:
            if "gpt-edge-z" in PROVIDER_MODELS["openai"]:
                PROVIDER_MODELS["openai"].remove("gpt-edge-z")


class TestModelList:
    def test_lists_custom_models(self, out):
        _model_list(_config(custom_models=["openai:gpt-x"]))
        assert "openai:gpt-x" in out()


# ---------------------------------------------------------------------------
# /dev dispatch surface
# ---------------------------------------------------------------------------


class TestDispatchSurface:
    def test_bare_dev_prints_index(self, out):
        handle_dev_command("/dev", Config())
        assert "developer / extension guide" in out()

    def test_unknown_subcommand(self, out):
        handle_dev_command("/dev frobnicate", Config())
        assert "Unknown /dev subcommand" in out()

    def test_guides(self, out):
        for sub, marker in [
            ("provider", "Add an LLM Provider"),
            ("platform", "Add a Platform Capability"),
            ("tool", "Add a Local Tool"),
            ("skill", "Add a Skill"),
        ]:
            handle_dev_command(f"/dev {sub}", Config())
            assert marker in out(), sub

    def test_model_usage_when_args_missing(self, out):
        handle_dev_command("/dev model add", Config())
        assert "Usage" in out()

    def test_model_add_shorthand_unknown_prefix(self, out):
        handle_dev_command("/dev model add llama3", Config())
        assert "Cannot infer provider from model name" in out()

    def test_model_add_shorthand_inferred(self, out):
        config = _config()
        original = list(PROVIDER_MODELS["openai"])
        try:
            handle_dev_command("/dev model add gpt-edge-sh", config)
            assert "openai:gpt-edge-sh" in config.custom_models
        finally:
            PROVIDER_MODELS["openai"] = original

    def test_provider_action_usage(self, out):
        handle_dev_command("/dev provider badcmd", Config())
        assert "Usage" in out()

    def test_debug_usage_and_default(self, out):
        handle_dev_command("/dev debug badcmd", Config())
        assert "Usage" in out()

    def test_test_usage(self, out):
        handle_dev_command("/dev test", Config())
        assert "Usage" in out()

    def test_dev_reload_dispatch(self, out, monkeypatch):
        monkeypatch.setattr(dev, "_hot_reload", lambda config=None: None)
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: SimpleNamespace(
                providers=[],
                capabilities=[1],
                skills=[],
                shell_tools=[],
            ),
        )
        handle_dev_command("/dev reload", Config())
        assert "Extensions reloaded" in out()
        assert "capabilities: 1" in out()

    def test_dev_log_dispatch(self, out):
        config = Config()
        handle_dev_command("/dev log", config)
        assert "Registered tool stats" in out()


class TestTestProviderLoopHandling:
    """Regression: /dev test provider used get_event_loop().run_until_complete
    which crashes with 'This event loop is already running' inside the
    REPL/TUI loop."""

    async def test_schedules_on_running_loop(self, monkeypatch):
        mock = AsyncMock()
        monkeypatch.setattr(dev, "_test_provider", mock)
        handle_dev_command("/dev test provider tongyi", Config())
        for _ in range(3):
            import asyncio

            await asyncio.sleep(0)
        assert mock.await_count == 1

    def test_runs_with_private_loop_without_running_loop(self, monkeypatch):
        mock = AsyncMock()
        monkeypatch.setattr(dev, "_test_provider", mock)
        handle_dev_command("/dev test provider tongyi", Config())
        assert mock.await_count == 1


# ---------------------------------------------------------------------------
# interactive helpers
# ---------------------------------------------------------------------------


class TestPromptHelpers:
    def test_prompt_returns_default_on_empty(self, monkeypatch):
        _script_inputs(monkeypatch, [""])
        assert _prompt("标签", default="dflt") == "dflt"

    def test_prompt_secret_uses_getpass(self, monkeypatch):
        monkeypatch.setattr("getpass.getpass", lambda *a, **k: "  s3cr3t  ")
        assert _prompt("key", secret=True) == "s3cr3t"

    def test_choose_target_workspace(self, monkeypatch):
        from dashscope_agenticx.extensions import (
            GLOBAL_EXTENSIONS_FILE,
            WORKSPACE_EXTENSIONS_FILE,
        )

        _script_inputs(monkeypatch, ["2"])
        assert _choose_target() == WORKSPACE_EXTENSIONS_FILE
        _script_inputs(monkeypatch, [""])
        assert _choose_target() == GLOBAL_EXTENSIONS_FILE

    def test_choose_target_defaults_to_workspace_and_reprompts(
        self,
        monkeypatch,
    ):
        from dashscope_agenticx.extensions import WORKSPACE_EXTENSIONS_FILE

        _script_inputs(monkeypatch, ["99", ""])
        assert (
            _choose_target(default_global=False) == WORKSPACE_EXTENSIONS_FILE
        )


# ---------------------------------------------------------------------------
# provider add / list / remove
# ---------------------------------------------------------------------------


class TestProviderAdd:
    def test_empty_name_cancels(self, out, monkeypatch, clean_extensions):
        _script_inputs(monkeypatch, [""])
        dev._provider_add(Config())
        assert "Cancelled" in out()

    def test_builtin_name_conflict(self, out, monkeypatch, clean_extensions):
        _script_inputs(monkeypatch, ["tongyi"])
        dev._provider_add(Config())
        assert "Name conflict" in out()

    def test_extension_name_conflict(self, out, monkeypatch):
        ext = SimpleNamespace(
            providers=[SimpleNamespace(name="moonshot")],
            capabilities=[],
            skills=[],
            shell_tools=[],
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.load_extensions",
            lambda: ext,
        )
        _script_inputs(monkeypatch, ["moonshot"])
        dev._provider_add(Config())
        assert "Name conflict" in out()

    def test_empty_base_url_rejected(self, out, monkeypatch, clean_extensions):
        _script_inputs(monkeypatch, ["newprov", "", ""])
        result = dev._provider_add(Config())
        assert result.status == "cancelled"
        assert "No endpoint entered" in out()

    def test_bad_protocol_reprompted(self, out, monkeypatch, clean_extensions):
        _script_inputs(monkeypatch, ["newprov", "grpc", "openai", ""])
        dev._provider_add(Config())
        assert "Protocol must be" in out()

    def test_invalid_url_reprompted(self, out, monkeypatch, clean_extensions):
        _script_inputs(monkeypatch, ["newprov", "openai", "sssss", ""])
        result = dev._provider_add(Config())
        assert result.status == "cancelled"
        assert "absolute HTTP(S)" in out()

    def test_empty_default_model_cancels(
        self,
        out,
        monkeypatch,
        clean_extensions,
    ):
        _script_inputs(
            monkeypatch,
            ["newprov", "openai", "https://example.com/v1", ""],
        )
        result = dev._provider_add(Config())
        assert result.status == "cancelled"
        assert "No default model" in out()

    def test_env_key_happy_path(
        self,
        out,
        monkeypatch,
        clean_extensions,
        no_reload,
    ):
        captured = {}
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.append_provider",
            lambda target, provider: captured.update(provider=provider),
        )
        monkeypatch.setenv("EDGE_API_KEY", "sk-x")
        _script_inputs(
            monkeypatch,
            [
                "edgeprov",
                "openai",
                "https://api.edge.com/v1",
                "edge-max",
                "y",
                "edge-mini, edge-pro",
                "2",
                "EDGE_API_KEY",
                "2",
            ],
        )
        result = dev._provider_add(Config())
        provider = captured["provider"]
        assert result.status == "completed"
        assert (
            provider.name == "edgeprov"
            and provider.api_key_env == "EDGE_API_KEY"
        )
        assert provider.models == ["edge-mini", "edge-pro"]
        assert not provider.api_key_enc and provider.auth
        assert no_reload

    def test_enc_key_happy_path(
        self,
        out,
        monkeypatch,
        clean_extensions,
        no_reload,
    ):
        captured = {}
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.append_provider",
            lambda target, provider: captured.update(provider=provider),
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.encrypt_for_toml",
            lambda secret: f"ENC:{secret}",
        )
        monkeypatch.setattr("getpass.getpass", lambda prompt: "sk-live")
        _script_inputs(
            monkeypatch,
            [
                "encprov",
                "dashscope",
                "https://api.enc.com/v1",
                "m",
                "n",
                "1",
                "1",
            ],
        )
        result = dev._provider_add(Config())
        provider = captured["provider"]
        assert result.status == "completed"
        assert (
            provider.api_key_enc == "ENC:sk-live" and not provider.api_key_env
        )
        assert provider.protocol == "dashscope"

    def test_no_auth_skips_key_prompts(
        self,
        out,
        monkeypatch,
        clean_extensions,
        no_reload,
    ):
        captured = {}
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.append_provider",
            lambda target, provider: captured.update(provider=provider),
        )
        monkeypatch.setattr(
            "getpass.getpass",
            lambda prompt: pytest.fail("Unexpected secret prompt"),
        )
        _script_inputs(
            monkeypatch,
            [
                "ollama2",
                "openai",
                "http://localhost:11434/v1",
                "llama3",
                "n",
                "0",
                "1",
            ],
        )
        dev._provider_add(Config())
        assert captured["provider"].auth is False


class TestProviderListRemove:
    def test_list_empty(self, out, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: SimpleNamespace(providers=[]),
        )
        dev._provider_list()
        assert "No extension providers registered" in out()

    def test_list_shows_auth_source(self, out, monkeypatch):
        providers = [
            SimpleNamespace(
                name="moon",
                base_url="https://x",
                api_key_env="MOON_KEY",
                api_key_enc="",
                default_model="k2",
                resolved_protocol=lambda: "openai",
                source="global",
            ),
            SimpleNamespace(
                name="encp",
                base_url="https://y",
                api_key_env="",
                api_key_enc="ENC:z",
                default_model="m",
                resolved_protocol=lambda: "dashscope",
                source="workspace",
            ),
        ]
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: SimpleNamespace(providers=providers),
        )
        dev._provider_list()
        text = out()
        assert "env=MOON_KEY" in text
        assert "ENC(machine-local, legacy)" in text

    def test_remove_from_both_files(self, out, monkeypatch, no_reload):
        probed = []

        def _remove(target, name):
            probed.append(target)
            return len(probed) == 1  # workspace file is probed first

        monkeypatch.setattr(
            "dashscope_agenticx.extensions.remove_provider",
            _remove,
        )
        dev._provider_remove("moon")
        assert "Removed provider moon" in out()
        assert len(probed) == 2  # both files are tried
        assert no_reload

    def test_remove_not_found(self, out, monkeypatch, no_reload):
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.remove_provider",
            lambda target, name: False,
        )
        dev._provider_remove("ghost")
        assert "Extension provider not found" in out()
        assert not no_reload


# ---------------------------------------------------------------------------
# capability / skill / tool add+list+remove
# ---------------------------------------------------------------------------


class TestCapabilityFlows:
    def test_add_key_must_have_dot(self, out, monkeypatch, clean_extensions):
        _script_prompts(monkeypatch, ["nodot"])
        dev._capability_add(Config())
        assert "vendor.feature" in out()

    def test_add_existing_rejected(self, out, monkeypatch):
        ext = SimpleNamespace(
            providers=[],
            capabilities=[SimpleNamespace(key="vendor.feat")],
            skills=[],
            shell_tools=[],
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.load_extensions",
            lambda: ext,
        )
        _script_prompts(monkeypatch, ["vendor.feat"])
        dev._capability_add(Config())
        assert "already exists" in out()

    def test_add_writes_scaffold(
        self,
        out,
        monkeypatch,
        clean_extensions,
        no_reload,
    ):
        captured = {}
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.append_capability_scaffold",
            lambda target, key, display: captured.update(
                key=key,
                display=display,
            ),
        )
        _script_prompts(monkeypatch, ["vendor.new", "显示名"])
        _script_inputs(monkeypatch, ["1"])
        config = Config()
        dev._capability_add(config)
        assert captured == {"key": "vendor.new", "display": "显示名"}
        assert no_reload == [config]

    def test_list_and_remove(self, out, monkeypatch, no_reload):
        cap = SimpleNamespace(
            key="vendor.feat",
            display="Feat",
            tools=[
                SimpleNamespace(name="t1", http_method="GET", endpoint="/x"),
            ],
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: SimpleNamespace(capabilities=[cap]),
        )
        dev._capability_list()
        assert "GET /x" in out()

        monkeypatch.setattr(
            "dashscope_agenticx.extensions.remove_capability",
            lambda target, key: True,
        )
        dev._capability_remove("vendor.feat", Config())
        assert "Removed capability vendor.feat" in out()

    def test_remove_not_found(self, out, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.remove_capability",
            lambda target, key: False,
        )
        dev._capability_remove("ghost.cap", Config())
        assert "Extension capability not found" in out()


class TestSkillFlows:
    def test_add_empty_name_cancels(self, out, monkeypatch, clean_extensions):
        _script_prompts(monkeypatch, [""])
        dev._skill_add()
        assert "Cancelled" in out()

    def test_add_conflict(self, out, monkeypatch):
        ext = SimpleNamespace(
            providers=[],
            capabilities=[],
            skills=[SimpleNamespace(name="dup-skill")],
            shell_tools=[],
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.load_extensions",
            lambda: ext,
        )
        _script_prompts(monkeypatch, ["dup-skill"])
        dev._skill_add()
        assert "Name conflict" in out()

    def test_add_empty_template_rejected(
        self,
        out,
        monkeypatch,
        clean_extensions,
    ):
        _script_prompts(monkeypatch, ["edge-skill", "描述", ""])
        dev._skill_add()
        assert "Template must not be empty" in out()

    def test_add_registers_runtime_skill(
        self,
        out,
        monkeypatch,
        clean_extensions,
        no_reload,
    ):
        appended = {}
        registered = {}
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.append_skill",
            lambda target, s: appended.update(skill=s),
        )
        monkeypatch.setattr(
            "dashscope_agenticx.skills.base.register",
            lambda s: registered.update(skill=s),
        )
        _script_prompts(
            monkeypatch,
            ["edge-skill", "描述", "总结 {city} 天气", "city,lang", "time"],
        )
        _script_inputs(monkeypatch, ["1"])
        dev._skill_add()

        s = appended["skill"]
        assert s.arguments == ["city", "lang"]
        assert s.mcp_service == "time"
        assert registered["skill"].name == "edge-skill"
        assert registered["skill"].prompt_template == "总结 {city} 天气"

    def test_list_and_remove(self, out, monkeypatch, no_reload):
        skill = SimpleNamespace(
            name="cs",
            arguments=["a"],
            mcp_service="",
            description="d" * 80,
            prompt_template="t" * 80,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: SimpleNamespace(skills=[skill]),
        )
        dev._skill_list()
        assert "cs" in out()

        monkeypatch.setattr(
            "dashscope_agenticx.extensions.remove_skill",
            lambda target, name: True,
        )
        dev._skill_remove("cs")
        assert "Removed skill cs" in out()


class TestToolFlows:
    def test_add_parses_template_params(
        self,
        out,
        monkeypatch,
        clean_extensions,
        no_reload,
    ):
        captured = {}
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.append_shell_tool",
            lambda target, t: captured.update(tool=t),
        )
        _script_prompts(
            monkeypatch,
            [
                "check_port",  # name
                "检查端口",  # description
                "nc -z {{host}} {{port}}",  # template
                "主机名",  # host param desc
                "端口",  # port param desc
            ],
        )
        _script_inputs(monkeypatch, ["3", "2"])  # dangerous; workspace
        dev._tool_add()

        t = captured["tool"]
        assert t.name == "check_port"
        assert t.permission == "dangerous"
        assert [p["name"] for p in t.params] == ["host", "port"]
        assert t.params[0]["description"] == "主机名"

    def test_add_default_permission_confirm(
        self,
        out,
        monkeypatch,
        clean_extensions,
        no_reload,
    ):
        captured = {}
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.append_shell_tool",
            lambda target, t: captured.update(tool=t),
        )
        _script_prompts(monkeypatch, ["uptime_tool", "运行时间", "uptime"])
        _script_inputs(monkeypatch, ["", "1"])  # default confirm; global
        dev._tool_add()
        assert captured["tool"].permission == "confirm"
        assert captured["tool"].params == []

    def test_add_conflict_and_empty_template(self, out, monkeypatch):
        ext = SimpleNamespace(
            providers=[],
            capabilities=[],
            skills=[],
            shell_tools=[SimpleNamespace(name="dup")],
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.load_extensions",
            lambda: ext,
        )
        _script_prompts(monkeypatch, ["dup"])
        dev._tool_add()
        assert "Name conflict" in out()

        monkeypatch.setattr(
            "dashscope_agenticx.extensions.load_extensions",
            lambda: SimpleNamespace(
                providers=[],
                capabilities=[],
                skills=[],
                shell_tools=[],
            ),
        )
        _script_prompts(monkeypatch, ["newtool", "desc", ""])
        dev._tool_add()
        assert "Command template must not be empty" in out()

    def test_list_and_remove(self, out, monkeypatch, no_reload):
        tool = SimpleNamespace(
            name="st",
            permission="confirm",
            description="d",
            command_template="c",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: SimpleNamespace(shell_tools=[tool]),
        )
        dev._tool_list()
        assert "st" in out()

        monkeypatch.setattr(
            "dashscope_agenticx.extensions.remove_shell_tool",
            lambda target, name: False,
        )
        dev._tool_remove("ghost")
        assert "Custom shell tool not found" in out()


# ---------------------------------------------------------------------------
# debug utilities
# ---------------------------------------------------------------------------


class TestDebugUtilities:
    def test_debug_tools_lists_registered(self, out):
        _debug_tools()
        assert "Registered tools" in out()

    def test_debug_schema_unknown_tool(self, out):
        _debug_schema("ghost_tool_xyz")
        assert "does not exist" in out()

    def test_debug_call_unknown_tool(self, out):
        import asyncio

        asyncio.run(
            _debug_call(["/dev", "debug", "call", "ghost_tool_xyz", "{}"]),
        )
        assert "does not exist" in out()

    def test_debug_call_bad_json(self, out, monkeypatch):
        import asyncio

        from dashscope_agenticx.tools.registry import (
            PermissionLevel,
            ToolDefinition,
            registry,
        )

        registry._tools["edge_sync_tool"] = ToolDefinition(
            name="edge_sync_tool",
            description="t",
            permission=PermissionLevel.AUTO,
            func=lambda **kw: "sync-ok",
            parameters={},
        )
        try:
            asyncio.run(
                _debug_call(
                    ["/dev", "debug", "call", "edge_sync_tool", "{bad json"],
                ),
            )
            assert "Failed to parse args JSON" in out()

            asyncio.run(
                _debug_call(["/dev", "debug", "call", "edge_sync_tool"]),
            )
            assert "sync-ok" in out()
        finally:
            registry._tools.pop("edge_sync_tool", None)

    def test_debug_call_failing_tool(self, out):
        import asyncio

        from dashscope_agenticx.tools.registry import (
            PermissionLevel,
            ToolDefinition,
            registry,
        )

        def _boom(**kw):
            raise ValueError("kaput")

        registry._tools["edge_boom_tool"] = ToolDefinition(
            name="edge_boom_tool",
            description="t",
            permission=PermissionLevel.AUTO,
            func=_boom,
            parameters={},
        )
        try:
            asyncio.run(
                _debug_call(["/dev", "debug", "call", "edge_boom_tool"]),
            )
            assert "Call failed" in out() and "kaput" in out()
        finally:
            registry._tools.pop("edge_boom_tool", None)

    def test_debug_prompt(self, out):
        config = Config()
        dev._debug_prompt(config)
        assert "Current system prompt" in out()


class TestTestProviderErrors:
    async def test_no_model(self, out, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: None,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.providers.build_profiles_from_config",
            lambda cfg: [SimpleNamespace(api_key="k", model="")],
        )
        await _test_provider("ghostprov", Config())
        assert "has no model configured" in out()

    async def test_no_key_with_extension_hint(self, out, monkeypatch):
        ext = SimpleNamespace(
            api_key_env="EDGE_KEY",
            default_model="m",
            resolved_models=lambda: ["m"],
        )
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: ext,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.providers.build_profiles_from_config",
            lambda cfg: [SimpleNamespace(api_key="", model="m")],
        )
        await _test_provider("edgep", Config())
        text = out()
        assert "has no API Key configured" in text
        assert "EDGE_KEY" in text

    async def test_no_key_builtin(self, out, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: None,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.providers.build_profiles_from_config",
            lambda cfg: [SimpleNamespace(api_key="", model="m")],
        )
        await _test_provider("tongyi", Config())
        assert "has no API Key configured" in out()

    async def test_create_provider_raises(self, out, monkeypatch):
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.find_provider",
            lambda name: None,
        )
        monkeypatch.setattr(
            "dashscope_agenticx.providers.build_profiles_from_config",
            lambda cfg: [SimpleNamespace(api_key="k", model="m")],
        )

        def _raise(profile):
            raise RuntimeError("boom")

        monkeypatch.setattr(
            "dashscope_agenticx.providers._create_provider",
            _raise,
        )
        await _test_provider("tongyi", Config())
        assert "connection failed" in out() and "boom" in out()


# ---------------------------------------------------------------------------
# reload / log
# ---------------------------------------------------------------------------


class TestReloadAndLog:
    def test_dev_reload_counts(self, out, monkeypatch):
        monkeypatch.setattr(dev, "_hot_reload", lambda config=None: None)
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.current",
            lambda: SimpleNamespace(
                providers=[1, 2],
                capabilities=[],
                skills=[1],
                shell_tools=[],
            ),
        )
        _dev_reload(Config())
        text = out()
        assert "providers: 2" in text and "skills: 1" in text

    def test_dev_log_groups_by_permission(self, out):
        _dev_log(Config())
        assert "Registered tool stats" in out()


# ---------------------------------------------------------------------------
# help menu completeness (commands.py)
# ---------------------------------------------------------------------------


class TestHelpMenu:
    def test_dev_surface_fully_listed(self):
        from dashscope_agenticx.commands import render_help_text

        text = render_help_text()
        for marker in [
            "/dev model add",
            "/dev provider add",
            "/dev capability add",
            "/dev skill add",
            "/dev tool add",
            "/dev debug",
            "/dev test provider",
            "/expert download",
            "/expert restore",
        ]:
            assert marker in text, marker


class TestShellEscape:
    def test_success_captures_output(self):
        from dashscope_agenticx.commands import handle_shell_escape

        stdout, stderr, rc = handle_shell_escape("echo hello")
        assert stdout.strip() == "hello"
        assert rc == 0

    def test_failure_rc_propagates(self):
        from dashscope_agenticx.commands import handle_shell_escape

        _, _, rc = handle_shell_escape("exit 3")
        assert rc == 3

    def test_exception_returns_rc1(self, monkeypatch):
        import dashscope_agenticx.commands as commands_mod

        monkeypatch.setattr(
            commands_mod._sp,
            "run",
            lambda *a, **k: (_ for _ in ()).throw(OSError("no shell")),
        )
        stdout, stderr, rc = commands_mod.handle_shell_escape("whatever")
        assert rc == 1
        assert "no shell" in stderr
