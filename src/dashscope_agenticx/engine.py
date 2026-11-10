# -*- coding: utf-8 -*-
# pylint: disable=protected-access,too-many-statements
"""Composable execution services used by the Agent runtime.

The public ``Agent`` remains the lifecycle owner, while the mechanics that
change independently (model streaming, guard hints, tool batching, and turn
persistence) live here.  Keeping these services stateless makes them safe to
reuse across runtime instances and gives later frontends a stable seam for
observability and policy injection.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass, field, replace
from typing import Any, AsyncIterator

from dashscope_agenticx.compression import (
    estimate_message_tokens,
    estimate_tokens,
)
from dashscope_agenticx.events import AgentEventType, ToolDisplayText
from dashscope_agenticx.providers.base import LLMChunk, ToolCall
from dashscope_agenticx.providers.hardening import (
    RetriesExhausted,
    is_retryable_error,
)
from dashscope_agenticx.utils import (
    UserAbortedTurn,
    UserSupplement,
    normalize_for_model,
    text_of,
    tool_result_for_display,
    truncate,
)
from dashscope_agenticx.utils.validation import normalize_tool_arguments


def _budget_burned_slowly(error: BaseException) -> bool:
    """Whether the provider already consumed a meaningful retry budget."""
    return isinstance(error, RetriesExhausted) and error.exhausted_slowly


@dataclass(frozen=True)
class ModelDelta:
    """One displayable delta produced during a model invocation."""

    text: str


@dataclass(frozen=True)
class ModelInvocation:
    """The fully assembled result of one streaming model invocation."""

    content: str
    reasoning_content: str
    tool_calls: list[ToolCall]
    finish_reason: str
    usage: dict[str, Any] | None = None


class ModelInvoker:
    """Own provider streaming, retry, usage, and LLM trace bookkeeping."""

    @staticmethod
    def _normalize_tool_call(agent: Any, tool_call: ToolCall) -> ToolCall:
        tool_registry = getattr(agent, "tool_registry", None)
        tool_def = (
            tool_registry.get(tool_call.name)
            if tool_registry is not None
            else None
        )
        if tool_def is None or tool_call.truncated:
            return tool_call
        return replace(
            tool_call,
            arguments=normalize_tool_arguments(tool_def, tool_call.arguments),
        )

    async def invoke(
        self,
        agent: Any,
        request_messages: list[dict],
        tools_schema: list[dict],
        *,
        hint: str = "",
        hint_chars: dict[str, int] | None = None,
    ) -> AsyncIterator[ModelDelta | ModelInvocation]:
        full_content = ""
        full_reasoning = ""
        tool_calls: list[ToolCall] = []
        seen_tool_calls: set[tuple[str, str]] = set()
        last_chunk: LLMChunk | None = None
        finish_reason = ""
        llm_start = time.monotonic()
        turn_attempt = 0

        while True:
            emitted = False
            try:
                async for chunk in agent.provider.chat_stream(
                    normalize_for_model(request_messages, agent.model_name),
                    tools_schema,
                    response_format=(
                        {"type": "json_object"} if agent.json_mode else None
                    ),
                ):
                    if chunk.delta_content:
                        emitted = True
                        full_content += chunk.delta_content
                        yield ModelDelta(chunk.delta_content)
                    if chunk.delta_reasoning_content:
                        full_reasoning += chunk.delta_reasoning_content
                        agent._emit_event(
                            AgentEventType.REASONING_DELTA,
                            {"text": chunk.delta_reasoning_content},
                        )
                    if chunk.tool_calls:
                        emitted = True
                        for tool_call in chunk.tool_calls:
                            tool_call = self._normalize_tool_call(
                                agent,
                                tool_call,
                            )
                            key = (
                                tool_call.name,
                                json.dumps(
                                    tool_call.arguments,
                                    sort_keys=True,
                                    ensure_ascii=False,
                                ),
                            )
                            if key in seen_tool_calls:
                                continue
                            seen_tool_calls.add(key)
                            tool_calls.append(tool_call)
                    if chunk.usage:
                        agent._emit_event(  # pylint: disable=protected-access
                            AgentEventType.USAGE_UPDATED,
                            dict(chunk.usage),
                        )
                        last_chunk = chunk
                    if chunk.finish_reason:
                        finish_reason = chunk.finish_reason
                break
            except Exception as exc:
                if (
                    not emitted
                    and is_retryable_error(exc)
                    and turn_attempt < agent.turn_retry_max
                    and not _budget_burned_slowly(exc)
                ):
                    turn_attempt += 1
                    full_content = ""
                    full_reasoning = ""
                    tool_calls = []
                    seen_tool_calls = set()
                    last_chunk = None
                    finish_reason = ""
                    await asyncio.sleep(
                        min(
                            agent.turn_retry_delay * (2 ** (turn_attempt - 1)),
                            agent.turn_retry_backoff_cap,
                        ),
                    )
                    continue
                if emitted and is_retryable_error(exc):
                    warning = (
                        f"\n[Stream interrupted ({exc}); continuing with "
                        "the partial response]\n"
                    )
                    yield ModelDelta(warning)
                    break
                raise

        agent.executor.record_api_call(
            last_chunk.usage if last_chunk and last_chunk.usage else None,
        )
        prompt_stats = agent.executor.record_prompt_composition(
            request_messages,
            tools_schema,
        )
        prompt_stats["est_prompt_tokens"] = estimate_message_tokens(
            request_messages,
        ) + estimate_tokens(json.dumps(tools_schema, ensure_ascii=False))
        if hint_chars:
            prompt_stats["hint_chars"] = hint_chars
        prompt_stats["prefix_chars"] = (
            prompt_stats["system_chars"]
            + prompt_stats["user_chars"]
            + prompt_stats["assistant_chars"]
            + prompt_stats["tool_result_chars"]
            + prompt_stats["tool_call_args_chars"]
            + prompt_stats["tools_schema_chars"]
            - len(hint)
        )
        usage = last_chunk.usage if last_chunk and last_chunk.usage else None
        agent.trace_logger.log_llm_call(
            messages=request_messages,
            tools=tools_schema,
            response={
                "content": full_content,
                "tool_calls": [
                    {"name": call.name, "arguments": call.arguments}
                    for call in tool_calls
                ],
                "usage": usage,
            },
            duration_ms=int((time.monotonic() - llm_start) * 1000),
            model=agent.model_name,
            prompt_stats=prompt_stats,
        )
        yield ModelInvocation(
            content=full_content,
            reasoning_content=full_reasoning,
            tool_calls=tool_calls,
            finish_reason=finish_reason,
            usage=usage,
        )


class RunGuards:
    """Build volatile loop hints and enforce non-provider run limits."""

    def __init__(self):
        self.reflection = None
        self.stagnation = None

    def bind(self, reflection, stagnation) -> None:
        """Own the per-run guard trackers exposed by compatibility memory."""
        self.reflection = reflection
        self.stagnation = stagnation

    def build_hint(
        self,
        agent: Any,
        loop_index: int,
    ) -> tuple[str, dict[str, int]]:
        parts = {
            "reflection": agent._reflection_section(),
            "stagnation": agent._stagnation_section(),
            "convergence": agent._convergence_section(loop_index),
            "time_budget": agent._time_budget_section(),
            "compression": agent._compression_section(),
            "silence": agent._silence_section(),
        }
        return (
            "".join(parts.values()).strip(),
            {name: len(value) for name, value in parts.items() if value},
        )

    def readonly_stop(self, agent: Any) -> str:
        if (
            agent.oneshot
            and agent.readonly_hard_cap
            and self.stagnation is not None
            and self.stagnation.readonly_streak >= agent.readonly_hard_cap
        ):
            return (
                f"\n[Stopped: {agent.readonly_hard_cap} consecutive "
                "read-only tool calls with no progress. Ending the run "
                "to avoid an infinite verification loop.]\n"
            )
        return ""


@dataclass
class ToolBatchResult:
    """Rendered tool output and whether the model should immediately replan."""

    output: list[str] = field(default_factory=list)
    replan: bool = False


class ToolCoordinator:
    """Coordinate calls while preserving assistant/tool message pairing."""

    async def execute(
        self,
        agent: Any,
        tool_calls: list[ToolCall],
        messages_with_system: list[dict],
    ) -> ToolBatchResult:
        result = ToolBatchResult()
        any_needs_confirm = any(
            agent._needs_confirmation(call) for call in tool_calls
        )
        if len(tool_calls) > 1 and not any_needs_confirm:
            values = await asyncio.gather(
                *[agent._execute_tool(call) for call in tool_calls],
                return_exceptions=True,
            )
            supplement_info = None
            aborted: UserAbortedTurn | None = None
            for tool_call, value in zip(tool_calls, values):
                if isinstance(value, UserAbortedTurn):
                    aborted = aborted or value
                    value = "Cancelled (user aborted this turn)"
                elif isinstance(value, UserSupplement):
                    supplement_info = value.supplement
                    value = "Cancelled (user added info to replan)"
                elif isinstance(value, Exception):
                    value = f"Error: {value}"

                result.output.append(
                    ToolDisplayText(
                        f"\n[{tool_call.name}] →\n"
                        f"{tool_result_for_display(tool_call.name, value)}\n",
                        tool_call.id,
                        tool_call.name,
                    ),
                )
                tool_msg = agent._tool_message(tool_call, value)
                agent.messages.append(tool_msg)
                messages_with_system.append(tool_msg)

            if aborted:
                raise aborted
            if supplement_info:
                supplement_msg = {
                    "role": "user",
                    "content": (
                        f"[User supplement]: {supplement_info}\n"
                        "Replan with the new info and continue."
                    ),
                }
                agent.messages.append(supplement_msg)
                messages_with_system.append(supplement_msg)
                result.replan = True
            return result

        answered_ids: set[str] = set()
        try:
            for tool_call in tool_calls:
                value = await agent._execute_tool(tool_call)
                rendered = tool_result_for_display(tool_call.name, value)
                result.output.append(
                    ToolDisplayText(
                        f"\n[{tool_call.name}] →\n{rendered}\n",
                        tool_call.id,
                        tool_call.name,
                    ),
                )
                tool_msg = agent._tool_message(tool_call, value)
                agent.messages.append(tool_msg)
                messages_with_system.append(tool_msg)
                answered_ids.add(tool_call.id)
        except UserSupplement as exc:
            agent._close_pending_tool_calls(
                tool_calls,
                answered_ids,
                messages_with_system,
            )
            supplement_text = (
                f"[User supplement]: {exc.supplement}\n"
                "Replan with the new info and continue."
            )
            result.output.append(f"\n{supplement_text}\n")
            supplement_msg = {"role": "user", "content": supplement_text}
            agent.messages.append(supplement_msg)
            messages_with_system.append(supplement_msg)
            result.replan = True
        except UserAbortedTurn:
            agent._close_pending_tool_calls(
                tool_calls,
                answered_ids,
                messages_with_system,
                reason="Cancelled (user aborted this turn)",
            )
            raise
        return result


class SessionPersistence:
    """Commit completed turn projections, memory, and learned experience."""

    async def finalize(
        self,
        agent: Any,
        user_input: Any,
        last_content: str,
    ) -> None:
        if agent.session_path is not None:
            try:
                turn_topic = agent.session_path.parent.name
                outcome = ""
                if agent._current_turn_tools:
                    outcome = agent._classify_outcome(
                        agent._turn_tool_successes,
                        agent._turn_tool_failures,
                    )
                agent.session_manager.record_turn_event(
                    user_text=text_of(user_input),
                    assistant_text=last_content,
                    tools_used=agent._current_turn_tools,
                    outcome=outcome,
                    topic=turn_topic,
                    identifiers=dict(agent._event_context),
                )
                agent.session_manager.record_messages_snapshot(
                    agent.messages,
                    topic=turn_topic,
                    identifiers=dict(agent._event_context),
                )
            except Exception:
                pass

        agent.save_session()
        if last_content:
            await agent._store_memory(
                [
                    {"role": "user", "content": text_of(user_input)},
                    {"role": "assistant", "content": last_content},
                ],
            )
        if not agent._current_turn_tools:
            return
        task_summary = truncate(" ".join(text_of(user_input).split()), 100)
        outcome = agent._classify_outcome(
            agent._turn_tool_successes,
            agent._turn_tool_failures,
        )
        agent.memory_manager.record_experience(
            task_summary=task_summary,
            tools_used=agent._current_turn_tools,
            outcome=outcome,
            verified=outcome in {"success", "failure"},
        )
        if outcome != "success":
            return

        import os

        if os.environ.get("AGENTICX_EXPERIMENTAL_LEARNING", "") != "1":
            return

        from dashscope_agenticx.memory.directives_learning import (
            _load_patterns,
            analyze_patterns,
            propose_directive,
            record_tool_sequence,
        )

        record_tool_sequence(agent._current_turn_tools)
        try:
            seq_count = len(_load_patterns().get("sequences", []))
            if seq_count >= 5 and seq_count % 5 == 0:
                for proposal in analyze_patterns():
                    propose_directive(
                        proposal["directive"],
                        proposal["pattern"],
                    )
        except Exception:
            pass
