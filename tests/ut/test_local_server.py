# -*- coding: utf-8 -*-
"""Phase 7 localhost API security and Client roundtrip regressions."""
from __future__ import annotations

import pytest

from dashscope_agenticx.server import LocalAPI, create_server
from dashscope_agenticx.session import SessionManager


@pytest.fixture
def local_api(tmp_path):
    manager = SessionManager(tmp_path)
    manager.record_messages_snapshot(
        [{"role": "user", "content": "hello"}],
    )
    manager.record_artifact(
        {"path": "/tmp/report.txt", "name": "report.txt", "size": 3},
    )

    def run_handler(payload):
        return {
            "run_id": "run-1",
            "events": [
                {
                    "type": "run.started",
                    "payload": {"input": payload["input"]},
                },
                {"type": "turn.finished", "payload": {"output": "done"}},
            ],
        }

    token = "test-token-at-least-16-chars"
    api = LocalAPI(
        token,
        session_manager=manager,
        run_handler=run_handler,
        trace_dir=tmp_path / "traces",
    )
    return api, token


def test_server_requires_token(local_api):
    api, token = local_api
    assert api.authorized("") is False
    assert api.authorized(f"Bearer {token}") is True


def test_browser_client_can_run_and_read_session(local_api):
    api, _ = local_api
    status, run = api.post("/v1/runs", {"input": "hello"})
    assert status == 200
    assert run["events"][-1]["payload"]["output"] == "done"

    _, sessions = api.get("/v1/sessions")
    assert sessions["sessions"][0]["topic"] == "default"
    _, artifacts = api.get("/v1/artifacts?session_id=default")
    assert artifacts["artifacts"][0]["name"] == "report.txt"


def test_remote_permission_decisions_are_forbidden(local_api):
    api, _ = local_api
    status, payload = api.post("/v1/permissions/permit", {})
    assert status == 403
    assert "local-runtime only" in payload["error"]


def test_server_refuses_non_loopback_bind():
    with pytest.raises(ValueError, match="localhost"):
        create_server("0.0.0.0", 0, token="test-token-at-least-16-chars")


def test_error_responses_are_redacted(local_api):
    api, _ = local_api

    def fail(payload):
        raise RuntimeError("password=private-value")

    api.run_handler = fail
    status, response = api.post("/v1/runs", {"input": "hello"})
    assert status == 500
    assert "private-value" not in response["message"]
    assert api.authorized("Bearer 非法内容") is False


@pytest.mark.parametrize("length,status", [("-1", 400), ("1000001", 413)])
def test_http_server_rejects_invalid_body_lengths(local_api, length, status):
    import http.client
    import threading

    api, token = local_api
    server = create_server(port=0, token=token, api=api)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    connection = http.client.HTTPConnection(
        "127.0.0.1",
        server.server_port,
        timeout=2,
    )
    try:
        connection.request(
            "POST",
            "/v1/runs",
            body=b"{}",
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Length": length,
            },
        )
        assert connection.getresponse().status == status
    finally:
        connection.close()
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_default_server_run_never_inherits_auto_approve(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from dashscope_agenticx.config import Config
    from dashscope_agenticx.events import AgentEvent, AgentEventType
    from dashscope_agenticx.executor import Executor
    from dashscope_agenticx.server import _default_run
    from dashscope_agenticx.tools.registry import (
        PermissionLevel,
        ToolDefinition,
    )

    config = Config(auto_approve=True, confirm_mode="dangerous")
    monkeypatch.setattr(
        "dashscope_agenticx.server.Config.load",
        lambda: config,
    )
    action = MagicMock(return_value="written")
    closed = AsyncMock()

    def build():
        executor = Executor(config.auto_approve, config.confirm_mode)

        async def run_events(prompt):
            outcome = await executor.execute_detailed(
                ToolDefinition(
                    name="write_side_effect",
                    description="write a file",
                    permission=PermissionLevel.CONFIRM,
                    func=action,
                ),
                {},
            )
            yield AgentEvent(
                AgentEventType.TOOL_FINISHED,
                {"success": outcome.ok},
            )

        agent = SimpleNamespace(run_events=run_events)
        return SimpleNamespace(
            executor=executor,
            context=SimpleNamespace(session_manager=None),
            create_agent=lambda **kwargs: agent,
            aclose=closed,
            activate=nullcontext,
        )

    monkeypatch.setattr(
        "dashscope_agenticx.server.RuntimeBuilder",
        lambda config: SimpleNamespace(build=build),
    )
    response = _default_run({"input": "write something"})
    action.assert_not_called()
    assert response["events"][0]["payload"]["success"] is False
    closed.assert_awaited_once()


def test_default_server_closes_runtime_when_agent_creation_fails(monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace
    from unittest.mock import AsyncMock, MagicMock

    from dashscope_agenticx.config import Config
    from dashscope_agenticx.server import _default_run

    runtime = SimpleNamespace(
        executor=MagicMock(),
        context=SimpleNamespace(session_manager=None),
        create_agent=MagicMock(side_effect=ValueError("invalid session")),
        aclose=AsyncMock(),
        activate=nullcontext,
    )
    monkeypatch.setattr(Config, "load", lambda: Config())
    monkeypatch.setattr(
        "dashscope_agenticx.server.RuntimeBuilder",
        lambda config: SimpleNamespace(build=lambda: runtime),
    )
    with pytest.raises(ValueError, match="invalid session"):
        _default_run({"input": "hello"})
    runtime.aclose.assert_awaited_once()
