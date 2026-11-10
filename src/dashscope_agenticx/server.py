# -*- coding: utf-8 -*-
"""Token-authenticated localhost API for AgenticX Clients."""
# pylint: disable=too-many-return-statements,protected-access
from __future__ import annotations

import asyncio
import hmac
import json
import secrets
import socket
from dataclasses import asdict
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from dashscope_agenticx.config import WORKSPACE_DIR, Config
from dashscope_agenticx.runtime import RuntimeBuilder
from dashscope_agenticx.session import SessionManager
from dashscope_agenticx.utils import sanitize

RunHandler = Callable[[dict[str, Any]], dict[str, Any]]


def generate_server_token() -> str:
    return secrets.token_urlsafe(32)


def _default_run(payload: dict[str, Any]) -> dict[str, Any]:
    async def _run() -> dict[str, Any]:
        config = Config.load()
        config.auto_approve = False
        config.confirm_mode = "all"
        runtime = RuntimeBuilder(config).build()
        try:
            with runtime.activate():
                return await _run_agent(runtime)
        finally:
            await runtime.aclose()

    async def _run_agent(runtime) -> dict[str, Any]:
        session_id = str(payload.get("session_id", "default"))
        manager = runtime.context.session_manager
        if session_id != "default" and not manager.set_current_topic(
            session_id,
        ):
            manager.create_topic(session_id)

        async def _deny_remote_permission(*args, **kwargs):
            del args, kwargs
            return "n"

        runtime.executor._confirm_callback = _deny_remote_permission
        agent = runtime.create_agent(
            system_prompt=str(payload.get("system_prompt", "")),
            session_id=session_id,
        )
        events = []
        async for event in agent.run_events(payload.get("input", "")):
            item = asdict(event)
            item["type"] = event.type.value
            events.append(sanitize(item))
        return {
            "run_id": events[0].get("run_id", "") if events else "",
            "events": events,
        }

    return asyncio.run(_run())


class LocalAPI:
    def __init__(
        self,
        token: str,
        *,
        session_manager: SessionManager | None = None,
        run_handler: RunHandler | None = None,
        trace_dir: Path | None = None,
    ):
        if len(token) < 16:
            raise ValueError(
                "localhost server token must be at least 16 chars",
            )
        self.token = token
        self.session_manager = session_manager or SessionManager()
        self.run_handler = run_handler or _default_run
        self.trace_dir = trace_dir or (WORKSPACE_DIR / "traces")

    def authorized(self, header: str) -> bool:
        expected = f"Bearer {self.token}"
        return hmac.compare_digest(
            (header or "").encode("utf-8"),
            expected.encode("utf-8"),
        )

    def get(self, path: str) -> tuple[int, dict[str, Any]]:
        parsed = urlparse(path)
        if parsed.path == "/health":
            return 200, {"status": "ok"}
        if parsed.path == "/v1/sessions":
            return 200, {
                "sessions": [
                    meta.to_dict()
                    for meta in self.session_manager.list_topics()
                ],
            }
        if parsed.path.startswith("/v1/sessions/"):
            topic = parsed.path.rsplit("/", 1)[-1]
            try:
                state = self.session_manager.project_state(topic)
            except ValueError:
                return 400, {"error": "invalid session"}
            return 200, sanitize(state)
        if parsed.path == "/v1/artifacts":
            topic = parse_qs(parsed.query).get("session_id", ["default"])[0]
            return 200, {
                "artifacts": self.session_manager.project_state(topic).get(
                    "artifacts",
                    [],
                ),
            }
        if parsed.path == "/v1/traces":
            traces = []
            if self.trace_dir.is_dir():
                traces = [
                    path.name
                    for path in sorted(self.trace_dir.glob("*.jsonl"))[-100:]
                ]
            return 200, {"traces": traces}
        return 404, {"error": "not found"}

    def post(
        self,
        path: str,
        payload: dict[str, Any],
    ) -> tuple[int, dict[str, Any]]:
        if path == "/v1/runs":
            if not payload.get("input"):
                return 400, {"error": "input is required"}
            try:
                return 200, sanitize(self.run_handler(payload))
            except Exception as exc:
                return 500, sanitize(
                    {"error": type(exc).__name__, "message": str(exc)},
                )
        if path.startswith("/v1/permissions"):
            return 403, {
                "error": "permission decisions are local-runtime only",
            }
        return 404, {"error": "not found"}


def create_server(
    host: str = "127.0.0.1",
    port: int = 8765,
    *,
    token: str,
    api: LocalAPI | None = None,
) -> ThreadingHTTPServer:
    if host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("AgenticX serve only binds to localhost")
    local_api = api or LocalAPI(token)

    class Handler(BaseHTTPRequestHandler):
        def _json(self, status: int, payload: dict[str, Any]) -> None:
            body = json.dumps(sanitize(payload), ensure_ascii=False).encode(
                "utf-8",
            )
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _authorized(self) -> bool:
            if local_api.authorized(self.headers.get("Authorization", "")):
                return True
            self._json(401, {"error": "unauthorized"})
            return False

        def do_GET(self):  # noqa: N802
            if not self._authorized():
                return
            status, payload = local_api.get(self.path)
            self._json(status, payload)

        def do_POST(self):  # noqa: N802
            if not self._authorized():
                return
            try:
                size = int(self.headers.get("Content-Length", "0"))
                if size < 0:
                    raise ValueError("negative content length")
                if size > 1_000_000:
                    self._json(413, {"error": "request body too large"})
                    return
                self.connection.settimeout(30)
                payload = json.loads(self.rfile.read(size) or b"{}")
                if not isinstance(payload, dict):
                    raise ValueError("JSON body must be an object")
            except (ValueError, json.JSONDecodeError):
                self._json(400, {"error": "invalid JSON"})
                return
            status, result = local_api.post(urlparse(self.path).path, payload)
            self._json(status, result)

        def log_message(self, fmt, *args):
            del fmt, args

    class Server(ThreadingHTTPServer):
        address_family = socket.AF_INET6 if host == "::1" else socket.AF_INET

    return Server((host, port), Handler)


def serve(
    host: str = "127.0.0.1",
    port: int = 8765,
    token: str = "",
) -> None:
    token = token or generate_server_token()
    server = create_server(host, port, token=token)
    print(f"AgenticX server: http://{host}:{server.server_port}")
    print(f"Bearer token: {token}")
    try:
        server.serve_forever()
    finally:
        server.server_close()
