# -*- coding: utf-8 -*-
"""Typed runtime events shared by CLI, TUI, SDK, and future Clients."""
from __future__ import annotations

import uuid
import contextvars
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, AsyncIterator, Callable


class AgentEventType(str, Enum):
    """Stable event names for the public runtime stream."""

    RUN_STARTED = "run.started"
    MESSAGE_DELTA = "message.delta"
    REASONING_DELTA = "reasoning.delta"
    PLAN_UPDATED = "plan.updated"
    COMPRESSION_COMPLETED = "compression.completed"
    PROVIDER_SWITCHED = "provider.switched"
    PERMISSION_REQUESTED = "permission.requested"
    PERMISSION_RESOLVED = "permission.resolved"
    TOOL_STARTED = "tool.started"
    TOOL_FINISHED = "tool.finished"
    USAGE_UPDATED = "usage.updated"
    ARTIFACT_CREATED = "artifact.created"
    SANDBOX_STATUS = "sandbox.status"
    TURN_FINISHED = "turn.finished"
    ERROR = "error"


_provider_event_sink: contextvars.ContextVar[
    Callable | None
] = contextvars.ContextVar("agenticx_provider_event_sink", default=None)


@contextmanager
def bind_provider_event_sink(sink: Callable):
    token = _provider_event_sink.set(sink)
    try:
        yield
    finally:
        _provider_event_sink.reset(token)


def emit_provider_switch(payload: dict[str, Any]) -> None:
    sink = _provider_event_sink.get()
    if sink is not None:
        sink(AgentEventType.PROVIDER_SWITCHED, payload)


@dataclass(frozen=True)
class AgentEventEnvelope:
    """Versioned immutable envelope emitted by an Agent run."""

    type: AgentEventType
    payload: dict[str, Any] = field(default_factory=dict)
    schema_version: str = "1.0"
    runtime_id: str = ""
    agent_id: str = ""
    session_id: str = ""
    run_id: str = ""
    turn_id: str = ""
    sequence: int = 0
    event_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    parent_event_id: str = ""
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
    )


# Compatibility name retained for the alpha API introduced before the
# envelope name was frozen.  Both names refer to the same runtime type.
AgentEvent = AgentEventEnvelope


class ToolDisplayText(str):
    """Legacy display text retaining the identity of its tool call."""

    tool_call_id: str
    name: str

    def __new__(cls, text: str, tool_call_id: str, name: str):
        value = super().__new__(cls, text)
        value.tool_call_id = tool_call_id
        value.name = name
        return value


async def iter_message_deltas(agent, user_input) -> AsyncIterator[str]:
    """Yield display text from the typed API with legacy-agent fallback."""
    run_events = getattr(type(agent), "run_events", None)
    if run_events is None:
        async for chunk in agent.run_stream(user_input):
            yield chunk
        return

    async for event in agent.run_events(user_input):
        if event.type == AgentEventType.MESSAGE_DELTA:
            yield str(event.payload.get("text", ""))
