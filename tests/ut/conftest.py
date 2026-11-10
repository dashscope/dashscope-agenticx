# -*- coding: utf-8 -*-
import pytest

from dashscope_agenticx.executor import Executor, classify_tool_text
from dashscope_agenticx.tools.registry import PermissionLevel, ToolDefinition


class _MemorySecretBackend:
    def __init__(self):
        self.values = {}

    def set_password(self, service, account, secret):
        self.values[(service, account)] = secret

    def get_password(self, service, account):
        return self.values.get((service, account))

    def delete_password(self, service, account):
        self.values.pop((service, account), None)


@pytest.fixture(autouse=True)
def isolated_secret_store():
    """Never let unit tests read or mutate the developer's OS keyring."""
    from dashscope_agenticx.utils.secret_store import set_secret_backend

    backend = _MemorySecretBackend()
    set_secret_backend(backend)
    try:
        yield backend
    finally:
        set_secret_backend(None)


@pytest.fixture(autouse=True)
def isolated_generation_path_cache(monkeypatch, tmp_path):
    """Keep TongyiProvider's learned model→path cache off the real ~/.agenticx.

    The cache is persisted across processes by design, so without this a test
    run would write bindings into the developer's home directory — and would
    read back whatever is already there, making the probe-order assertions
    depend on machine state instead of on the code.
    """
    from dashscope_agenticx import config as config_module
    from dashscope_agenticx.providers.tongyi import TongyiProvider

    monkeypatch.setenv("AGENTICX_DISABLE_MIGRATION", "1")
    monkeypatch.setattr(
        config_module,
        "CONFIG_DIR",
        tmp_path / "agenticx-home",
    )
    # Class-level state survives across tests; monkeypatch restores the
    # previous value on teardown, so each test starts from "nothing learned".
    monkeypatch.setattr(TongyiProvider, "_path_cache", None)


@pytest.fixture(autouse=True)
def acceptance_gate_off(monkeypatch):
    """Keep the oneshot acceptance gate disarmed unless a test arms it.

    The gate discovers the acceptance command from the working directory, and
    unit tests run inside this repository — so a oneshot test that drives one
    mutating tool call would otherwise shell out to the real pytest suite to
    capture its baseline.
    """
    monkeypatch.delenv("AGENTICX_ACCEPTANCE_CMD", raising=False)
    monkeypatch.setenv("AGENTICX_ACCEPTANCE", "0")


def script_outcomes(results):
    """Adapt scripted executor results to the structured executor API.

    Agent-loop tests script what the executor "returns" as plain strings or
    exceptions, but ``execute_detailed`` yields a ``ToolOutcome``. Strings go
    through the production classifier so a test asserting on a failed tool
    call exercises the same verdict path a real result takes; exceptions pass
    through untouched so ``AsyncMock`` still raises them.
    """
    return [
        classify_tool_text(r) if isinstance(r, str) else r for r in results
    ]


@pytest.fixture
def make_tool():
    def _make(
        name: str = "test_tool",
        permission: PermissionLevel = PermissionLevel.CONFIRM,
        func=None,
        parameters: dict = None,
    ) -> ToolDefinition:
        return ToolDefinition(
            name=name,
            description="test tool",
            permission=permission,
            func=func or (lambda **kw: "ok"),
            parameters=parameters or {},
        )

    return _make


@pytest.fixture
def make_executor():
    def _make(
        auto_approve: bool = False,
        callback_result: str = "y",
        confirm_mode: str = "all",
    ) -> Executor:
        # Most executor unit tests assert on the confirmation-prompt
        # behavior of CONFIRM-level tools, which only fires in
        # confirm_mode="all"; the production default is "dangerous"
        # (see TestConfirmMode below), so pin the classic mode here.
        ex = Executor(auto_approve=auto_approve, confirm_mode=confirm_mode)

        async def fake_callback(tool_def, arguments, is_dangerous):
            return callback_result

        ex._confirm_callback = fake_callback
        return ex

    return _make
