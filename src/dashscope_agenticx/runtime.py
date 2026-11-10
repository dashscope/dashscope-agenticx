# -*- coding: utf-8 -*-
"""Runtime construction and instance-local state for Agent entrypoints.

``RuntimeBuilder`` is the 1.0 migration seam: CLI, TUI, SDK and embedded hosts
will progressively move behind it. The first increment isolates the tool and
skill registries while preserving the existing module-level facade for legacy
callers and decorators.
"""
from __future__ import annotations

import importlib.util
import asyncio
import uuid
import copy
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

# pylint: disable=unused-import,protected-access

if TYPE_CHECKING:
    from dashscope_agenticx.agent import Agent
    from dashscope_agenticx.config import Config
    from dashscope_agenticx.hooks import HookBus
    from dashscope_agenticx.platforms.base import MemoryProvider
    from dashscope_agenticx.providers.base import LLMProvider
    from dashscope_agenticx.session import SessionManager
    from dashscope_agenticx.skills.manager import SkillManager
    from dashscope_agenticx.tools.registry import ToolRegistry


def import_builtin_tools() -> None:
    """Populate the default registry with all built-in static tools."""
    import dashscope_agenticx.tools.filesystem  # noqa: F401
    import dashscope_agenticx.tools.shell  # noqa: F401
    import dashscope_agenticx.tools.web_search  # noqa: F401

    if importlib.util.find_spec("playwright") is not None:
        import dashscope_agenticx.tools.browser  # noqa: F401
    if importlib.util.find_spec("cv2") is not None:
        import dashscope_agenticx.tools.camera  # noqa: F401


def prepare_extensions(config: Config) -> None:
    """Load declarative providers, capabilities, shell tools, and skills."""
    from dashscope_agenticx.config import PROVIDER_MODELS
    from dashscope_agenticx.extensions import apply_extensions

    del config  # retained for a stable runtime-preparation contract
    apply_extensions(PROVIDER_MODELS)


def create_runtime_memory(config: Config) -> MemoryProvider | None:
    """Create and publish the configured memory provider, if enabled."""
    if not config.memory_enabled:
        return None
    from dashscope_agenticx.platforms import get_memory_provider

    return get_memory_provider(config)


@dataclass
class RuntimeContext:
    """Instance-local mutable registries owned by one Runtime."""

    runtime_id: str = field(default_factory=lambda: uuid.uuid4().hex)
    tool_registry: ToolRegistry | None = None
    skill_manager: SkillManager | None = None
    hook_bus: HookBus | None = None
    session_manager: SessionManager | None = None
    capability_tools: dict[str, set[str]] = field(default_factory=dict)
    mcp_clients: dict = field(default_factory=dict)
    permission_policy: object | None = None
    extensions: object | None = None

    @classmethod
    def create(cls, config: Config) -> "RuntimeContext":
        """Create an isolated context from the imported built-in baseline."""
        del config  # reserved for later policy/session factories
        import_builtin_tools()

        from dashscope_agenticx.hooks import create_hook_bus
        from dashscope_agenticx.session import SessionManager
        from dashscope_agenticx.skills.manager import SkillManager
        from dashscope_agenticx.tools.registry import registry
        from dashscope_agenticx.permission import (
            get_permission_policy,
            load_permission_policy,
        )
        from dashscope_agenticx.extensions import current

        tool_registry = registry.clone()
        return cls(
            runtime_id=uuid.uuid4().hex,
            tool_registry=tool_registry,
            skill_manager=SkillManager(tool_registry=tool_registry),
            hook_bus=create_hook_bus(),
            session_manager=SessionManager(),
            capability_tools={},
            permission_policy=load_permission_policy(
                copy.deepcopy(get_permission_policy()),
            ),
            extensions=copy.deepcopy(current()),
        )


@dataclass
class Runtime:
    """A configured provider/executor plus isolated runtime state."""

    config: Config
    provider: LLMProvider
    executor: object
    context: RuntimeContext
    memory: MemoryProvider | None = None
    _closed: bool = field(default=False, init=False, repr=False)
    _agent_count: int = field(default=0, init=False, repr=False)
    _managed_providers: dict[int, object] = field(
        default_factory=dict,
        init=False,
        repr=False,
    )

    def track_provider(self, provider) -> None:
        if provider is not None:
            if self._closed:
                raise RuntimeError("Runtime is closed")
            self._managed_providers[id(provider)] = provider

    def _set_memory_client(self, client) -> None:
        self.track_provider(client)
        self.memory = client

    @contextmanager
    def activate(self, *, tool_registry=None):
        """Bind runtime-owned state for frontend commands and tool calls."""
        if self._closed:
            raise RuntimeError("Runtime is closed")
        from dashscope_agenticx.cli.mcp import bind_mcp_clients
        from dashscope_agenticx.tools.platform import bind_capability_tools
        from dashscope_agenticx.tools.registry import bind_tool_registry
        from dashscope_agenticx.session import bind_session_manager
        from dashscope_agenticx.permission import bind_permission_policy
        from dashscope_agenticx.extensions import bind_extensions
        from dashscope_agenticx.cli.handlers_profile import bind_memory_client

        with ExitStack() as stack:
            stack.enter_context(
                bind_tool_registry(
                    tool_registry
                    if tool_registry is not None
                    else self.context.tool_registry,
                ),
            )
            stack.enter_context(bind_mcp_clients(self.context.mcp_clients))
            stack.enter_context(
                bind_capability_tools(self.context.capability_tools),
            )
            stack.enter_context(
                bind_session_manager(self.context.session_manager),
            )
            stack.enter_context(
                bind_permission_policy(self.context.permission_policy),
            )
            stack.enter_context(bind_extensions(self.context.extensions))
            stack.enter_context(
                bind_memory_client(self.memory, self._set_memory_client),
            )
            yield self

    async def aclose(self) -> None:
        """Release only this runtime's MCP connections and provider clients."""
        if self._closed:
            return
        from dashscope_agenticx.providers.base import close_provider

        clients = list(self.context.mcp_clients.values())
        self.context.mcp_clients.clear()
        resources = {
            id(resource): resource
            for resource in (
                self.provider,
                self.memory,
                *clients,
                *self._managed_providers.values(),
            )
            if resource is not None
        }
        self._managed_providers.clear()
        self._closed = True
        results = await asyncio.gather(
            *(close_provider(resource) for resource in resources.values()),
            return_exceptions=True,
        )
        for result in results:
            if isinstance(result, BaseException):
                raise result

    async def __aenter__(self):
        if self._closed:
            raise RuntimeError("Runtime is closed")
        return self

    async def __aexit__(self, exc_type, exc, traceback):
        await self.aclose()

    def create_agent(
        self,
        system_prompt: str,
        session_id: str | None = None,
        *,
        oneshot: bool = False,
    ) -> Agent:
        """Construct and fully wire an Agent owned by this Runtime."""
        if self._closed:
            raise RuntimeError("Runtime is closed")
        from dashscope_agenticx.agent import Agent
        from dashscope_agenticx.tools.platform import (
            disabled_capabilities_hint,
        )

        session_path: Path | None = None
        if session_id:
            session_path = self.context.session_manager.get_history_path(
                session_id,
            )

        tool_registry = self.context.tool_registry
        if self._agent_count:
            tool_registry = tool_registry.clone()
        agent = Agent(
            provider=self.provider,
            executor=self.executor
            if self._agent_count == 0
            else self.executor.fork_for_child(),
            max_turns=self.config.max_turns,
            memory=self.memory,
            user_name=self.config.user_name,
            provider_name=self.config.provider,
            model_name=self.config.model,
            system_prompt=system_prompt,
            session_path=session_path,
            disabled_caps_provider=lambda: disabled_capabilities_hint(
                self.config,
            ),
            directives_provider=lambda: self.config.user_directives,
            hook_bus=self.context.hook_bus,
            tool_registry=tool_registry,
            skill_manager=self.context.skill_manager,
            session_manager=self.context.session_manager,
            runtime_config=self.config,
            runtime_id=self.context.runtime_id,
            capability_tools=self.context.capability_tools,
            oneshot=oneshot,
        )
        with self.activate(tool_registry=tool_registry):
            wire_agent_runtime(
                self.config,
                agent,
                self.context.hook_bus,
                tool_registry=tool_registry,
                skill_manager=self.context.skill_manager,
                capability_tools=self.context.capability_tools,
                mcp_clients=self.context.mcp_clients,
            )
        # Keep the owning Runtime alive for future lifecycle/close APIs.
        agent.runtime = self
        self.track_provider(agent.provider)
        self._agent_count += 1
        return agent


class RuntimeBuilder:
    """Build a Runtime without leaking mutable registries across instances."""

    def __init__(
        self,
        config: Config,
        *,
        extensions_prepared: bool = False,
    ):
        self.config = config
        self.extensions_prepared = extensions_prepared

    def build(self) -> Runtime:
        """Prepare extensions, provider, executor, memory, and context."""
        from dashscope_agenticx.executor import Executor
        from dashscope_agenticx.providers import get_provider_chain

        if not self.extensions_prepared:
            prepare_extensions(self.config)
        context = RuntimeContext.create(self.config)
        return Runtime(
            config=self.config,
            provider=get_provider_chain(self.config),
            executor=Executor(
                auto_approve=self.config.auto_approve,
                confirm_mode=self.config.confirm_mode,
            ),
            context=context,
            memory=create_runtime_memory(self.config),
        )


def wire_child_tools(parent: Agent, child: Agent) -> None:
    from dashscope_agenticx.tools.registry import bind_tool_registry
    from dashscope_agenticx.tools.session import register_plan_tools
    from dashscope_agenticx.tools.skills import register_skill_tools

    child.tool_registry = parent.tool_registry.clone()
    for name in (
        "switch_model",
        "switch_provider",
        "enable_capability",
        "disable_capability",
        "mcp_connect",
        "mcp_disconnect",
    ):
        child.tool_registry.unregister(name)
    with bind_tool_registry(child.tool_registry):
        register_plan_tools(get_agent=lambda: child)
        register_skill_tools(get_agent=lambda: child)


def wire_agent_runtime(
    config: Config,
    agent: Agent,
    hook_bus: HookBus | None = None,
    *,
    tool_registry=None,
    skill_manager=None,
    capability_tools: dict[str, set[str]] | None = None,
    mcp_clients: dict | None = None,
) -> None:
    """Attach every dynamic tool/capability family to an existing Agent."""
    import_builtin_tools()

    from dashscope_agenticx.agents.delegate import bind_delegate_context
    from dashscope_agenticx.agents.subagent import bind_subagent_context
    from dashscope_agenticx.tools.platform import bind_capability_tools
    from dashscope_agenticx.tools.registry import bind_tool_registry, registry
    from dashscope_agenticx.cli.mcp import bind_mcp_clients

    target_registry = tool_registry or registry.current()
    capability_tracker = (
        capability_tools if capability_tools is not None else {}
    )
    with (
        bind_tool_registry(target_registry),
        bind_capability_tools(capability_tracker),
        bind_subagent_context(agent, config),
        bind_delegate_context(agent, config),
        bind_mcp_clients(mcp_clients if mcp_clients is not None else {}),
    ):
        from dashscope_agenticx.cli.mcp import (
            _connect_mcp,
            _disconnect_mcp,
            _mcp_clients,
        )
        from dashscope_agenticx.providers import get_provider
        from dashscope_agenticx.skills import (
            get_skill_manager,
            list_known_services,
        )
        from dashscope_agenticx.tools.platform import register_platform_tools

        register_platform_tools(config, connect_mcp_fn=_connect_mcp)

        manager = skill_manager or get_skill_manager()
        manager.tool_registry = target_registry
        manager._registry_url = getattr(config, "skill_registry", "")
        manager._global = False
        manager.load(hook_bus=hook_bus or agent.hook_bus)

        from dashscope_agenticx.tools.session import register_session_tools

        register_session_tools(
            config,
            get_agent=lambda: agent,
            get_provider_fn=get_provider,
            connect_mcp_fn=_connect_mcp,
            disconnect_mcp_fn=_disconnect_mcp,
            list_mcp_services_fn=list_known_services,
            get_mcp_clients_fn=lambda: mcp_clients
            if mcp_clients is not None
            else _mcp_clients,
        )

        from dashscope_agenticx.tools.vision import register_vision_tools

        register_vision_tools(config, get_provider_fn=get_provider)

        import os

        from dashscope_agenticx.tools.skills import register_skill_tools

        if os.environ.get("AGENTICX_EXPERIMENTAL", "") == "1":
            from dashscope_agenticx.tools.evolution import (
                register_evolution_tools,
            )

            register_evolution_tools(get_agent=lambda: agent)
        register_skill_tools(get_agent=lambda: agent)
