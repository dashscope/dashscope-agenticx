# -*- coding: utf-8 -*-
"""Capability chain hardening tests.

Covers the breakpoints found in the capability review:
1. Extension/bailian capability tools must reach the LLM tool schema once
   registered (registration is the gate).
2. build_http_tool must inject the token as Bearer when a capability only
   declares api_key_env/api_key_enc without an auth spec, and must fail with
   a clear message (not a raw 401) when credentials are declared but missing.
3. refresh_extension_capability_tools must rebind tool closures to the
   latest toml definitions (stale-closure fix).
4. Privacy mode must keep cloud capabilities out of the registration layer.
"""

from __future__ import annotations

import pytest

import dashscope_agenticx.tools.platform as platform_module
from dashscope_agenticx.config import Config
from dashscope_agenticx.extensions import (
    CustomCapability,
    CustomTool,
    _resolve_auth_header,
    build_http_tool,
)
from dashscope_agenticx.tools.registry import registry


@pytest.fixture(autouse=True)
def clean_capability_state():
    """Snapshot the global registry + capability→tools map; restore after."""
    snapshot_tools = dict(registry._tools)
    snapshot_caps = {
        k: set(v) for k, v in platform_module._capability_tools.items()
    }
    yield
    registry._tools.clear()
    registry._tools.update(snapshot_tools)
    platform_module._capability_tools.clear()
    platform_module._capability_tools.update(snapshot_caps)


class _FakeResponse:
    status_code = 200

    def json(self):
        return {"ok": True}


class _FakeClient:
    """Captures the outgoing request so tests can inspect headers."""

    last_request: dict = {}

    def __init__(self, timeout=None):
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def request(self, method, url, json=None, headers=None):
        _FakeClient.last_request = {
            "method": method,
            "url": url,
            "json": json,
            "headers": headers or {},
        }
        return _FakeResponse()


def _tool(name="do_thing", **kw) -> CustomTool:
    kw.setdefault("description", "d")
    kw.setdefault("endpoint", "https://example.com/api")
    return CustomTool(name=name, **kw)


# ── breakpoint 2: auth injection ───────────────────────────────────────


def test_resolve_auth_header_forms():
    assert _resolve_auth_header("") is None
    assert _resolve_auth_header("none") is None
    assert _resolve_auth_header("bearer:$NO_SUCH_ENV_XYZ") is None
    assert _resolve_auth_header(
        "bearer:$NO_SUCH_ENV_XYZ",
        fallback_key="tok",
    ) == (
        "Authorization",
        "Bearer tok",
    )
    assert _resolve_auth_header(
        "apikey-header:X-Key:$NO_SUCH_ENV_XYZ",
        fallback_key="tok",
    ) == ("X-Key", "tok")


@pytest.mark.asyncio
async def test_http_tool_injects_bearer_when_only_api_key_env(monkeypatch):
    """api_key_env without an auth spec must still produce a Bearer header."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    monkeypatch.setenv("TEST_CAP_TOKEN", "sekret-123")
    cap = CustomCapability(key="vendor.feat", api_key_env="TEST_CAP_TOKEN")
    call = build_http_tool(cap, _tool())
    out = await call()

    headers = _FakeClient.last_request["headers"]
    assert headers.get("Authorization") == "Bearer sekret-123"
    assert "ok" in out


@pytest.mark.asyncio
async def test_http_tool_missing_creds_fails_with_hint(monkeypatch):
    """Credentials declared (api_key_env) but unresolvable → clear error,
    no unauthenticated HTTP request sent."""
    import httpx

    def _explode(*args, **kwargs):
        raise AssertionError("must not send an HTTP request without creds")

    monkeypatch.setattr(httpx, "AsyncClient", _explode)
    monkeypatch.delenv("TEST_CAP_TOKEN", raising=False)
    cap = CustomCapability(key="vendor.feat", api_key_env="TEST_CAP_TOKEN")
    out = await build_http_tool(cap, _tool())()

    assert "Error" in out
    assert "/capability enable vendor.feat" in out


@pytest.mark.asyncio
async def test_http_tool_without_any_creds_sends_unauthenticated(monkeypatch):
    """No auth spec and no declared credentials → request goes out as-is."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    cap = CustomCapability(key="vendor.open")
    await build_http_tool(cap, _tool())()

    assert "Authorization" not in _FakeClient.last_request["headers"]


@pytest.mark.asyncio
async def test_http_tool_applies_param_defaults(monkeypatch):
    """Omitted optional params fall back to declared defaults instead of
    leaving unrendered {{placeholders}} that break the body JSON."""
    import httpx

    monkeypatch.setattr(httpx, "AsyncClient", _FakeClient)
    cap = CustomCapability(key="vendor.open")
    tool = CustomTool(
        name="gen",
        description="d",
        endpoint="https://example.com/api",
        params=[
            {"name": "prompt", "type": "string", "required": True},
            {"name": "size", "type": "string", "default": "1024x1024"},
        ],
        body_template='{"prompt": {{prompt}}, "size": {{size}}}',
    )
    await build_http_tool(cap, tool)(prompt="a bike")

    assert _FakeClient.last_request["json"] == {
        "prompt": "a bike",
        "size": "1024x1024",
    }


# ── breakpoint 1: schema visibility ────────────────────────────────────


def _patch_extension(monkeypatch, cap: CustomCapability) -> None:
    monkeypatch.setattr(
        "dashscope_agenticx.extensions.find_capability",
        lambda key: cap if key == cap.key else None,
    )
    from types import SimpleNamespace

    monkeypatch.setattr(
        "dashscope_agenticx.extensions.current",
        lambda: SimpleNamespace(capabilities=[cap]),
    )


def test_extension_capability_tool_reaches_schema(monkeypatch):
    cap = CustomCapability(key="vendor.feat", tools=[_tool(name="search")])
    _patch_extension(monkeypatch, cap)

    added = platform_module.register_one_capability(Config(), "vendor.feat")
    assert added == 1

    names = {s["name"] for s in registry.to_schema_list("")}
    assert "vendor_feat_search" in names

    # Disable drops it from both registry and schema.
    removed = platform_module.unregister_capability_tools("vendor.feat")
    assert removed == 1
    names = {s["name"] for s in registry.to_schema_list("画一张图")}
    assert "vendor_feat_search" not in names


# ── breakpoint 3: stale closures on reload ─────────────────────────────


def test_refresh_rebinds_tool_to_new_definition(monkeypatch):
    cap_v1 = CustomCapability(
        key="vendor.feat",
        tools=[_tool(name="t1", description="old-def")],
    )
    _patch_extension(monkeypatch, cap_v1)
    config = Config(enabled_capabilities=["vendor.feat"])
    assert platform_module.register_one_capability(config, "vendor.feat") == 1
    assert "old-def" in registry.get("vendor_feat_t1").description

    cap_v2 = CustomCapability(
        key="vendor.feat",
        tools=[_tool(name="t1", description="new-def")],
    )
    _patch_extension(monkeypatch, cap_v2)
    platform_module.refresh_extension_capability_tools(config)

    assert "new-def" in registry.get("vendor_feat_t1").description


# ── breakpoint 4: privacy at the registration layer ────────────────────


async def _fake_connect(service, config, url=""):
    return ""


def test_privacy_mode_skips_cloud_capabilities(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "dashscope_agenticx.extensions.current",
        lambda: SimpleNamespace(capabilities=[]),
    )
    config = Config()
    config.privacy_mode = True
    config.api_key = "dummy"

    platform_module.register_platform_tools(
        config,
        connect_mcp_fn=_fake_connect,
    )

    assert registry.get("mcp_connect") is None


def test_non_privacy_registers_cloud_capabilities(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(
        "dashscope_agenticx.extensions.current",
        lambda: SimpleNamespace(capabilities=[]),
    )
    config = Config()
    config.api_key = "dummy"

    platform_module.register_platform_tools(
        config,
        connect_mcp_fn=_fake_connect,
    )

    assert registry.get("mcp_connect") is not None


def test_unregister_cloud_capability_tools(monkeypatch):
    cap = CustomCapability(key="vendor.feat", tools=[_tool(name="search")])
    _patch_extension(monkeypatch, cap)
    config = Config()
    platform_module.register_one_capability(config, "vendor.feat")
    assert registry.get("vendor_feat_search") is not None

    removed = platform_module.unregister_cloud_capability_tools()
    assert removed == 1
    assert registry.get("vendor_feat_search") is None


# ── local.memory capability wiring ─────────────────────────────────────

_MEMORY_TOOLS = {"memory_search", "memory_store", "memory_delete"}


@pytest.fixture()
def isolated_memory(tmp_path, monkeypatch):
    import dashscope_agenticx.config as config_module
    from dashscope_agenticx.capabilities import get_capability_registry

    monkeypatch.setattr(config_module, "WORKSPACE_DIR", tmp_path)
    get_capability_registry().unregister("local.memory")
    yield
    get_capability_registry().unregister("local.memory")


def test_local_memory_capability_registers_tools_and_registry(isolated_memory):
    from dashscope_agenticx.capabilities import get_capability

    added = platform_module.register_one_capability(Config(), "local.memory")
    assert added == 3

    cap = get_capability("local.memory")
    assert cap is not None
    assert cap.name == "local.memory"
    assert cap.provider == "local"
    assert cap.is_available()

    names = {s["name"] for s in registry.to_schema_list("")}
    assert _MEMORY_TOOLS <= names

    removed = platform_module.unregister_capability_tools("local.memory")
    assert removed == 3
    names = {s["name"] for s in registry.to_schema_list("搜索记忆")}
    assert not (_MEMORY_TOOLS & names)


@pytest.mark.asyncio
async def test_local_memory_store_search_roundtrip(isolated_memory):
    platform_module.register_one_capability(Config(), "local.memory")

    store = registry.get("memory_store")
    out = await store.func(content="我喜欢用 Python 写脚本")
    assert "Remembered" in out

    found = await registry.get("memory_search").func(query="Python 脚本")
    assert "Python" in found
    assert "No relevant memories found" not in found

    # Duplicates are not stored twice.
    out2 = await store.func(content="我喜欢用 Python 写脚本")
    assert "duplicates" in out2


# ── cred reuse: extension cap borrows the built-in provider's key ──────


def test_extension_cap_reuses_builtin_provider_key(monkeypatch):
    """api_key_env matching a built-in provider's env var (DASHSCOPE_API_KEY
    ↔ tongyi) reuses the stored config key — no second prompt, tool gets
    authenticated without env or ENC."""
    # KEY_TARGETS reuse only works once dashscope_agenticx.cli.constants is loaded (the
    # real CLI always imports it at startup; importing it here registers
    # core tools, so assert by membership instead of tool counts).
    import dashscope_agenticx.cli.constants  # noqa: F401

    cap = CustomCapability(
        key="vendor.feat",
        api_key_env="DASHSCOPE_API_KEY",
        tools=[_tool()],
    )
    _patch_extension(monkeypatch, cap)
    monkeypatch.delenv("DASHSCOPE_API_KEY", raising=False)

    config = Config(tongyi_api_key="sk-stored")
    platform_module.register_one_capability(config, "vendor.feat")
    assert "vendor_feat_do_thing" in {t.name for t in registry.list_tools()}
    assert cap.runtime_key == "sk-stored"
    assert cap.resolve_auth_key() == "sk-stored"

    # Enable flow must not re-prompt for the same secret (would call
    # input() and hang/fail the test otherwise).
    from dashscope_agenticx.cli.handlers_key import (
        _maybe_prompt_extension_token,
    )

    _maybe_prompt_extension_token("vendor.feat", config)

    platform_module.unregister_capability_tools("vendor.feat")
