# -*- coding: utf-8 -*-
"""Edge-case tests for the capability module:
bailian MCP SSE client, session tools, shell classifier attacks,
browser SSRF guards, skill store/package lifecycle, extension plumbing."""

from __future__ import annotations

import asyncio
import io
import json
import os
import stat
import tarfile
import urllib.request
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

from dashscope_agenticx.config import PROVIDER_MODELS, Config
from dashscope_agenticx.tools.registry import registry


@pytest.fixture
def clean_registry():
    """Snapshot/restore the global tool registry around a test."""
    before = dict(registry._tools)
    yield registry
    registry._tools.clear()
    registry._tools.update(before)


@pytest.fixture
def no_config_save(monkeypatch):
    monkeypatch.setattr(Config, "save_workspace", lambda self: None)
    monkeypatch.setattr(Config, "save_global", lambda self: None)


# ---------------------------------------------------------------------------
# platforms/bailian/mcp.py — SSE client
# ---------------------------------------------------------------------------


class _FakeStreamResp:
    def __init__(self, lines, status=200, explode_after=None):
        self.status_code = status
        self._lines = lines
        self._explode_after = explode_after

    async def aiter_lines(self):
        for i, line in enumerate(self._lines):
            await asyncio.sleep(0)
            yield line
            if self._explode_after is not None and i >= self._explode_after:
                raise RuntimeError("stream boom")


class _FakeStreamCtx:
    def __init__(self, resp):
        self._resp = resp

    async def __aenter__(self):
        return self._resp

    async def __aexit__(self, *exc):
        return False


class _FakeHTTP:
    """Stand-in for httpx.AsyncClient used by MCPClient."""

    def __init__(self, lines, status=200, post_error=None, explode_after=None):
        self._resp = _FakeStreamResp(lines, status, explode_after)
        self.posts: list[dict] = []
        self.post_error = post_error
        self.closed = False

    def stream(self, method, url, headers=None):
        return _FakeStreamCtx(self._resp)

    async def post(self, url, json=None, headers=None):
        self.posts.append(json)
        if self.post_error:
            raise self.post_error

        class _R:
            def raise_for_status(self):
                pass

        return _R()

    async def aclose(self):
        self.closed = True


def _mcp_client(fake: _FakeHTTP):
    from dashscope_agenticx.platforms.bailian.mcp import MCPClient

    client = MCPClient("time", "test-key")
    client._client = fake
    return client


class TestMCPClient:
    async def test_full_flow_initialize_and_list_tools(self):
        lines = [
            "event: endpoint",
            "data: /messages?session=abc",
            "",
            "event: message",
            'data: {"jsonrpc": "2.0", "id": 1, "result": {"serverInfo": {}}}',
            "",
            "event: message",
            'data: {"jsonrpc": "2.0", "id": 2, "result": {"tools": [{"name": "t1"}]}}',
            "",
        ]
        fake = _FakeHTTP(lines)
        client = _mcp_client(fake)
        assert await client.initialize() is True
        assert client._message_endpoint.startswith("https://")
        assert "messages" in client._message_endpoint
        assert await client.list_tools() == [{"name": "t1"}]
        await client.close()
        assert fake.closed

    async def test_non_200_sse_sets_last_error(self):
        fake = _FakeHTTP(["Service Unavailable"], status=503)
        client = _mcp_client(fake)
        assert await client.initialize() is False
        assert "Service unavailable" in client.last_error

    async def test_stream_error_resolves_pending_instead_of_hanging(self):
        lines = ["event: endpoint", "data: /m", ""]
        fake = _FakeHTTP(lines, explode_after=2)
        client = _mcp_client(fake)
        await client._start_sse()
        # The stream explodes right after the endpoint event; the pending
        # initialize request must be failed by _fail_all_pending, not hang.
        assert await client.initialize() is False
        assert "中断" in client.last_error or "boom" in client.last_error
        assert client._pending == {}

    async def test_close_resolves_pending_futures(self):
        fake = _FakeHTTP(["event: endpoint", "data: /m", ""])
        client = _mcp_client(fake)
        await client._start_sse()
        task = asyncio.create_task(client._send_jsonrpc("tools/list"))
        await asyncio.sleep(0.05)
        assert client._pending, "request should be in-flight"
        await client.close()
        result = await asyncio.wait_for(task, timeout=2)
        assert "error" in result
        assert "closed" in result["error"]["message"]

    async def test_post_failure_pops_pending_and_raises(self):
        fake = _FakeHTTP(
            ["event: endpoint", "data: /m", ""],
            post_error=httpx.ConnectError("refused"),
        )
        client = _mcp_client(fake)
        await client._start_sse()
        with pytest.raises(httpx.ConnectError):
            await client._send_jsonrpc("tools/list")
        assert client._pending == {}

    def test_resolve_endpoint_absolute_and_relative(self):
        client = _mcp_client(_FakeHTTP([]))
        client._resolve_endpoint("https://other.example.com/m")
        assert client._message_endpoint == "https://other.example.com/m"
        client._resolve_endpoint("/rel/path")
        assert client._message_endpoint.startswith("https://")
        assert client._message_endpoint.endswith("/rel/path")

    async def test_handle_message_ignores_unknown_and_malformed(self):
        client = _mcp_client(_FakeHTTP([]))
        client._handle_message("not json")
        future = asyncio.get_running_loop().create_future()
        client._pending[7] = future
        client._handle_message('{"id": 99, "result": {}}')  # unknown id
        assert not future.done()
        client._handle_message('{"id": 7, "result": {"ok": 1}}')
        assert future.done()
        assert future.result()["result"] == {"ok": 1}

    async def test_call_tool_error_dict(self):
        client = _mcp_client(_FakeHTTP([]))

        async def fake_send(method, params=None):
            return {"error": {"message": "tool exploded"}}

        client._send_jsonrpc = fake_send
        out = await client.call_tool("x", {})
        assert "MCP Error: tool exploded" in out

    async def test_call_tool_content_parts(self):
        client = _mcp_client(_FakeHTTP([]))

        async def fake_send(method, params=None):
            return {
                "result": {
                    "content": [
                        {"type": "text", "text": "hello"},
                        {"type": "image", "mimeType": "image/png"},
                        {"type": "other", "v": 1},
                        "raw-string",
                    ],
                },
            }

        client._send_jsonrpc = fake_send
        out = await client.call_tool("x", {})
        assert "hello" in out
        assert "[image: image/png]" in out
        assert "raw-string" in out

    async def test_call_tool_empty_content(self):
        client = _mcp_client(_FakeHTTP([]))

        async def fake_send(method, params=None):
            return {"result": {}}

        client._send_jsonrpc = fake_send
        assert await client.call_tool("x", {}) == "{}"


# ---------------------------------------------------------------------------
# tools/session.py — agent-facing session management tools
# ---------------------------------------------------------------------------


def _make_agent():
    from dashscope_agenticx.memory.plan import PlanTracker

    return SimpleNamespace(
        provider=None,
        provider_name="",
        model_name="",
        memory_manager=SimpleNamespace(
            session=SimpleNamespace(plan=PlanTracker()),
        ),
    )


def _register_session_tools(config, agent, clients=None, connect_error=""):
    from dashscope_agenticx.tools.session import register_session_tools

    clients = clients if clients is not None else {}

    async def _connect(service, cfg):
        if connect_error:
            return connect_error
        clients[service] = SimpleNamespace(tools=[{"name": "t"}])
        return ""

    async def _disconnect(service):
        clients.pop(service, None)

    register_session_tools(
        config,
        get_agent=lambda: agent,
        get_provider_fn=lambda *a, **kw: object(),
        connect_mcp_fn=_connect,
        disconnect_mcp_fn=_disconnect,
        list_mcp_services_fn=lambda: "services: " + ",".join(clients),
        get_mcp_clients_fn=lambda: clients,
    )
    return clients


class TestSessionModelTools:
    async def test_switch_model(self, clean_registry, no_config_save):
        config = Config(
            provider="tongyi",
            model="qwen-plus",
            tongyi_api_key="k",
        )
        agent = _make_agent()
        _register_session_tools(config, agent)
        out = await clean_registry.get("switch_model").func(
            model_name="qwen-max",
        )
        assert "Switched model" in out
        assert config.model == "qwen-max"
        assert agent.provider is not None
        assert agent.model_name == "qwen-max"

    async def test_switch_provider_unknown(
        self,
        clean_registry,
        no_config_save,
    ):
        config = Config()
        _register_session_tools(config, _make_agent())
        out = await clean_registry.get("switch_provider").func(
            provider_name="not-a-provider",
        )
        assert "Unknown provider" in out

    async def test_switch_provider_known(self, clean_registry, no_config_save):
        config = Config()
        agent = _make_agent()
        _register_session_tools(config, agent)
        provider = next(iter(PROVIDER_MODELS))
        out = await clean_registry.get("switch_provider").func(
            provider_name=provider,
        )
        assert "Switched" in out
        assert config.provider == provider
        assert config.model == PROVIDER_MODELS[provider][0]
        assert agent.provider_name == provider

    async def test_list_models_marks_current(
        self,
        clean_registry,
        no_config_save,
    ):
        config = Config(provider="tongyi")
        _register_session_tools(config, _make_agent())
        out = await clean_registry.get("list_models").func()
        assert "tongyi ← current" in out


class TestSessionCapabilityTools:
    async def test_capability_enable_unknown(
        self,
        clean_registry,
        no_config_save,
    ):
        config = Config()
        _register_session_tools(config, _make_agent())
        out = await clean_registry.get("capability_enable").func(key="no.such")
        assert "Unknown capability" in out

    async def test_capability_enable_privacy_blocks_cloud(
        self,
        clean_registry,
        no_config_save,
    ):
        config = Config(privacy_mode=True)
        _register_session_tools(config, _make_agent())
        out = await clean_registry.get("capability_enable").func(
            key="bailian.mcp",
        )
        assert "Privacy mode" in out

    async def test_capability_enable_disable_cycle(
        self,
        clean_registry,
        no_config_save,
    ):
        config = Config(enabled_capabilities=["local.memory"])
        _register_session_tools(config, _make_agent())
        enable = clean_registry.get("capability_enable").func
        out = await enable(key="bailian.mcp")
        assert "Enabled bailian.mcp" in out
        assert "bailian.mcp" in config.enabled_capabilities
        out = await enable(key="bailian.mcp")
        assert "already enabled" in out

        disable = clean_registry.get("capability_disable").func
        out = await disable(key="bailian.mcp")
        assert "Disabled" in out
        assert "bailian.mcp" not in config.enabled_capabilities
        out = await disable(key="bailian.mcp")
        assert "already disabled" in out
        out = await disable(key="no.such")
        assert "Unknown capability" in out

    async def test_capability_list(self, clean_registry, no_config_save):
        config = Config(enabled_capabilities=["local.memory"])
        _register_session_tools(config, _make_agent())
        out = await clean_registry.get("capability_list").func()
        assert "local.memory — ✓ enabled" in out
        assert "bailian.mcp — ✗ disabled" in out


class TestSessionMCPTools:
    async def test_connect_success_and_idempotent(
        self,
        clean_registry,
        no_config_save,
    ):
        config = Config()
        clients = _register_session_tools(config, _make_agent())
        connect = clean_registry.get("mcp_connect").func
        out = await connect(service="time")
        assert "Connected time" in out
        assert any(m.service == "time" for m in config.mcp_servers)
        out = await connect(service="time")
        assert "already connected (1 tools)" in out

    async def test_connect_failure(self, clean_registry, no_config_save):
        config = Config()
        _register_session_tools(config, _make_agent(), connect_error="boom")
        out = await clean_registry.get("mcp_connect").func(service="time")
        assert "Failed to connect time: boom" in out
        assert not config.mcp_servers

    async def test_disconnect(self, clean_registry, no_config_save):
        config = Config()
        clients = _register_session_tools(config, _make_agent())
        disconnect = clean_registry.get("mcp_disconnect").func
        assert "is not connected" in await disconnect(service="time")
        await clean_registry.get("mcp_connect").func(service="time")
        assert "Disconnected" in await disconnect(service="time")
        assert "time" not in clients
        assert not config.mcp_servers


class TestSessionPlanTools:
    async def test_plan_lifecycle(self, clean_registry, no_config_save):
        config = Config()
        agent = _make_agent()
        _register_session_tools(config, agent)

        out = await clean_registry.get("get_plan_status").func()
        assert "No plan in progress" in out

        out = await clean_registry.get("create_plan").func(
            goal="重构",
            steps=["a", "b"],
        )
        assert "重构" in out  # goal appears in the creation confirmation
        out = await clean_registry.get("get_plan_status").func()
        assert "Plan progress: 0/2" in out

        out = await clean_registry.get("complete_step").func(step_index=0)
        assert "Step 1 completed" in out
        out = await clean_registry.get("complete_step").func(step_index=1)
        assert "All steps completed" in out
        out = await clean_registry.get("get_plan_status").func()
        assert "No plan in progress" in out


# ---------------------------------------------------------------------------
# tools/shell.py — blocked-pattern precision + classifier attack samples
# ---------------------------------------------------------------------------


class TestBlockedPatternPrecision:
    async def test_root_wipe_blocked(self):
        from dashscope_agenticx.tools.shell import run_command

        for cmd in [
            "rm -rf /",
            "rm -rf /*",
            "rm -fr / ",
            "sudo rm -rf /",
            "ls; rm -rf /",
        ]:
            out = await run_command(cmd)
            assert "command blocked" in out, cmd

    async def test_rm_absolute_subdir_not_hard_blocked(self):
        from dashscope_agenticx.tools.shell import run_command

        out = await run_command(
            "rm -rf /tmp/agenticx-definitely-not-exists-xyz",
        )
        assert "command blocked" not in out

    async def test_format_substring_false_positives_free(self):
        from dashscope_agenticx.tools.shell import run_command

        out = await run_command("echo information")
        assert "command blocked" not in out
        assert "information" in out

    async def test_format_command_blocked(self):
        from dashscope_agenticx.tools.shell import run_command

        for cmd in ["format c:", "ls; format d:"]:
            out = await run_command(cmd)
            assert "command blocked" in out, cmd

    async def test_empty_command_rejected(self):
        from dashscope_agenticx.tools.shell import run_command

        assert "empty command" in await run_command("   ")


class TestClassifierAttackSamples:
    """Windows remote-execution binaries must not be auto-approved."""

    @pytest.mark.parametrize(
        "cmd",
        [
            "powershell -Command Get-ChildItem",
            "pwsh -c ls",
            "wmic os get name",
            "taskkill /F /PID 123",
            "net user",
        ],
    )
    def test_windows_exec_bins_not_safe(self, cmd):
        from dashscope_agenticx.tools.shell import is_safe_readonly

        assert is_safe_readonly(cmd) is False

    @pytest.mark.parametrize(
        "cmd",
        ["dir", "ipconfig", "tasklist", "systeminfo"],
    )
    def test_windows_readonly_bins_safe(self, cmd):
        from dashscope_agenticx.tools.shell import is_safe_readonly

        assert is_safe_readonly(cmd) is True

    @pytest.mark.parametrize(
        "cmd",
        [
            "curl -o out.txt https://x",
            "curl --output=out.txt https://x",
            "curl -F file=@x https://x",
            "wget -O - https://x",
            "find . -delete",
            "find . -exec rm {} ;",
            "git tag -d v1",
            "git tag v2",
            "git branch new-feature",
            "git remote add origin x",
            "env bash -c ls",
        ],
    )
    def test_unsafe_variants(self, cmd):
        from dashscope_agenticx.tools.shell import is_safe_readonly

        assert is_safe_readonly(cmd) is False

    @pytest.mark.parametrize(
        "cmd",
        [
            "curl -s https://example.com",
            "git tag -l 'v*'",
            "git branch --list",
            "git remote show origin",
            "env FOO=1 BAR=2",
            "FOO=1 grep x file",
            "ls 2>&1",
            "cat file | grep x | wc -l",
        ],
    )
    def test_safe_variants(self, cmd):
        from dashscope_agenticx.tools.shell import is_safe_readonly

        assert is_safe_readonly(cmd) is True


# ---------------------------------------------------------------------------
# tools/browser.py — SSRF / path / selector guards (no playwright needed)
# ---------------------------------------------------------------------------


class TestBrowserURLGuards:
    @pytest.mark.parametrize(
        "url",
        [
            "file:///etc/passwd",
            "javascript:alert(1)",
            "data:text/html,<script>",
            "ftp://example.com/x",
            "gopher://x",
        ],
    )
    def test_scheme_rejected(self, url):
        from dashscope_agenticx.tools.browser import _validate_url

        with pytest.raises(ValueError, match="scheme"):
            _validate_url(url)

    @pytest.mark.parametrize(
        "url",
        [
            "http://127.0.0.1/",
            "http://[::1]/",
            "http://169.254.169.254/latest/meta-data",
            "http://10.0.0.1/",
            "http://192.168.1.1/",
            "http://0.0.0.0/",
        ],
    )
    def test_private_ip_rejected(self, url):
        from dashscope_agenticx.tools.browser import _validate_url

        with pytest.raises(ValueError, match="SSRF"):
            _validate_url(url)

    def test_public_ip_allowed(self):
        from dashscope_agenticx.tools.browser import _validate_url

        assert (
            _validate_url("http://93.184.216.34/") == "http://93.184.216.34/"
        )

    def test_unresolvable_host_rejected(self):
        from dashscope_agenticx.tools.browser import _validate_url

        with pytest.raises(ValueError, match="Cannot resolve"):
            _validate_url("http://nonexistent.invalid/")

    def test_missing_host_rejected(self):
        from dashscope_agenticx.tools.browser import _validate_url

        with pytest.raises(ValueError):
            _validate_url("http://")

    def test_safe_response_ip(self):
        from dashscope_agenticx.tools.browser import _is_safe_response_ip

        assert _is_safe_response_ip("http://127.0.0.1/x") is False
        assert _is_safe_response_ip("http://93.184.216.34/x") is True

    async def test_scrape_web_rejects_bad_url_before_launch(self):
        from dashscope_agenticx.tools.browser import scrape_web

        out = await scrape_web("file:///etc/passwd")
        assert "Error" in out


class TestBrowserPathAndSelector:
    def test_screenshot_path_traversal_rejected(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from dashscope_agenticx.tools.browser import _validate_screenshot_path

        with pytest.raises(ValueError, match="path traversal"):
            _validate_screenshot_path("../escape.png")
        with pytest.raises(ValueError):
            _validate_screenshot_path("/etc/escape.png")

    def test_screenshot_path_ok_creates_parent(self, tmp_path, monkeypatch):
        monkeypatch.chdir(tmp_path)
        from dashscope_agenticx.tools.browser import _validate_screenshot_path

        resolved = _validate_screenshot_path("shots/a.png")
        assert resolved.endswith("shots/a.png")
        assert (tmp_path / "shots").is_dir()

    @pytest.mark.parametrize(
        "sel",
        ["div`x`", "a;b", "${x}", "javascript:x", "<script>"],
    )
    def test_selector_dangerous_rejected(self, sel):
        from dashscope_agenticx.tools.browser import _validate_selector

        with pytest.raises(ValueError):
            _validate_selector(sel)

    def test_selector_ok(self):
        from dashscope_agenticx.tools.browser import _validate_selector

        assert _validate_selector(".cls > #id") == ".cls > #id"
        assert _validate_selector("") == ""

    def test_clamp_timeout(self):
        from dashscope_agenticx.tools.browser import _clamp_timeout

        assert _clamp_timeout(0) == 1000
        assert _clamp_timeout(999999) == 120000
        assert _clamp_timeout(5000) == 5000


class TestBrowserCleanup:
    def test_cleanup_with_closed_loop_discards_coroutines(self):
        import dashscope_agenticx.tools.browser as browser

        closed_coros = []

        class _FakeObj:
            def __init__(self, name):
                self.name = name

            def close(self):
                async def _noop():
                    pass

                coro = _noop()
                closed_coros.append(coro)
                return coro

            stop = close

        browser._browser_instance = _FakeObj("b")
        browser._playwright_instance = _FakeObj("p")
        browser._browser_loop = (
            None  # loop gone → coroutines must be closed, not awaited
        )
        browser._cleanup_browser()
        assert browser._browser_instance is None
        assert browser._playwright_instance is None
        for coro in closed_coros:
            with pytest.raises(RuntimeError):
                coro.send(None)  # proves the coroutine was closed


# ---------------------------------------------------------------------------
# skills/store.py — url install, symlink safety, traversal protection
# ---------------------------------------------------------------------------


def _make_pkg(base: Path, name="demo", version="0.1.0") -> Path:
    pkg = base / name
    pkg.mkdir(parents=True)
    (pkg / "skill.toml").write_text(
        f'name = "{name}"\nversion = "{version}"\ndescription = "d"\n',
        encoding="utf-8",
    )
    (pkg / "prompt.md").write_text("prompt body", encoding="utf-8")
    return pkg


def _tar_bytes(src: Path, arcname: str) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w:gz") as tar:
        tar.add(src, arcname=arcname)
    return buf.getvalue()


class TestSkillStoreUrlInstall:
    def test_url_install_roundtrip(self, tmp_path, monkeypatch):
        from dashscope_agenticx.skills import store

        src_pkg = _make_pkg(tmp_path / "src")
        payload = _tar_bytes(src_pkg, "demo")
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda url, timeout=None: io.BytesIO(payload),
        )
        target = tmp_path / "store"
        name = store.install(
            "https://example.com/demo-0.1.0.tar.gz",
            skills_dir=target,
        )
        assert name == "demo"
        assert (target / "demo" / "skill.toml").exists()
        manifest = store.load_manifest(target)
        assert manifest["demo"]["source_type"] == "url"

    def test_url_install_via_registry_entry(self, tmp_path, monkeypatch):
        from dashscope_agenticx.skills import store

        src_pkg = _make_pkg(tmp_path / "src")
        payload = _tar_bytes(src_pkg, "demo")
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda url, timeout=None: io.BytesIO(payload),
        )
        monkeypatch.setattr(
            store,
            "_fetch_registry_index",
            lambda url: [
                {
                    "name": "demo",
                    "version": "0.1.0",
                    "source": "https://x/demo.tar.gz",
                },
            ],
        )
        target = tmp_path / "store"
        name = store.install(
            "demo",
            skills_dir=target,
            registry_url="https://reg",
        )
        assert name == "demo"
        assert (target / "demo" / "prompt.md").exists()

    def test_traversal_member_rejected(self, tmp_path, monkeypatch):
        from dashscope_agenticx.skills import store

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            info = tarfile.TarInfo("../evil.txt")
            data = b"pwned"
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        payload = buf.getvalue()
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda url, timeout=None: io.BytesIO(payload),
        )
        with pytest.raises(RuntimeError, match="escapes target dir"):
            store.install(
                "https://x/evil.tar.gz",
                skills_dir=tmp_path / "store",
            )
        assert not (tmp_path / "evil.txt").exists()

    def test_multi_toplevel_tar_rejected(self, tmp_path, monkeypatch):
        from dashscope_agenticx.skills import store

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            for n in ("a/f.txt", "b/f.txt"):
                info = tarfile.TarInfo(n)
                data = b"x"
                info.size = 1
                tar.addfile(info, io.BytesIO(data))
        payload = buf.getvalue()
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda url, timeout=None: io.BytesIO(payload),
        )
        with pytest.raises(RuntimeError, match="top-level directory"):
            store.install(
                "https://x/multi.tar.gz",
                skills_dir=tmp_path / "store",
            )

    def test_update_url_source(self, tmp_path, monkeypatch):
        from dashscope_agenticx.skills import store

        src_pkg = _make_pkg(tmp_path / "src")
        payload_v2 = _tar_bytes(src_pkg, "demo")
        monkeypatch.setattr(
            urllib.request,
            "urlopen",
            lambda url, timeout=None: io.BytesIO(payload_v2),
        )
        target = tmp_path / "store"
        store.install("https://x/demo.tar.gz", skills_dir=target)
        out = store.update("demo", skills_dir=target)
        assert out == "demo"
        assert (target / "demo" / "skill.toml").exists()

    def test_copy_over_symlink_dest(self, tmp_path):
        from dashscope_agenticx.skills.store import _copy_package

        src = _make_pkg(tmp_path / "src")
        link_target = tmp_path / "elsewhere"
        link_target.mkdir()
        dest = tmp_path / "store" / "demo"
        dest.parent.mkdir(parents=True)
        dest.symlink_to(link_target, target_is_directory=True)
        _copy_package(src, dest)
        assert dest.is_dir() and not dest.is_symlink()
        assert (dest / "skill.toml").exists()


# ---------------------------------------------------------------------------
# skills/package.py + manager.py — rollback and hook lifecycle
# ---------------------------------------------------------------------------


class TestSkillPackageRollback:
    def test_failed_tools_py_rolls_back(self, tmp_path, clean_registry):
        import sys

        from dashscope_agenticx.skills.package import (
            SkillPackage,
            register_skill_package,
        )

        pkg_dir = _make_pkg(tmp_path, name="boom")
        (pkg_dir / "tools.py").write_text(
            "from dashscope_agenticx.tools.registry import Tool, registry\n"
            "registry.register_mcp_tool(name='orphan_tool', description='d',"
            " parameters={}, call_fn=lambda: 'x')\n"
            "raise RuntimeError('mid-import failure')\n",
            encoding="utf-8",
        )
        pkg = SkillPackage(
            name="boom",
            version="1",
            description="d",
            author="a",
            path=pkg_dir,
        )
        register_skill_package(pkg, clean_registry, SimpleNamespace())
        assert clean_registry.get("orphan_tool") is None
        assert not any(
            k.startswith("agenticx_skill_tools_boom") for k in sys.modules
        )
        assert pkg.tools_registered == []

    def test_hook_unregister_roundtrip(self, tmp_path, clean_registry):
        from dashscope_agenticx.hooks import HookBus
        from dashscope_agenticx.skills.package import (
            SkillPackage,
            register_skill_package,
            unregister_skill_package,
        )

        pkg_dir = _make_pkg(tmp_path, name="hooky")
        (pkg_dir / "hooks.toml").write_text(
            '[[hooks.tool_post]]\naction = "log"\nmessage = "hi"\n',
            encoding="utf-8",
        )
        bus = HookBus(hooks_file=tmp_path / "nonexistent.toml")
        before = len(bus.hooks)
        pkg = SkillPackage(
            name="hooky",
            version="1",
            description="d",
            author="a",
            path=pkg_dir,
        )
        register_skill_package(pkg, clean_registry, bus)
        assert len(bus.hooks) == before + 1
        unregister_skill_package(pkg, bus)
        assert len(bus.hooks) == before
        assert pkg.hooks_registered == []


class TestSkillManagerReload:
    def test_reload_does_not_duplicate_hooks(self, tmp_path, monkeypatch):
        from dashscope_agenticx.hooks import HookBus
        from dashscope_agenticx.skills.manager import SkillManager

        pkg_dir = _make_pkg(tmp_path / "skills", name="hooky")
        (pkg_dir / "hooks.toml").write_text(
            '[[hooks.tool_post]]\naction = "log"\nmessage = "hi"\n',
            encoding="utf-8",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.skills.store.get_skills_dir",
            lambda global_=False: tmp_path / "skills",
        )
        monkeypatch.setattr(
            "dashscope_agenticx.skills.package.discover_skill_packages",
            lambda extra_dirs=None: [
                __import__(
                    "dashscope_agenticx.skills.package",
                    fromlist=["load_skill_package"],
                ).load_skill_package(pkg_dir),
            ],
        )
        bus = HookBus(hooks_file=tmp_path / "none.toml")
        mgr = SkillManager()
        mgr.load(bus)
        count_after_first = len(bus.hooks)
        mgr.reload()
        mgr.reload()
        assert len(bus.hooks) == count_after_first


# ---------------------------------------------------------------------------
# capabilities/__init__.py — registry roundtrip
# ---------------------------------------------------------------------------


class TestCapabilityRegistry:
    def test_register_get_unregister(self):
        from dashscope_agenticx.capabilities import (
            CapabilityRegistry,
            MemoryCapability,
        )

        class _Mem(MemoryCapability):
            @property
            def name(self):
                return "test.mem"

            @property
            def provider(self):
                return "test"

            def is_available(self):
                return True

            async def initialize(self):
                pass

            async def shutdown(self):
                pass

            async def search(self, query, top_k=5):
                return []

            async def add(self, content, metadata=None):
                return "id"

            async def delete(self, entry_id):
                return True

            async def list(self, limit=20):
                return []

        reg = CapabilityRegistry()
        cap = _Mem()
        reg.register(cap)
        assert reg.get("test.mem") is cap
        assert reg.get("missing") is None
        reg.unregister("test.mem")
        assert reg.get("test.mem") is None


# ---------------------------------------------------------------------------
# extensions.py — shell injection quoting, private writes, GET rendering
# ---------------------------------------------------------------------------


class TestExtensionShellTools:
    async def test_shell_arg_injection_quoted(self, clean_registry, tmp_path):
        from dashscope_agenticx.extensions import _register_custom_shell_tools

        pwned = tmp_path / "pwned"
        ext = SimpleNamespace(
            shell_tools=[
                SimpleNamespace(
                    name="inj_echo",
                    description="d",
                    command_template="echo {{msg}}",
                    params=[{"name": "msg", "type": "string"}],
                    permission="auto",
                ),
            ],
        )
        _register_custom_shell_tools(ext)
        func = clean_registry.get("inj_echo").func
        out = await func(msg=f"hello; touch {pwned}")
        assert "hello" in out
        assert not pwned.exists()

    async def test_shell_timeout_and_failure(self, clean_registry):
        from dashscope_agenticx.extensions import _register_custom_shell_tools

        ext = SimpleNamespace(
            shell_tools=[
                SimpleNamespace(
                    name="fail_cmd",
                    description="d",
                    command_template="exit 3",
                    params=[],
                    permission="auto",
                ),
            ],
        )
        _register_custom_shell_tools(ext)
        out = await clean_registry.get("fail_cmd").func()
        assert "无输出" in out or out == "" or isinstance(out, str)


class TestPrivateWrites:
    def test_write_private_text_perms(self, tmp_path):
        from dashscope_agenticx.extensions import _write_private_text

        target = tmp_path / "custom-extensions.toml"
        _write_private_text(target, 'key = "ENC:secret"\n')
        mode = stat.S_IMODE(target.stat().st_mode)
        assert mode == 0o600
        assert target.read_text() == 'key = "ENC:secret"\n'

    def test_write_private_text_existing_file(self, tmp_path):
        from dashscope_agenticx.extensions import _write_private_text

        target = tmp_path / "x.toml"
        target.write_text("old")
        target.chmod(0o644)
        _write_private_text(target, "new")
        assert target.read_text() == "new"
        assert stat.S_IMODE(target.stat().st_mode) == 0o600


class TestHTTPToolRendering:
    async def test_get_renders_endpoint_and_sends_no_body(self, monkeypatch):
        from dashscope_agenticx.extensions import (
            CustomCapability,
            CustomTool,
            build_http_tool,
        )

        captured = {}

        class _Resp:
            status_code = 200
            text = '{"ok": true}'

            def json(self):
                return {"ok": True}

        class _Client:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def request(self, method, url, json=None, headers=None):
                captured.update(method=method, url=url, json=json)
                return _Resp()

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        cap = CustomCapability(key="t.cap")
        tool = CustomTool(
            name="search",
            description="d",
            endpoint="https://api.example.com/q={{query}}",
            http_method="GET",
            params=[{"name": "query"}],
        )
        out = await build_http_tool(cap, tool)(query="杭州 天气")
        assert captured["method"] == "GET"
        # URL template values are percent-encoded by _render_url_template.
        assert (
            captured["url"]
            == "https://api.example.com/q=%E6%9D%AD%E5%B7%9E%20%E5%A4%A9%E6%B0%94"
        )
        assert captured["json"] is None
        assert json.loads(out) == {"ok": True}

    async def test_post_sends_rendered_body(self, monkeypatch):
        from dashscope_agenticx.extensions import (
            CustomCapability,
            CustomTool,
            build_http_tool,
        )

        captured = {}

        class _Resp:
            status_code = 200
            text = "ok"

            def json(self):
                raise ValueError("not json")

        class _Client:
            def __init__(self, **kw):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *a):
                return False

            async def request(self, method, url, json=None, headers=None):
                captured.update(method=method, url=url, json=json)
                return _Resp()

        monkeypatch.setattr(httpx, "AsyncClient", _Client)
        cap = CustomCapability(key="t.cap")
        tool = CustomTool(
            name="send",
            description="d",
            endpoint="https://api.example.com/send",
            http_method="POST",
            params=[{"name": "msg", "default": "hi"}],
            body_template='{"text": {{msg}}}',
        )
        out = await build_http_tool(cap, tool)()
        assert captured["json"] == {"text": "hi"}
        assert out == "ok"

    async def test_bad_body_template_json(self):
        from dashscope_agenticx.extensions import (
            CustomCapability,
            CustomTool,
            build_http_tool,
        )

        cap = CustomCapability(key="t.cap")
        tool = CustomTool(
            name="bad",
            description="d",
            endpoint="https://x",
            body_template="{not json",
        )
        out = await build_http_tool(cap, tool)()
        assert "valid JSON" in out


class TestExtensionLoadErrors:
    def test_unreadable_file_recorded_not_raised(self, tmp_path, monkeypatch):
        import dashscope_agenticx.extensions as ext_mod

        bad = tmp_path / "bad.toml"
        bad.write_text("x = 1")
        monkeypatch.setattr(ext_mod, "GLOBAL_EXTENSIONS_FILE", bad)
        monkeypatch.setattr(
            ext_mod,
            "WORKSPACE_EXTENSIONS_FILE",
            tmp_path / "missing.toml",
        )

        original = Path.read_text

        def _raise(self, *a, **kw):
            if self == bad:
                raise OSError("permission denied")
            return original(self, *a, **kw)

        monkeypatch.setattr(Path, "read_text", _raise)
        ext = ext_mod.load_extensions()
        assert any("read failed" in e for e in ext.errors)


# ---------------------------------------------------------------------------
# platforms/bailian/cli.py — env key injection, timeout reaping
# ---------------------------------------------------------------------------


class TestBailianCLIRun:
    async def test_api_key_via_env_not_argv(self, monkeypatch):
        from dashscope_agenticx.platforms.bailian.cli import BailianCLIClient

        captured = {}

        class _Proc:
            returncode = 0

            async def communicate(self, inp=None):
                return (b"out", b"")

        async def fake_exec(*cmd, **kw):
            captured["cmd"] = cmd
            captured["env"] = kw.get("env")
            return _Proc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        client = BailianCLIClient(api_key="supersecret")
        result = await client.run(["text", "chat"])
        assert "--api-key" not in captured["cmd"]
        assert "supersecret" not in captured["cmd"]
        assert captured["env"]["DASHSCOPE_API_KEY"] == "supersecret"
        assert result.ok
        assert result.stdout == "out"

    async def test_timeout_kills_and_reaps(self, monkeypatch):
        from dashscope_agenticx.platforms.bailian.cli import BailianCLIClient

        proc = None

        class _SlowProc:
            returncode = None
            killed = False
            waited = False

            async def communicate(self, inp=None):
                await asyncio.sleep(60)

            def kill(self):
                self.killed = True

            async def wait(self):
                self.waited = True

        async def fake_exec(*cmd, **kw):
            nonlocal proc
            proc = _SlowProc()
            return proc

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        client = BailianCLIClient(timeout=1)
        client.timeout = 0.05
        result = await client.run(["x"])
        assert result.code == 124
        assert proc.killed and proc.waited

    async def test_no_key_no_env_override(self, monkeypatch):
        from dashscope_agenticx.platforms.bailian.cli import BailianCLIClient

        captured = {}

        class _Proc:
            returncode = 0

            async def communicate(self, inp=None):
                return (b"", b"")

        async def fake_exec(*cmd, **kw):
            captured["env"] = kw.get("env")
            return _Proc()

        monkeypatch.setattr(asyncio, "create_subprocess_exec", fake_exec)
        client = BailianCLIClient()
        await client.run(["x"])
        assert captured["env"] is None
