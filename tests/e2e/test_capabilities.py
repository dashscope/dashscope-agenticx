# -*- coding: utf-8 -*-
"""End-to-end tests for capability management commands."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestCapabilities:
    async def test_capability_list(self, dispatch):
        _, _, _, captured = await dispatch("/capability list")
        assert "Capability status" in captured.out
        assert "bailian.mcp" in captured.out

    async def test_capability_disable_then_enable(self, dispatch):
        await dispatch("/capability enable local.subagent")
        _, _, config, captured = await dispatch(
            "/capability disable local.subagent",
        )
        assert "Disabled" in captured.out
        assert "local.subagent" not in config.enabled_capabilities

        _, _, config2, captured2 = await dispatch(
            "/capability enable local.subagent",
        )
        assert "Enabled" in captured2.out
        assert "local.subagent" in config2.enabled_capabilities

    async def test_capability_reload(self, dispatch):
        _, _, _, captured = await dispatch("/capability reload")
        assert "Capability registry reloaded" in captured.out
