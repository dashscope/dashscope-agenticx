# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.platforms.local.memory — LocalMemoryClient CRUD."""

import asyncio

import pytest

from dashscope_agenticx.platforms.base import MemoryNode
from dashscope_agenticx.platforms.local.memory import LocalMemoryClient


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setattr("dashscope_agenticx.config.WORKSPACE_DIR", tmp_path)
    return LocalMemoryClient(user_id="test_user")


class TestAdd:
    def test_add_custom_content(self, client):
        nodes = asyncio.run(client.add([], custom_content="user likes python"))
        assert len(nodes) == 1
        assert nodes[0].content == "user likes python"

    def test_add_extracts_from_messages(self, client):
        messages = [
            {"role": "user", "content": "我喜欢用 Python 写代码"},
            {"role": "assistant", "content": "好的"},
        ]
        nodes = asyncio.run(client.add(messages))
        assert len(nodes) >= 1
        assert any(
            "Python" in n.content or "python" in n.content for n in nodes
        )

    def test_add_deduplicates(self, client):
        messages = [{"role": "user", "content": "我喜欢用 Python 写代码"}]
        asyncio.run(client.add(messages))
        asyncio.run(client.add(messages))
        nodes = asyncio.run(client.list(page_size=100))
        contents = [n.content for n in nodes]
        assert len(contents) == 1

    def test_add_ignores_short_lines(self, client):
        messages = [{"role": "user", "content": "我"}]
        nodes = asyncio.run(client.add(messages))
        assert len(nodes) == 0


class TestList:
    def test_list_empty(self, client):
        nodes = asyncio.run(client.list())
        assert nodes == []

    def test_list_pagination(self, client):
        for i in range(5):
            asyncio.run(client.add([], custom_content=f"item {i}"))
        page1 = asyncio.run(client.list(page_num=1, page_size=2))
        page2 = asyncio.run(client.list(page_num=2, page_size=2))
        assert len(page1) == 2
        assert len(page2) == 2
        ids_p1 = {n.id for n in page1}
        ids_p2 = {n.id for n in page2}
        assert ids_p1.isdisjoint(ids_p2)


class TestSearch:
    def test_search_keyword(self, client):
        asyncio.run(client.add([], custom_content="user prefers Python"))
        asyncio.run(client.add([], custom_content="user likes coffee"))
        results = asyncio.run(client.search("Python", top_k=5))
        assert len(results) == 1
        assert "Python" in results[0].content
        assert results[0].score > 0

    def test_search_no_match(self, client):
        asyncio.run(client.add([], custom_content="hello world"))
        results = asyncio.run(client.search("nonexistent"))
        assert results == []

    def test_search_chinese(self, client):
        asyncio.run(client.add([], custom_content="用户喜欢编程"))
        results = asyncio.run(client.search("编程"))
        assert len(results) >= 1
        assert "编程" in results[0].content

    def test_multi_char_keyword_outranks_single_char(self, client):
        # Query "部署" (2 chars): a node containing the full word must beat
        # a node containing only one of the characters.
        asyncio.run(client.add([], custom_content="部置好了"))
        asyncio.run(client.add([], custom_content="负责部署服务"))
        results = asyncio.run(client.search("部署", min_score=0.1))
        assert results[0].content == "负责部署服务"
        assert results[0].score > results[-1].score

    def test_tie_favors_newest(self, client):
        asyncio.run(client.add([], custom_content="我喜欢 Python 早期记录"))
        asyncio.run(client.add([], custom_content="我喜欢 Python 最新记录"))
        results = asyncio.run(client.search("Python"))
        assert results[0].content == "我喜欢 Python 最新记录"


class TestDelete:
    def test_delete_existing(self, client):
        nodes = asyncio.run(client.add([], custom_content="to delete"))
        node_id = nodes[0].id
        result = asyncio.run(client.delete(node_id))
        assert result is True
        remaining = asyncio.run(client.list(page_size=100))
        assert all(n.id != node_id for n in remaining)

    def test_delete_missing(self, client):
        result = asyncio.run(client.delete("nonexistent_id"))
        assert result is False


class TestUpdate:
    def test_update_existing(self, client):
        nodes = asyncio.run(client.add([], custom_content="original"))
        node_id = nodes[0].id
        result = asyncio.run(client.update(node_id, "updated content"))
        assert result is True
        all_nodes = asyncio.run(client.list(page_size=100))
        updated = next(n for n in all_nodes if n.id == node_id)
        assert updated.content == "updated content"

    def test_update_missing(self, client):
        result = asyncio.run(client.update("nonexistent", "x"))
        assert result is False


class TestUserIdValidation:
    @pytest.mark.parametrize(
        "bad_id",
        [
            "../../x",
            "../x",
            "..",
            "a/b",
            "a\\b",
            "/etc/passwd",
            "",
            "a b",
            "a;b",
        ],
    )
    def test_rejects_unsafe_user_ids(self, tmp_path, monkeypatch, bad_id):
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path,
        )
        with pytest.raises(ValueError):
            LocalMemoryClient(user_id=bad_id)
        # Nothing may escape the memory directory.
        assert not (tmp_path / "x.json").exists()

    @pytest.mark.parametrize(
        "good_id",
        ["default", "user_1", "user-1", "user.1", "agenticcli-1234abcd"],
    )
    def test_accepts_safe_user_ids(self, tmp_path, monkeypatch, good_id):
        monkeypatch.setattr(
            "dashscope_agenticx.config.WORKSPACE_DIR",
            tmp_path,
        )
        client = LocalMemoryClient(user_id=good_id)
        assert client._file.parent == tmp_path / "memory"
        assert client._file.name == f"{good_id}.json"


def test_stable_memory_user_id_keeps_legacy_identity_contract(monkeypatch):
    import hashlib

    from dashscope_agenticx.utils import ids

    monkeypatch.setattr(ids.platform, "node", lambda: "migration-host")
    monkeypatch.setattr(ids.os, "getuid", lambda: 1234, raising=False)
    expected_digest = hashlib.sha256(
        b"migration-host:1234:agenticcli-memory",
    ).hexdigest()

    assert ids.stable_memory_user_id() == f"agenticcli-{expected_digest[:8]}"
    assert ids.stable_memory_user_id(length=12) == (
        f"agenticcli-{expected_digest[:12]}"
    )


# ---------------------------------------------------------------------------
# C3: memory _save must be atomic — temp file in the same directory, then
# os.replace; a crash mid-write must not truncate the existing file.
# ---------------------------------------------------------------------------


class TestAtomicSave:
    def test_save_uses_tmp_then_replace(self, client, monkeypatch):
        import os
        from pathlib import Path

        calls = []
        real_replace = os.replace

        def spy_replace(src, dst):
            calls.append((src, dst))
            return real_replace(src, dst)

        monkeypatch.setattr(os, "replace", spy_replace)

        client._save([{"id": "x", "content": "测试"}])

        assert len(calls) == 1
        src, dst = calls[0]
        assert Path(dst) == client._file
        assert Path(src).parent == client._file.parent
        assert str(src).endswith(".tmp")
        assert client._load()[0]["id"] == "x"
        assert not (client._file.parent / f"{client._file.name}.tmp").exists()

    def test_crash_during_replace_preserves_existing_file(
        self,
        client,
        monkeypatch,
    ):
        import json
        import os

        client._file.write_text(
            json.dumps([{"id": "old", "content": "旧数据"}]),
            encoding="utf-8",
        )

        def boom(src, dst):
            raise OSError("模拟崩溃")

        monkeypatch.setattr(os, "replace", boom)

        with pytest.raises(OSError):
            client._save([{"id": "new", "content": "新数据"}])

        assert client._load()[0]["id"] == "old"
        assert not (client._file.parent / f"{client._file.name}.tmp").exists()
