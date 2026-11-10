# -*- coding: utf-8 -*-
"""Tests for the MCP stdio transport client (mcp_stdio.py).

A tiny fake MCP server (newline-delimited JSON-RPC 2.0 on stdio) is
written to a temp file and driven through a real subprocess by
StdioMCPClient.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from dashscope_agenticx.mcp_stdio import MCPError, StdioMCPClient

_FAKE_SERVER = """
import json
import sys


def respond(req_id, result):
    msg = {"jsonrpc": "2.0", "id": req_id, "result": result}
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()


def respond_error(req_id, code, message):
    msg = {"jsonrpc": "2.0", "id": req_id,
           "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(msg) + "\\n")
    sys.stdout.flush()


TOOLS = [{
    "name": "echo",
    "description": "echo text back",
    "inputSchema": {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    },
}]

for line in sys.stdin:
    line = line.strip()
    if not line:
        continue
    try:
        req = json.loads(line)
    except json.JSONDecodeError:
        continue
    method = req.get("method")
    req_id = req.get("id")
    if req_id is None:
        continue  # notification
    if method == "initialize":
        respond(req_id, {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "serverInfo": {"name": "fake-server", "version": "0.0.1"},
        })
    elif method == "tools/list":
        respond(req_id, {"tools": TOOLS})
    elif method == "tools/call":
        params = req.get("params", {})
        name = params.get("name")
        args = params.get("arguments", {})
        if name == "echo":
            respond(req_id, {"content": [
                {"type": "text", "text": "echo:" + str(
                    args.get("text", ""))},
                {"type": "json", "data": {"ok": True}},
            ]})
        elif name == "boom":
            respond_error(req_id, -32603, "boom failure")
        else:
            respond_error(req_id, -32601, "unknown tool: " + str(name))
    elif method == "prompts/list":
        respond(req_id, {"prompts": []})
    else:
        respond_error(req_id, -32601, "unknown method: " + str(method))
"""


@pytest.fixture
def fake_server_path(tmp_path: Path) -> Path:
    script = tmp_path / "fake_mcp_server.py"
    script.write_text(_FAKE_SERVER, encoding="utf-8")
    return script


def _make_client(path: Path, timeout: float = 10.0) -> StdioMCPClient:
    return StdioMCPClient(sys.executable, [str(path)], timeout=timeout)


class TestConnectAndListTools:
    async def test_connect_and_list_tools(self, fake_server_path: Path):
        client = _make_client(fake_server_path)
        try:
            assert await client.initialize() is True
            assert client.last_error == ""
            tools = await client.list_tools()
            assert len(tools) == 1
            assert tools[0]["name"] == "echo"
            assert "inputSchema" in tools[0]
            assert client.tools == tools
        finally:
            await client.close()

    async def test_list_prompts_unsupported_returns_empty(
        self,
        fake_server_path: Path,
    ):
        client = _make_client(fake_server_path)
        try:
            assert await client.initialize() is True
            assert await client.list_prompts() == []
        finally:
            await client.close()


class TestCallTool:
    async def test_call_tool_text_result(self, fake_server_path: Path):
        client = _make_client(fake_server_path)
        try:
            assert await client.initialize() is True
            result = await client.call_tool("echo", {"text": "hello"})
            # text block kept as text; non-text block JSON-dumped
            assert "echo:hello" in result
            assert '"ok": true' in result
        finally:
            await client.close()

    async def test_call_tool_error_returns_message(
        self,
        fake_server_path: Path,
    ):
        client = _make_client(fake_server_path)
        try:
            assert await client.initialize() is True
            result = await client.call_tool("boom", {})
            assert result.startswith("MCP Error:")
            assert "boom failure" in result
        finally:
            await client.close()


class TestErrorPaths:
    async def test_server_exit_fails_initialize(self, tmp_path: Path):
        # Server exits right after reading one line, never responding.
        script = tmp_path / "dying_server.py"
        script.write_text("import sys\nsys.stdin.readline()\nsys.exit(1)\n")
        client = _make_client(script)
        # initialize() mirrors the SSE contract: False + last_error.
        assert await client.initialize() is False
        assert client.last_error
        await client.close()  # must be safe after failed init

    async def test_request_after_death_raises(self, tmp_path: Path):
        # Server answers initialize, then dies: next request must raise.
        script = tmp_path / "die_after_init.py"
        script.write_text(
            "import json\n"
            "import sys\n"
            "line = sys.stdin.readline()\n"
            "req = json.loads(line)\n"
            "msg = {'jsonrpc': '2.0', 'id': req['id'], 'result': {}}\n"
            "sys.stdout.write(json.dumps(msg) + '\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stdin.readline()  # notifications/initialized\n"
            "sys.exit(1)\n",
        )
        client = _make_client(script)
        try:
            assert await client.initialize() is True
            with pytest.raises(MCPError):
                await client.list_tools()
        finally:
            await client.close()

    async def test_request_timeout(self, tmp_path: Path):
        # Server answers initialize then sleeps forever on tools/list.
        script = tmp_path / "slow_server.py"
        script.write_text(
            "import json\n"
            "import sys\n"
            "import time\n"
            "line = sys.stdin.readline()\n"
            "req = json.loads(line)\n"
            "msg = {'jsonrpc': '2.0', 'id': req['id'], 'result': {}}\n"
            "sys.stdout.write(json.dumps(msg) + '\\n')\n"
            "sys.stdout.flush()\n"
            "sys.stdin.readline()\n"
            "time.sleep(30)\n",
        )
        client = StdioMCPClient(
            sys.executable,
            [str(script)],
            timeout=0.5,
        )
        try:
            assert await client.initialize() is True
            with pytest.raises(MCPError, match="timed out"):
                await client.list_tools()
        finally:
            await client.close()

    async def test_missing_command_raises(self):
        with pytest.raises(MCPError):
            StdioMCPClient("", [])


class TestClose:
    async def test_close_terminates_process(self, fake_server_path: Path):
        client = _make_client(fake_server_path)
        assert await client.initialize() is True
        proc = client._proc
        assert proc is not None and proc.returncode is None
        await client.close()
        assert proc.returncode is not None
        # double close is safe
        await client.close()
