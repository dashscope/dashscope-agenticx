# -*- coding: utf-8 -*-
"""End-to-end tests for /memory and /session persistence."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.e2e


class TestMemorySession:
    async def test_memory_shows_stats(self, dispatch):
        _, _, _, captured = await dispatch("/memory")
        assert "Chat history" in captured.out

    async def test_memory_list_empty(self, dispatch):
        _, _, _, captured = await dispatch("/memory list")
        assert "No conversation history yet" in captured.out

    async def test_session_new_and_switch(self, dispatch):
        await dispatch("/session new topic-a")
        agent, _, _, captured = await dispatch("/session switch topic-a")
        assert "Switched to session" in captured.out
        assert agent.session_path is not None
        assert "topic-a" in str(agent.session_path)

    async def test_session_rename(self, dispatch):
        await dispatch("/session new old-name")
        _, _, _, captured = await dispatch("/session rename old-name new-name")
        assert "Renamed" in captured.out

    async def test_session_remove(self, dispatch):
        await dispatch("/session new removable")
        _, _, _, captured = await dispatch("/session remove removable")
        assert "Removed session" in captured.out
