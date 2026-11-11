# -*- coding: utf-8 -*-
from __future__ import annotations

import types

import pytest

import dashscope_agenticx.tools.platform as platform_mod
from dashscope_agenticx.cli import runners
from dashscope_agenticx.cli.handlers_setup import (
    _ensure_provider_key,
    _handle_setup,
    _setup_expert,
    _setup_finalize,
    _setup_preset_custom,
)
from dashscope_agenticx.cli.mcp import _connect_mcp
from dashscope_agenticx.cli.startup import _needs_workspace_setup
from dashscope_agenticx.config import Config


@pytest.fixture
def wizard_paths(monkeypatch, tmp_path):
    import dashscope_agenticx.config as config_module
    import dashscope_agenticx.extensions as extensions
    import dashscope_agenticx.skills.package as packages
    import dashscope_agenticx.skills.store as store

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    monkeypatch.chdir(workspace)
    workspace_dir = workspace / ".agenticx"
    global_dir = tmp_path / "global"
    monkeypatch.setattr(config_module, "CONFIG_DIR", global_dir)
    monkeypatch.setattr(
        config_module,
        "CONFIG_FILE",
        global_dir / "config.toml",
    )
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", workspace_dir)
    monkeypatch.setattr(
        config_module,
        "WORKSPACE_CONFIG_FILE",
        workspace_dir / "config.toml",
    )
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
    monkeypatch.setattr(extensions, "_current", extensions.CustomExtensions())
    monkeypatch.setattr(packages, "CONFIG_DIR", global_dir)
    monkeypatch.setattr(store, "CONFIG_DIR", global_dir)
    monkeypatch.setattr(store, "WORKSPACE_DIR", workspace_dir)
    return workspace_dir


async def test_setup_applies_template_to_owning_runtime(
    monkeypatch,
    tmp_path,
    wizard_paths,
):
    import dashscope_agenticx.cli.handlers_provider as provider_handler
    import dashscope_agenticx.dev as dev
    import dashscope_agenticx.extensions as extensions
    from dashscope_agenticx.agent import Agent
    from dashscope_agenticx.executor import Executor
    from dashscope_agenticx.prompt_pipeline import PromptContext
    from dashscope_agenticx.runtime import Runtime, RuntimeContext
    from dashscope_agenticx.skills.manager import SkillManager
    from dashscope_agenticx.tools.registry import (
        ToolDefinition,
        ToolRegistry,
        registry,
    )
    from dashscope_agenticx.utils.toml import load_toml

    wizard_paths.mkdir()
    target = wizard_paths / "custom-extensions.toml"
    target.write_text(
        '[[providers]]\nname = "setup-local"\nbase_url = "https://configured.example/v1"\n'
        'default_model = "configured-model"\nprotocol = "anthropic"\napi_key_env = "SETUP_LOCAL_KEY"\n',
    )
    extensions._current = extensions.load_extensions()
    models = {"tongyi": ["qwen3.8-max"]}
    monkeypatch.setattr(provider_handler, "PROVIDER_MODELS", models)
    monkeypatch.setattr(dev, "PROVIDER_MODELS", models)
    monkeypatch.setenv("SETUP_LOCAL_KEY", "sk-runtime-test")
    template = tmp_path / "apps" / "basic-chat" / ".agenticx"
    package = template / "skills" / "setup-package"
    package.mkdir(parents=True)
    (template / "config.toml").write_text(
        'provider = "openai"\nuser_name = "template-user"\n',
    )
    (template / "system-prompt.md").write_text(
        "Template role applies immediately.",
    )
    (template / "custom-extensions.toml").write_text(
        '[[providers]]\nname = "setup-local"\nbase_url = "https://wrong.example/v1"\nauth = false\n',
    )
    (package / "skill.toml").write_text(
        'name = "setup-package"\nversion = "1.0.0"\nalways_active = true\n',
    )
    (package / "prompt.md").write_text("Template package instructions.")
    monkeypatch.setattr(
        "dashscope_agenticx.cli.experts._get_experts_dir",
        lambda: tmp_path / "apps",
    )
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    answers = iter(
        ["runtime-user", "2", "", "", "n", "local.subagent", "1", "y"],
    )
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        "getpass.getpass",
        lambda prompt: pytest.fail("Resolved key was asked again"),
    )
    config = Config(
        provider="setup-local",
        model="configured-model",
        protocol="anthropic",
        base_url="https://configured.example/v1",
        enabled_capabilities=[],
        memory_enabled=False,
    )
    tool_registry = ToolRegistry()
    tool_registry.register(
        ToolDefinition(name="old_setup_tool", description="old capability"),
    )
    tracker = {"old.cap": {"old_setup_tool"}}
    manager = SkillManager(tool_registry=tool_registry)
    original_provider = object()
    updated_provider = object()
    agent = Agent(
        original_provider,
        Executor(),
        tool_registry=tool_registry,
        skill_manager=manager,
        capability_tools=tracker,
        runtime_config=config,
        system_prompt="Old role.",
    )
    runtime = Runtime(
        config,
        original_provider,
        agent.executor,
        RuntimeContext(
            tool_registry=tool_registry,
            skill_manager=manager,
            capability_tools=tracker,
        ),
    )
    agent.runtime = runtime
    agent.messages.append(
        {"role": "user", "content": "Keep this conversation."},
    )
    history = list(agent.messages)
    extra = types.SimpleNamespace(
        name="setup-extra",
        render=lambda ctx: "Preserved extra section.",
    )
    agent._prompt_pipeline.add_ephemeral(extra)
    monkeypatch.setattr(
        "dashscope_agenticx.providers.get_provider_chain",
        lambda cfg: updated_provider,
    )
    original_global_tracker = dict(platform_mod.current_capability_tools())

    result = await _handle_setup(config, agent)

    assert result.status == "completed"
    assert agent.provider is runtime.provider is updated_provider
    assert runtime.config is agent.runtime_config is config
    assert agent.messages == history
    assert agent.user_name == "runtime-user"
    assert tool_registry.get("subagent_invoke") is not None
    assert tool_registry.get("old_setup_tool") is None
    assert "local.subagent" in tracker and "old.cap" not in tracker
    assert registry.get("setup_template_tool") is None
    assert platform_mod.current_capability_tools() == original_global_tracker
    assert [package.name for package in manager.packages] == ["setup-package"]
    prompt = agent._prompt_pipeline.render(PromptContext(user_input="hello"))
    assert "Template role applies immediately." in prompt
    assert "Template package instructions." in prompt
    assert "Preserved extra section." in prompt
    assert "Old role." not in prompt
    assert load_toml(wizard_paths / "config.toml")["provider"] == "setup-local"
    assert (
        load_toml(target)["providers"][0]["api_key_env"] == "SETUP_LOCAL_KEY"
    )


@pytest.mark.parametrize("secret", ["sk-cap-test", ""])
def test_setup_deduplicates_shared_capability_keys(
    monkeypatch,
    wizard_paths,
    secret,
):
    import dashscope_agenticx.extensions as extensions
    from dashscope_agenticx.cli.handlers_key import (
        _maybe_prompt_extension_token,
    )
    from dashscope_agenticx.cli.wizard import WizardContext
    from dashscope_agenticx.utils.secret_store import resolve_secret

    wizard_paths.mkdir()
    target = wizard_paths / "custom-extensions.toml"
    target.write_text(
        '[[capabilities]]\nkey = "setup.first"\nauth = "bearer:$SETUP_SHARED_KEY"\n'
        '[[capabilities]]\nkey = "setup.second"\nauth = "bearer:$SETUP_SHARED_KEY"\n',
    )
    monkeypatch.delenv("SETUP_SHARED_KEY", raising=False)
    extensions._current = extensions.load_extensions()
    calls = []
    monkeypatch.setattr(
        "getpass.getpass",
        lambda prompt: calls.append(prompt) or secret,
    )
    context = WizardContext(setup=True)
    assert _maybe_prompt_extension_token(
        "setup.first",
        Config(),
        context=context,
    ) is bool(secret)
    assert _maybe_prompt_extension_token(
        "setup.second",
        Config(),
        context=context,
    ) is bool(secret)
    assert len(calls) == 1
    loaded = extensions.load_extensions()
    assert not loaded.errors
    if secret:
        references = [cap.api_key_enc for cap in loaded.capabilities]
        assert references[0] == references[1]
        assert all(
            reference.startswith("KEYRING:") for reference in references
        )
        assert all(
            resolve_secret(reference) == secret for reference in references
        )
        assert secret not in target.read_text()
    else:
        assert "SETUP_SHARED_KEY" in context.deferred_credentials


def test_maas_key_does_not_suppress_bailian_credential(monkeypatch):
    from dashscope_agenticx.cli.handlers_capability import _enable_capability
    from dashscope_agenticx.cli.handlers_provider import _wizard_key_step
    from dashscope_agenticx.cli.wizard import WizardContext

    config = Config(
        provider="tongyi",
        workspace="test",
        region="cn-test",
        maas_api_key="sk-maas",
        tongyi_api_key="",
        enabled_capabilities=[],
    )
    context = WizardContext(setup=True)
    assert _wizard_key_step(config, context=context).status == "completed"
    assert "maas_api_key" in context.prompted_credentials
    assert "tongyi_api_key" not in context.prompted_credentials
    calls = []
    monkeypatch.setattr(
        "getpass.getpass",
        lambda prompt: calls.append(prompt) or "sk-bailian",
    )
    assert (
        _enable_capability(config, "bailian.mcp", context=context).status
        == "completed"
    )
    assert config.tongyi_api_key == "sk-bailian"
    assert len(calls) == 1


def test_setup_reuses_extension_provider_key_at_runtime(
    monkeypatch,
    wizard_paths,
):
    import dashscope_agenticx.extensions as extensions
    from dashscope_agenticx.cli.handlers_key import (
        _maybe_prompt_extension_token,
    )
    from dashscope_agenticx.cli.wizard import WizardContext
    from dashscope_agenticx.tools.registry import (
        ToolRegistry,
        bind_tool_registry,
    )

    wizard_paths.mkdir()
    reference = extensions.encrypt_for_toml("sk-shared-provider")
    (wizard_paths / "custom-extensions.toml").write_text(
        '[[providers]]\nname = "setup-provider"\nbase_url = "https://provider.example/v1"\n'
        f'api_key_env = "SETUP_PROVIDER_KEY"\napi_key = "{reference}"\n'
        '[[capabilities]]\nkey = "setup.http"\nauth = "bearer:$SETUP_PROVIDER_KEY"\n'
        '[[capabilities.tools]]\nname = "request"\nendpoint = "https://api.example/v1"\n',
    )
    monkeypatch.delenv("SETUP_PROVIDER_KEY", raising=False)
    extensions._current = extensions.load_extensions()
    monkeypatch.setattr(
        "getpass.getpass",
        lambda prompt: pytest.fail("Provider key was asked twice"),
    )
    config = Config(
        provider="setup-provider",
        enabled_capabilities=["setup.http"],
    )
    assert _maybe_prompt_extension_token(
        "setup.http",
        config,
        context=WizardContext(setup=True),
    )
    tool_registry = ToolRegistry()
    with bind_tool_registry(tool_registry), platform_mod.bind_capability_tools(
        {},
    ):
        platform_mod.register_one_capability(config, "setup.http")
    cap = extensions.find_capability("setup.http")
    assert cap.runtime_key == cap.resolve_auth_key() == "sk-shared-provider"
    assert tool_registry.get("setup_http_request") is not None


def test_setup_summary_separates_pending_capabilities(monkeypatch, capsys):
    import dashscope_agenticx.extensions as extensions
    from dashscope_agenticx.cli.wizard import WizardContext, WizardResult

    provider = extensions.CustomProvider(
        name="setup-noauth",
        base_url="https://local.example/v1",
        auth=False,
    )
    monkeypatch.setattr(
        extensions,
        "_current",
        extensions.CustomExtensions(providers=[provider]),
    )
    monkeypatch.setattr(
        "dashscope_agenticx.providers.get_provider_chain",
        lambda config: object(),
    )
    registered = []
    monkeypatch.setattr(
        platform_mod,
        "register_platform_tools",
        lambda config, **kwargs: registered.append(
            list(config.enabled_capabilities),
        ),
    )
    config = Config(
        provider="setup-noauth",
        enabled_capabilities=["bailian.mcp"],
    )
    config.save_global = lambda: None
    config.save_workspace = lambda: None
    context = WizardContext(
        setup=True,
        pending_capabilities={"bailian.mcp"},
        results=[WizardResult("skipped", "expert")],
    )
    with platform_mod.bind_capability_tools({}):
        _setup_finalize(config, types.SimpleNamespace(), context=context)
    output = capsys.readouterr().out
    assert "Capabilities: (none)" in output
    assert "Pending capabilities: bailian.mcp" in output
    assert "API Key: not required" in output
    assert "Expert: skipped" in output
    assert registered == [[]]
    assert config.enabled_capabilities == ["bailian.mcp"]


@pytest.mark.parametrize("api_key", ["", "sk-default-secret"])
@pytest.mark.parametrize("choice", ["", "1"])
@pytest.mark.parametrize("expert", ["", "basic-chat"])
async def test_default_setup_selects_expert_separately(
    monkeypatch,
    wizard_paths,
    capsys,
    api_key,
    choice,
    expert,
):
    from dashscope_agenticx.utils.toml import load_toml

    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)
    prompts = []
    answers = iter(["", choice, expert])

    def read_input(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", read_input)
    monkeypatch.setattr(
        "getpass.getpass",
        lambda prompt: pytest.fail("Default setup prompted for a secret"),
    )
    downloads = []

    def download(args, **kwargs):
        downloads.append((args, kwargs))
        return True

    monkeypatch.setattr(
        "dashscope_agenticx.cli.experts._handle_expert_command",
        download,
    )
    monkeypatch.setattr("dashscope_agenticx.dev._hot_reload", lambda: None)
    monkeypatch.setattr(
        "dashscope_agenticx.providers.get_provider_chain",
        lambda cfg: object(),
    )
    registered = []
    monkeypatch.setattr(
        platform_mod,
        "register_platform_tools",
        lambda cfg, **kwargs: registered.append(
            list(cfg.enabled_capabilities),
        ),
    )
    config = Config(tongyi_api_key=api_key)
    with platform_mod.bind_capability_tools({}):
        result = await _handle_setup(config, types.SimpleNamespace())

    assert result.status == ("completed" if api_key else "deferred")
    assert prompts == [
        "Username [anonymous]: ",
        "Choice [1]: ",
        "Expert [0]: ",
    ]
    assert downloads == (
        [
            (
                ["download", expert],
                {"show_usage": False, "preserve_configuration": True},
            ),
        ]
        if expert
        else []
    )
    assert (
        config.user_name,
        config.provider,
        config.model,
        config.protocol,
    ) == (
        "anonymous",
        "tongyi",
        "qwen3.8-flash",
        "openai",
    )
    assert config.enabled_capabilities == ["bailian.mcp", "bailian.cli"]
    assert registered == [
        ["bailian.mcp", "bailian.cli"] if api_key else ["bailian.cli"],
    ]
    assert load_toml(wizard_paths / "config.toml")["user_name"] == "anonymous"
    output = capsys.readouterr().out
    assert (
        output.index("Default configuration")
        < output.index("Experts (optional)")
        < output.index("Setup complete")
    )
    assert "Provider: tongyi" in output and "Model: qwen3.8-flash" in output
    assert "Protocol: openai" in output and "API base URL:" in output
    assert "Experts are selected separately" in output
    assert f"Expert: {expert or 'skipped'}" in output
    assert (
        "basic-chat" in output
        and "dashscope-sdk-expert" in output
        and "bailian-expert" in output
    )
    assert "Key source" not in output and "Available models" not in output
    assert "Capabilities (optional)" not in output
    assert (
        "Pending capabilities: bailian.mcp" in output
        if not api_key
        else "value hidden" in output
    )
    assert "sk-default-secret" not in output


def test_default_configuration_preview_does_not_mutate_config(
    monkeypatch,
    capsys,
):
    from dashscope_agenticx.cli.handlers_setup import (
        _show_default_configuration,
    )
    from dashscope_agenticx.providers.profile import ProviderProfile

    config = Config(
        user_name="alice",
        provider="anthropic",
        model="custom-model",
        protocol="anthropic",
        base_url="https://custom.example/v1",
        enabled_capabilities=[],
    )
    profile = ProviderProfile(
        name="tongyi",
        provider="tongyi",
        model="qwen3.8-max",
        api_key="sk-preview-secret",
        base_url="https://user:password@api.example/v1?api_key=secret",
    )
    monkeypatch.setattr(
        "dashscope_agenticx.providers.profile.build_profiles_from_config",
        lambda cfg: [profile],
    )
    _show_default_configuration(config)
    assert (
        config.provider,
        config.model,
        config.protocol,
        config.base_url,
    ) == (
        "anthropic",
        "custom-model",
        "anthropic",
        "https://custom.example/v1",
    )
    assert config.enabled_capabilities == []
    output = capsys.readouterr().out
    assert "Username: alice" in output
    assert "https://api.example/v1" in output
    assert "password" not in output and "sk-preview-secret" not in output


def test_setup_finalize_passes_connect_mcp_fn(monkeypatch):
    """Regression: the /setup completion path re-registered platform tools
    without connect_mcp_fn, so the mcp_connect tool went missing."""
    captured = {}

    def spy_register(config, connect_mcp_fn=None):
        captured["connect_mcp_fn"] = connect_mcp_fn

    monkeypatch.setattr(platform_mod, "register_platform_tools", spy_register)
    monkeypatch.setattr(
        platform_mod,
        "unregister_capability_tools",
        lambda cap_key: None,
    )

    config = Config()
    config.save_global = lambda: None
    config.save_workspace = lambda: None
    agent = types.SimpleNamespace()

    _setup_finalize(config, agent)

    assert captured.get("connect_mcp_fn") is _connect_mcp


@pytest.mark.parametrize(
    "choice,expected",
    [
        ("", None),
        ("0", None),
        ("1", "basic-chat"),
        ("dashscope-sdk-expert", "dashscope-sdk-expert"),
        ("99", None),
    ],
)
def test_setup_expert_menu(monkeypatch, capsys, choice, expected):
    calls = []
    answers = iter([choice, "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))

    def download(args, **kwargs):
        calls.append((args, kwargs))
        return True

    monkeypatch.setattr(
        "dashscope_agenticx.cli.experts._handle_expert_command",
        download,
    )
    result = _setup_expert()
    assert calls == (
        [
            (
                ["download", expected],
                {"show_usage": False, "preserve_configuration": True},
            ),
        ]
        if expected
        else []
    )
    assert result.status == ("completed" if expected else "skipped")
    output = capsys.readouterr().out
    assert "Experts (optional)" in output
    assert "Skip (default)" in output
    assert "basic-chat" in output and "bailian-expert" in output
    assert "/expert" not in output
    assert "agenticx expert" not in output


def test_setup_expert_enter_skips_without_download(monkeypatch, capsys):
    from dashscope_agenticx.cli.wizard import WizardContext

    prompts = []

    def read_input(prompt):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", read_input)
    monkeypatch.setattr(
        "dashscope_agenticx.cli.experts._handle_expert_command",
        lambda *args, **kwargs: pytest.fail("Default expert downloaded"),
    )
    context = WizardContext(setup=True)
    result = _setup_expert(context=context)
    assert result.status == "skipped" and result.name == "expert"
    assert context.results == [result]
    assert prompts == ["Expert [0]: "]
    assert "Experts (optional)" in capsys.readouterr().out


@pytest.mark.parametrize("choice", ["", "1", "2"])
async def test_setup_orders_shared_handlers_before_one_finalize(
    monkeypatch,
    capsys,
    choice,
):
    import dashscope_agenticx.cli.handlers_setup as setup
    from dashscope_agenticx.cli.wizard import WizardResult

    calls = []
    monkeypatch.setattr("builtins.input", lambda prompt: choice)
    monkeypatch.setattr(setup, "_setup_user_name", lambda cfg: None)

    def provider(cmd, agent, cfg, **kwargs):
        assert cmd == "/provider" and kwargs["context"].setup
        calls.append("provider")
        return WizardResult("completed", cfg.provider)

    def capabilities(cmd, cfg, **kwargs):
        assert cmd == "/capability" and kwargs["context"].setup
        calls.append("capabilities")
        return WizardResult("completed", "capabilities")

    monkeypatch.setattr(
        "dashscope_agenticx.cli.handlers_provider.handle_provider_command",
        provider,
    )
    monkeypatch.setattr(
        "dashscope_agenticx.cli.handlers_capability._handle_capability_command",
        capabilities,
    )
    monkeypatch.setattr(
        setup,
        "_setup_finalize",
        lambda cfg, agent, **kwargs: calls.append("save"),
    )
    monkeypatch.setattr(
        setup,
        "_setup_expert",
        lambda **kwargs: calls.append("expert"),
    )
    monkeypatch.setattr(
        "dashscope_agenticx.dev._hot_reload",
        lambda: calls.append("reload"),
    )
    result = await _handle_setup(Config(), types.SimpleNamespace())
    assert result.handled
    assert calls == ["provider", "capabilities", "expert", "reload", "save"]
    output = capsys.readouterr().out
    assert "China general" not in output and "[3]" not in output
    assert (
        "/provider" not in output
        and "/capability" not in output
        and "/expert" not in output
    )


@pytest.mark.parametrize("add_choice", ["", "n", "y"])
async def test_setup_unknown_provider_can_be_reselected(
    monkeypatch,
    capsys,
    add_choice,
):
    import dashscope_agenticx.cli.handlers_provider as provider_handler
    from dashscope_agenticx.cli.wizard import WizardResult
    from dashscope_agenticx.extensions import CustomExtensions

    monkeypatch.setattr(
        "dashscope_agenticx.extensions._current",
        CustomExtensions(),
    )
    monkeypatch.setattr(
        provider_handler,
        "PROVIDER_MODELS",
        {"tongyi": ["qwen3.8-max"], "anthropic": ["claude-test"]},
    )
    answers = iter(["unlisted", add_choice, "anthropic", "", "n", "0"])
    monkeypatch.setattr("builtins.input", lambda prompt: next(answers))
    monkeypatch.setattr(
        provider_handler,
        "_wizard_key_step",
        lambda cfg, **kwargs: WizardResult("completed"),
    )
    additions = []

    def add(cfg, **kwargs):
        additions.append(kwargs)
        return WizardResult("cancelled")

    monkeypatch.setattr("dashscope_agenticx.dev._provider_add", add)
    config = Config(base_url="https://old.example/v1")
    await _setup_preset_custom(config)
    assert (
        config.provider,
        config.model,
        config.protocol,
        config.base_url,
    ) == ("anthropic", "claude-test", "anthropic", "")
    assert len(additions) == (1 if add_choice == "y" else 0)
    if additions:
        assert additions[0]["name"] == "unlisted"
        assert additions[0]["show_usage"] is False
        assert additions[0]["context"].setup
    assert "Choose a listed provider" in capsys.readouterr().out


@pytest.mark.parametrize(
    "key_source",
    ["no_auth", "env", "keyring", "deferred"],
)
async def test_setup_adds_provider_and_uses_its_profile(
    monkeypatch,
    tmp_path,
    capsys,
    key_source,
):
    import dashscope_agenticx.cli.handlers_provider as provider_handler
    import dashscope_agenticx.dev as dev
    import dashscope_agenticx.extensions as extensions
    from dashscope_agenticx.cli.wizard import WizardContext
    from dashscope_agenticx.providers.profile import build_profiles_from_config

    target = tmp_path / ".agenticx" / "custom-extensions.toml"
    monkeypatch.setattr(
        extensions,
        "GLOBAL_EXTENSIONS_FILE",
        tmp_path / "global.toml",
    )
    monkeypatch.setattr(extensions, "WORKSPACE_EXTENSIONS_FILE", target)
    monkeypatch.setattr(extensions, "_current", extensions.CustomExtensions())
    models = {"tongyi": ["qwen3.8-max"]}
    monkeypatch.setattr(provider_handler, "PROVIDER_MODELS", models)
    monkeypatch.setattr(dev, "PROVIDER_MODELS", models)

    def reload_extensions():
        extensions._current = extensions.load_extensions()

    monkeypatch.setattr(dev, "_hot_reload", reload_extensions)
    key_answers = {
        "no_auth": ["0"],
        "env": ["2", "LOCAL_TEST_API_KEY"],
        "keyring": ["1"],
        "deferred": ["1"],
    }
    if key_source == "env":
        monkeypatch.setenv("LOCAL_TEST_API_KEY", "sk-new-provider")
    else:
        monkeypatch.delenv("LOCAL_TEST_API_KEY", raising=False)
    monkeypatch.delenv("LOCAL_TEST_API_KEY_DEFAULT", raising=False)
    monkeypatch.setattr(
        "getpass.getpass",
        lambda prompt: "" if key_source == "deferred" else "sk-new-provider",
    )
    answers = iter(
        [
            "local-test",
            "y",
            "anthropic",
            "https://local.example/v1",
            "local-model",
            "n",
            *key_answers[key_source],
            "0",
        ],
    )
    prompts = []

    def read_input(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", read_input)
    config = Config(base_url="https://old.example/v1")
    context = WizardContext(setup=True)
    result = await _setup_preset_custom(config, context=context)
    assert result.handled
    assert target.is_file()
    assert (
        config.provider,
        config.model,
        config.protocol,
        config.base_url,
    ) == ("local-test", "local-model", "anthropic", "")
    profile = build_profiles_from_config(config)[0]
    assert profile.base_url == "https://local.example/v1"
    if key_source in ("env", "keyring"):
        assert profile.api_key == "sk-new-provider"
    if key_source == "deferred":
        assert "local-test_api_key" in context.deferred_credentials
        assert not profile.api_key
    assert prompts[-1] == "Capabilities [0]: "
    assert not any(prompt.startswith("Model [") for prompt in prompts)
    assert sum(prompt.startswith("Key source") for prompt in prompts) == 1
    output = capsys.readouterr().out
    assert "Add Provider" in output and "Available models:" not in output
    assert "/provider" not in output and "/dev" not in output


@pytest.mark.parametrize("key_source", ["env", "keyring", "no_auth"])
def test_setup_skips_resolved_extension_key(monkeypatch, key_source):
    from dashscope_agenticx.extensions import CustomExtensions, CustomProvider

    provider = CustomProvider(
        name="extension-test",
        base_url="https://extension.example/v1",
        api_key_env="EXTENSION_TEST_KEY",
        auth=key_source != "no_auth",
    )
    monkeypatch.setattr(
        "dashscope_agenticx.extensions._current",
        CustomExtensions(providers=[provider]),
    )
    if key_source == "env":
        monkeypatch.setenv("EXTENSION_TEST_KEY", "sk-test")
    elif key_source == "keyring":
        provider.api_key_enc = "keyring-reference"
        monkeypatch.setattr(
            "dashscope_agenticx.extensions.resolve_secret",
            lambda value: "sk-test",
        )
        monkeypatch.delenv("EXTENSION_TEST_KEY", raising=False)
    monkeypatch.setattr(
        "dashscope_agenticx.cli.handlers_provider._read_secret",
        lambda *args: pytest.fail("Unexpected API Key prompt"),
    )
    _ensure_provider_key(Config(provider="extension-test"))


def test_new_provider_deferred_key_is_not_reprompted_by_capability(
    monkeypatch,
    capsys,
):
    from dashscope_agenticx.cli.handlers_key import (
        _maybe_prompt_extension_token,
    )
    from dashscope_agenticx.cli.handlers_provider import _wizard_key_step
    from dashscope_agenticx.cli.wizard import WizardContext
    from dashscope_agenticx.extensions import (
        CustomCapability,
        CustomExtensions,
        CustomProvider,
    )

    provider = CustomProvider(
        name="deferred-test",
        base_url="https://local.example/v1",
    )
    cap = CustomCapability(
        key="deferred.http",
        auth="bearer:$DEFERRED_TEST_API_KEY",
    )
    monkeypatch.setattr(
        "dashscope_agenticx.extensions._current",
        CustomExtensions(capabilities=[cap]),
    )
    monkeypatch.delenv("DEFERRED_TEST_API_KEY", raising=False)
    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    calls = []
    monkeypatch.setattr(
        "getpass.getpass",
        lambda prompt: calls.append(prompt) or "",
    )
    context = WizardContext(setup=True)
    config = Config()
    assert (
        _wizard_key_step(config, context=context, new_provider=provider).status
        == "deferred"
    )
    assert not _maybe_prompt_extension_token(cap.key, config, context=context)
    assert len(calls) == 1
    assert "DEFERRED_TEST_API_KEY" in context.deferred_credentials
    assert "DEFERRED_TEST_API_KEY" in capsys.readouterr().out


def test_setup_persists_extension_key_with_existing_secret_handler(
    monkeypatch,
):
    from dashscope_agenticx.extensions import CustomExtensions, CustomProvider

    provider = CustomProvider(
        name="extension-test",
        base_url="https://extension.example/v1",
    )
    monkeypatch.setattr(
        "dashscope_agenticx.extensions._current",
        CustomExtensions(providers=[provider]),
    )
    monkeypatch.setattr("builtins.input", lambda prompt: "1")
    monkeypatch.setattr(
        "dashscope_agenticx.cli.handlers_provider._read_secret",
        lambda *args: "sk-new-key",
    )
    captured = []

    def save(ext, cfg, **kwargs):
        captured.append((ext, cfg, kwargs))
        return True

    monkeypatch.setattr(
        "dashscope_agenticx.cli.handlers_key._set_extension_provider_token",
        save,
    )
    config = Config(provider="extension-test")
    _ensure_provider_key(config)
    assert captured == [
        (
            provider,
            config,
            {
                "direct_value": "sk-new-key",
                "persist": False,
                "announce": False,
            },
        ),
    ]


async def test_setup_cancel_does_not_finalize(monkeypatch, capsys):
    monkeypatch.setattr(
        "builtins.input",
        lambda prompt: (_ for _ in ()).throw(KeyboardInterrupt()),
    )
    finalize = []
    monkeypatch.setattr(
        "dashscope_agenticx.cli.handlers_setup._setup_finalize",
        lambda *args, **kwargs: finalize.append(True),
    )
    config = Config(user_name="previous")
    result = await _handle_setup(config, types.SimpleNamespace())
    assert result.status == "cancelled" and config.user_name == "previous"
    assert not finalize
    assert "Setup complete" not in capsys.readouterr().out


@pytest.mark.parametrize(
    "saved,key,model,expected",
    [
        (False, "", "qwen3.8-flash", True),
        (False, "sk-test", "qwen3.8-flash", True),
        (False, "sk-test", "qwen-max", False),
        (False, "", "qwen-max", True),
        (True, "", "qwen3.8-flash", False),
        (True, "sk-test", "qwen3.8-flash", False),
    ],
)
def test_workspace_setup_detection(tmp_path, saved, key, model, expected):
    config_file = tmp_path / "config.toml"
    if saved:
        config_file.touch()
    config = Config(tongyi_api_key=key, model=model)

    assert _needs_workspace_setup(config, config_file) is expected


@pytest.mark.parametrize(
    "saved,key,model,expected",
    [
        (False, "", "qwen3.8-flash", True),
        (False, "sk-test", "qwen3.8-flash", True),
        (True, "sk-test", "qwen3.8-flash", False),
        (False, "sk-test", "qwen-max", False),
    ],
)
def test_tui_runs_workspace_setup_before_launch(
    monkeypatch,
    tmp_path,
    saved,
    key,
    model,
    expected,
):
    from contextlib import nullcontext
    from unittest.mock import AsyncMock

    config_file = tmp_path / "config.toml"
    if saved:
        config_file.touch()
    monkeypatch.setattr(
        "dashscope_agenticx.config.WORKSPACE_CONFIG_FILE",
        config_file,
    )
    config = Config(tongyi_api_key=key, model=model, session_persist=False)
    agent = types.SimpleNamespace()
    calls = []
    manager = types.SimpleNamespace()
    runtime = types.SimpleNamespace(
        context=types.SimpleNamespace(session_manager=manager),
        create_agent=lambda **kwargs: agent,
        activate=nullcontext,
        aclose=AsyncMock(),
    )
    monkeypatch.setattr(
        "dashscope_agenticx.runtime.RuntimeBuilder",
        lambda *args, **kwargs: types.SimpleNamespace(build=lambda: runtime),
    )
    monkeypatch.setattr(runners, "_apply_custom_models", lambda cfg: None)
    monkeypatch.setattr(
        "dashscope_agenticx.audit.configure_audit_logger",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        "dashscope_agenticx.debuglog.configure_debug_log",
        lambda cfg: None,
    )
    monkeypatch.setattr(
        "dashscope_agenticx.permission.configure_permission_policy",
        lambda: None,
    )
    monkeypatch.setattr(
        "dashscope_agenticx.extensions.apply_extensions",
        lambda models: None,
    )
    monkeypatch.setattr(
        "dashscope_agenticx.cli.handlers_capability.sync_extensions_into_catalog",
        lambda extensions: None,
    )
    monkeypatch.setattr(
        "dashscope_agenticx.session.set_session_manager",
        lambda session_manager: None,
    )
    monkeypatch.setattr(runners, "_load_system_prompt", lambda: None)
    monkeypatch.setattr(runners, "_compose_system_prompt", lambda base: base)

    async def setup(cfg, current_agent):
        assert cfg is config
        assert current_agent is agent
        calls.append("setup")
        from dashscope_agenticx.cli.wizard import WizardResult

        return WizardResult("deferred", "setup")

    def launch(cfg, current_agent, **kwargs):
        assert cfg is config
        assert current_agent is agent
        assert "setup_on_start" not in kwargs
        calls.append("tui")

    monkeypatch.setattr(runners, "_handle_setup", setup)
    monkeypatch.setattr(
        runners,
        "ensure_provider_key",
        lambda cfg, current_agent: calls.append("key"),
    )
    monkeypatch.setattr("dashscope_agenticx.ui.tui.run_tui", launch)

    runners._run_tui_mode(config)

    assert calls == (["setup", "tui"] if expected else ["key", "tui"])
    runtime.aclose.assert_awaited_once()
