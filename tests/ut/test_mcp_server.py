# -*- coding: utf-8 -*-
"""Tests for the MCP server module (mcp_server.py)."""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from dashscope_agenticx.mcp_server import (
    handle_mcp_request,
    run_stdio_server,
    tools_to_mcp_schema,
)
from dashscope_agenticx.tools.registry import (
    PermissionLevel,
    ToolDefinition,
    registry,
)


# --------------------------------------------------------------------------- #
# Fixtures: register a small, deterministic set of fake tools.
# --------------------------------------------------------------------------- #
def _echo(text: str) -> str:
    return f"echo:{text}"


async def _aecho(text: str) -> str:
    return f"aecho:{text}"


def _boom(**kwargs: Any) -> str:
    raise ValueError("boom")


_FAKE_TOOLS = [
    ToolDefinition(
        name="mcp_echo",
        description="echo a string",
        permission=PermissionLevel.AUTO,
        func=_echo,
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    ToolDefinition(
        name="mcp_aecho",
        description="async echo a string",
        permission=PermissionLevel.AUTO,
        func=_aecho,
        parameters={
            "type": "object",
            "properties": {"text": {"type": "string"}},
            "required": ["text"],
        },
    ),
    ToolDefinition(
        name="mcp_boom",
        description="always raises",
        permission=PermissionLevel.AUTO,
        func=_boom,
        parameters={"type": "object", "properties": {}},
    ),
]


@pytest.fixture(autouse=True)
def register_fake_tools():
    for t in _FAKE_TOOLS:
        registry.register(t)
    yield
    for t in _FAKE_TOOLS:
        registry.unregister(t.name)


class FakeExecutor:
    """Stand-in for dashscope_agenticx.executor.Executor used by handle_mcp_request."""

    def __init__(self, result: str = "exec-ok"):
        self.result = result
        self.calls: list = []

    async def execute(self, tool_def, arguments):
        self.calls.append((tool_def, arguments))
        return self.result


def _req(method: str, req_id: Any = 1, params: dict | None = None) -> dict:
    req = {"jsonrpc": "2.0", "id": req_id, "method": method}
    if params is not None:
        req["params"] = params
    return req


# --------------------------------------------------------------------------- #
# tools_to_mcp_schema
# --------------------------------------------------------------------------- #
class TestToolsToMcpSchema:
    def test_returns_list_of_dicts(self):
        schemas = tools_to_mcp_schema()
        assert isinstance(schemas, list)
        assert len(schemas) >= 1
        for s in schemas:
            assert isinstance(s, dict)
            assert set(s.keys()) == {"name", "description", "inputSchema"}

    def test_includes_registered_fake_tools(self):
        schemas = tools_to_mcp_schema()
        names = {s["name"] for s in schemas}
        assert "mcp_echo" in names
        assert "mcp_aecho" in names
        assert "mcp_boom" in names

    def test_input_schema_passed_through(self):
        schemas = tools_to_mcp_schema()
        echo = next(s for s in schemas if s["name"] == "mcp_echo")
        assert echo["description"] == "echo a string"
        assert echo["inputSchema"]["properties"]["text"]["type"] == "string"
        assert echo["inputSchema"]["required"] == ["text"]


# --------------------------------------------------------------------------- #
# handle_mcp_request: initialize
# --------------------------------------------------------------------------- #
class TestHandleInitialize:
    async def test_initialize_response(self):
        resp = await handle_mcp_request(_req("initialize", req_id=7))
        assert resp["jsonrpc"] == "2.0"
        assert resp["id"] == 7
        result = resp["result"]
        assert result["protocolVersion"] == "2024-11-05"
        assert "tools" in result["capabilities"]
        assert result["serverInfo"]["name"] == "dashscope-agenticx"

    async def test_initialize_with_no_id(self):
        resp = await handle_mcp_request(
            {"jsonrpc": "2.0", "method": "initialize"},
        )
        assert resp["id"] is None
        assert "result" in resp


# --------------------------------------------------------------------------- #
# handle_mcp_request: tools/list
# --------------------------------------------------------------------------- #
class TestHandleToolsList:
    async def test_tools_list_returns_schema(self):
        resp = await handle_mcp_request(_req("tools/list", req_id=2))
        assert resp["id"] == 2
        tools = resp["result"]["tools"]
        assert isinstance(tools, list)
        names = {t["name"] for t in tools}
        assert "mcp_echo" in names

    async def test_tools_list_entries_have_mcp_keys(self):
        resp = await handle_mcp_request(_req("tools/list"))
        for tool in resp["result"]["tools"]:
            assert {"name", "description", "inputSchema"} <= set(tool.keys())


# --------------------------------------------------------------------------- #
# handle_mcp_request: tools/call
# --------------------------------------------------------------------------- #
class TestHandleToolsCall:
    async def test_call_success_without_executor(self):
        resp = await handle_mcp_request(
            _req(
                "tools/call",
                req_id=3,
                params={"name": "mcp_echo", "arguments": {"text": "hi"}},
            ),
        )
        assert resp["id"] == 3
        content = resp["result"]["content"]
        assert content[0]["type"] == "text"
        assert content[0]["text"] == "echo:hi"

    async def test_call_async_success_without_executor(self):
        resp = await handle_mcp_request(
            _req(
                "tools/call",
                req_id=4,
                params={"name": "mcp_aecho", "arguments": {"text": "hi"}},
            ),
        )
        assert resp["result"]["content"][0]["text"] == "aecho:hi"

    async def test_call_success_with_executor(self):
        executor = FakeExecutor(result="exec-result")
        resp = await handle_mcp_request(
            _req(
                "tools/call",
                req_id=5,
                params={"name": "mcp_echo", "arguments": {"text": "hi"}},
            ),
            executor=executor,
        )
        assert resp["result"]["content"][0]["text"] == "exec-result"
        # executor received the tool def + arguments
        assert len(executor.calls) == 1
        tool_def, args = executor.calls[0]
        assert tool_def.name == "mcp_echo"
        assert args == {"text": "hi"}

    async def test_call_unknown_tool(self):
        resp = await handle_mcp_request(
            _req(
                "tools/call",
                req_id=6,
                params={"name": "does_not_exist", "arguments": {}},
            ),
        )
        assert resp["id"] == 6
        assert resp["error"]["code"] == -32601
        assert "does_not_exist" in resp["error"]["message"]

    async def test_call_exception_returns_error(self):
        resp = await handle_mcp_request(
            _req(
                "tools/call",
                req_id=7,
                params={"name": "mcp_boom", "arguments": {}},
            ),
        )
        assert resp["id"] == 7
        assert resp["error"]["code"] == -32603
        assert resp["error"]["message"] == "boom"

    async def test_call_missing_arguments_defaults_to_empty(self):
        # params without "arguments" key -> defaults to {}
        resp = await handle_mcp_request(
            _req("tools/call", req_id=8, params={"name": "mcp_echo"}),
        )
        # _echo requires text; calling without it raises TypeError -> -32603
        assert resp["error"]["code"] == -32603


# --------------------------------------------------------------------------- #
# handle_mcp_request: unknown method
# --------------------------------------------------------------------------- #
class TestHandleUnknownMethod:
    async def test_unknown_method_returns_error(self):
        resp = await handle_mcp_request(_req("resources/read", req_id=9))
        assert resp["id"] == 9
        assert resp["error"]["code"] == -32601
        assert "resources/read" in resp["error"]["message"]

    async def test_empty_method_returns_error(self):
        resp = await handle_mcp_request(_req("", req_id=10))
        assert resp["error"]["code"] == -32601


# --------------------------------------------------------------------------- #
# run_stdio_server
# --------------------------------------------------------------------------- #
class TestRunStdioServer:
    async def test_handles_requests_and_writes_responses(self, monkeypatch):
        requests = [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list"}),
            "",  # blank line -> skipped, no response
            "not valid json",  # parse error
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "initialize"}),
        ]
        stdin = io.StringIO("\n".join(requests) + "\n")
        stdout = io.StringIO()
        monkeypatch.setattr("sys.stdin", stdin)
        monkeypatch.setattr("sys.stdout", stdout)

        await run_stdio_server()

        lines = [l for l in stdout.getvalue().split("\n") if l]
        assert len(lines) == 3  # tools/list, parse error, initialize

        resp_list = json.loads(lines[0])
        assert resp_list["id"] == 1
        assert "tools" in resp_list["result"]

        resp_parse_err = json.loads(lines[1])
        assert resp_parse_err["error"]["code"] == -32700
        assert resp_parse_err["id"] is None

        resp_init = json.loads(lines[2])
        assert resp_init["id"] == 2
        assert resp_init["result"]["protocolVersion"] == "2024-11-05"

    async def test_tools_call_over_stdio(self, monkeypatch):
        request = json.dumps(
            {
                "jsonrpc": "2.0",
                "id": 42,
                "method": "tools/call",
                "params": {"name": "mcp_echo", "arguments": {"text": "hello"}},
            },
        )
        stdin = io.StringIO(request + "\n")
        stdout = io.StringIO()
        monkeypatch.setattr("sys.stdin", stdin)
        monkeypatch.setattr("sys.stdout", stdout)

        await run_stdio_server()

        lines = [l for l in stdout.getvalue().split("\n") if l]
        assert len(lines) == 1
        resp = json.loads(lines[0])
        assert resp["id"] == 42
        assert resp["result"]["content"][0]["text"] == "echo:hello"

    async def test_empty_stdin_terminates_cleanly(self, monkeypatch):
        stdin = io.StringIO("")
        stdout = io.StringIO()
        monkeypatch.setattr("sys.stdin", stdin)
        monkeypatch.setattr("sys.stdout", stdout)

        await run_stdio_server()
        assert stdout.getvalue() == ""

    async def test_blank_lines_produce_no_output(self, monkeypatch):
        stdin = io.StringIO("\n\n\n")
        stdout = io.StringIO()
        monkeypatch.setattr("sys.stdin", stdin)
        monkeypatch.setattr("sys.stdout", stdout)

        await run_stdio_server()
        assert stdout.getvalue() == ""

    async def test_notification_without_id_gets_no_response(self, monkeypatch):
        requests = [
            json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ),
            json.dumps({"jsonrpc": "2.0", "method": "tools/list"}),  # no id
        ]
        stdin = io.StringIO("\n".join(requests) + "\n")
        stdout = io.StringIO()
        monkeypatch.setattr("sys.stdin", stdin)
        monkeypatch.setattr("sys.stdout", stdout)

        await run_stdio_server()
        assert stdout.getvalue() == ""

    async def test_request_with_id_still_gets_response(self, monkeypatch):
        requests = [
            json.dumps(
                {"jsonrpc": "2.0", "method": "notifications/initialized"},
            ),
            json.dumps({"jsonrpc": "2.0", "id": 7, "method": "tools/list"}),
        ]
        stdin = io.StringIO("\n".join(requests) + "\n")
        stdout = io.StringIO()
        monkeypatch.setattr("sys.stdin", stdin)
        monkeypatch.setattr("sys.stdout", stdout)

        await run_stdio_server()
        lines = [l for l in stdout.getvalue().split("\n") if l]
        assert len(lines) == 1
        assert json.loads(lines[0])["id"] == 7


# --------------------------------------------------------------------------- #
# CLI dispatch: `agenticx mcp-server`
# --------------------------------------------------------------------------- #
class TestCLIDispatch:
    def test_mcp_server_dispatches_to_server_main(self, monkeypatch):
        import sys

        import dashscope_agenticx.cli as cli_mod
        import dashscope_agenticx.mcp_server as mcp_mod

        called = []
        monkeypatch.setattr(
            mcp_mod,
            "main",
            lambda: called.append("mcp-server"),
        )

        def _no_repl(*args, **kwargs):
            raise AssertionError(
                "REPL/config must not start in mcp-server mode",
            )

        monkeypatch.setattr(cli_mod.Config, "load", _no_repl)
        monkeypatch.setattr(sys, "argv", ["agenticx", "mcp-server"])

        cli_mod.main()
        assert called == ["mcp-server"]
