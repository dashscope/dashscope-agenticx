# -*- coding: utf-8 -*-
"""Tests for capabilities/local_memory.py — LocalMemoryCapability."""

from __future__ import annotations

import pytest

import dashscope_agenticx.config as config_module
from dashscope_agenticx.capabilities.local_memory import LocalMemoryCapability


@pytest.fixture(autouse=True)
def isolated(tmp_path, monkeypatch):
    # LocalMemoryClient resolves the store dir from dashscope_agenticx.config.WORKSPACE_DIR
    # at call time — redirect it into the test tmp dir.
    monkeypatch.setattr(config_module, "WORKSPACE_DIR", tmp_path)


@pytest.fixture()
def cap():
    return LocalMemoryCapability()


# ── is_available ──────────────────────────────────────────────────────────


def test_is_available_always_true(cap):
    assert cap.is_available() is True


# ── add ───────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_add_returns_id(cap):
    entry_id = await cap.add("remember this fact")
    assert isinstance(entry_id, str)
    assert len(entry_id) > 0


@pytest.mark.asyncio
async def test_add_persists(cap):
    await cap.add("fact one")
    items = await cap.list(limit=10)
    assert any("fact one" in m["content"] for m in items)


@pytest.mark.asyncio
async def test_add_with_metadata(cap):
    entry_id = await cap.add("tagged fact", metadata={"tag": "important"})
    items = await cap.list(limit=10)
    match = next((m for m in items if m["id"] == entry_id), None)
    assert match is not None
    assert match["metadata"]["tag"] == "important"


# ── search ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_search_empty_returns_empty(cap):
    results = await cap.search("anything")
    assert results == []


@pytest.mark.asyncio
async def test_search_keyword_match(cap):
    await cap.add("python is great for scripting")
    await cap.add("java is verbose but robust")
    results = await cap.search("python scripting")
    assert len(results) >= 1
    assert any("python" in r["content"] for r in results)


@pytest.mark.asyncio
async def test_search_empty_query_returns_empty(cap):
    await cap.add("some content")
    results = await cap.search("")
    assert results == []


@pytest.mark.asyncio
async def test_search_top_k_respected(cap):
    for i in range(10):
        await cap.add(f"shared keyword entry number {i}")
    results = await cap.search("shared keyword", top_k=3)
    assert len(results) <= 3


# ── delete ────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_delete_existing(cap):
    entry_id = await cap.add("to delete")
    result = await cap.delete(entry_id)
    assert result is True
    items = await cap.list(limit=10)
    assert not any(m["id"] == entry_id for m in items)


@pytest.mark.asyncio
async def test_delete_nonexistent(cap):
    result = await cap.delete("nonexistent-id")
    assert result is False


# ── list ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_list_respects_limit(cap):
    for i in range(10):
        await cap.add(f"entry {i}")
    items = await cap.list(limit=5)
    assert len(items) == 5


@pytest.mark.asyncio
async def test_list_returns_most_recent(cap):
    for i in range(5):
        await cap.add(f"item {i}")
    items = await cap.list(limit=3)
    contents = [m["content"] for m in items]
    assert "item 4" in contents


# ── initialize idempotency ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_initialize_is_idempotent(cap):
    await cap.initialize()
    await cap.add("fact")
    await cap.initialize()  # second call should not reload/reset
    items = await cap.list()
    assert len(items) == 1
