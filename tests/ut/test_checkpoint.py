# -*- coding: utf-8 -*-
"""Tests for tools/checkpoint.py — snapshot/undo of file mutations."""

from __future__ import annotations

import json

import pytest

from dashscope_agenticx.tools import checkpoint
from dashscope_agenticx.tools.filesystem import (
    delete_directory,
    delete_file,
    write_file,
)


@pytest.fixture(autouse=True)
def _workspace(tmp_path, monkeypatch):
    """Point WORKSPACE_DIR at an isolated temp dir for every test."""
    monkeypatch.setattr(
        "dashscope_agenticx.config.WORKSPACE_DIR",
        tmp_path / ".agenticx",
    )


def _cp_dir(tmp_path):
    return tmp_path / ".agenticx" / "checkpoints"


def _index_entries(tmp_path) -> list[dict]:
    index = _cp_dir(tmp_path) / "index.jsonl"
    if not index.is_file():
        return []
    return [
        json.loads(line)
        for line in index.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


# ── core snapshot/undo ────────────────────────────────────────────────────


def test_snapshot_then_undo_restores_previous_content(tmp_path):
    target = tmp_path / "doc.txt"
    target.write_text("v1", encoding="utf-8")
    checkpoint.snapshot(str(target), "overwrite")
    target.write_text("v2", encoding="utf-8")

    result = checkpoint.undo()

    assert "restored" in result
    assert str(target) in result
    assert target.read_text(encoding="utf-8") == "v1"


def test_undo_of_created_file_removes_it(tmp_path):
    target = tmp_path / "new.txt"
    checkpoint.snapshot(str(target), "create")
    target.write_text("made by tool", encoding="utf-8")

    result = checkpoint.undo()

    assert "removed created" in result
    assert not target.exists()


def test_undo_of_delete_restores_file(tmp_path):
    target = tmp_path / "victim.txt"
    target.write_text("keep me", encoding="utf-8")
    checkpoint.snapshot(str(target), "delete")
    target.unlink()

    result = checkpoint.undo()

    assert "restored" in result
    assert target.read_text(encoding="utf-8") == "keep me"


def test_undo_with_empty_index(tmp_path):
    assert checkpoint.undo() == "Nothing to undo"


def test_undo_pops_only_most_recent_entry(tmp_path):
    target = tmp_path / "seq.txt"
    target.write_text("v1", encoding="utf-8")
    checkpoint.snapshot(str(target), "overwrite")
    target.write_text("v2", encoding="utf-8")
    checkpoint.snapshot(str(target), "overwrite")
    target.write_text("v3", encoding="utf-8")

    checkpoint.undo()  # v3 -> v2
    assert target.read_text(encoding="utf-8") == "v2"
    checkpoint.undo()  # v2 -> v1
    assert target.read_text(encoding="utf-8") == "v1"
    assert checkpoint.undo() == "Nothing to undo"


def test_index_cap_prunes_oldest(tmp_path):
    target = tmp_path / "capped.txt"
    target.write_text("seed", encoding="utf-8")
    for _ in range(55):
        checkpoint.snapshot(str(target), "overwrite")

    entries = _index_entries(tmp_path)
    assert len(entries) == 50
    backups = {e["backup"] for e in entries if e["backup"]}
    assert len(backups) == 50
    # Orphaned backups belonging to pruned entries were deleted.
    remaining = {p.name for p in _cp_dir(tmp_path).glob("*.bak")}
    assert remaining == backups


def test_undo_missing_backup_reports_error(tmp_path):
    target = tmp_path / "gone.txt"
    target.write_text("x", encoding="utf-8")
    checkpoint.snapshot(str(target), "overwrite")
    for bak in _cp_dir(tmp_path).glob("*.bak"):
        bak.unlink()  # sabotage the store behind the index's back

    result = checkpoint.undo()

    assert result.startswith("Error")


def test_snapshot_never_raises(monkeypatch, tmp_path):
    def boom():
        raise OSError("disk unavailable")

    monkeypatch.setattr(checkpoint, "_checkpoint_dir", boom)
    # Must swallow the failure instead of propagating to the write tool.
    checkpoint.snapshot(str(tmp_path / "f.txt"), "overwrite")


def test_handle_undo_command_prints_result(capsys):
    checkpoint.handle_undo_command()
    assert "Nothing to undo" in capsys.readouterr().out


# ── write/delete tool hooks ───────────────────────────────────────────────


def test_write_file_overwrite_hook_undo_restores(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "hooked.txt"
    target.write_text("old\n", encoding="utf-8")
    write_file(str(target), "new\n")

    result = checkpoint.undo()

    assert "restored" in result
    assert target.read_text(encoding="utf-8") == "old\n"


def test_write_file_create_hook_undo_removes(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "brand-new.txt"
    write_file(str(target), "hello")

    result = checkpoint.undo()

    assert "removed created" in result
    assert not target.exists()


def test_delete_file_hook_undo_restores(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "doomed.txt"
    target.write_text("data", encoding="utf-8")
    delete_file(str(target))
    assert not target.exists()

    result = checkpoint.undo()

    assert "restored" in result
    assert target.read_text(encoding="utf-8") == "data"


# ── directory snapshots ───────────────────────────────────────────────────


def test_delete_directory_hook_undo_restores_tree(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "doomed-dir"
    (target / "sub").mkdir(parents=True)
    (target / "sub" / "f.txt").write_text("data", encoding="utf-8")
    delete_directory(str(target))
    assert not target.exists()

    result = checkpoint.undo()

    assert "restored" in result
    assert "delete_directory" in result
    assert (target / "sub" / "f.txt").read_text(encoding="utf-8") == "data"


def test_oversized_directory_records_no_backup(tmp_path, monkeypatch):
    monkeypatch.setattr(checkpoint, "_MAX_DIR_BACKUP_BYTES", 10)
    target = tmp_path / "big"
    target.mkdir()
    (target / "f.bin").write_bytes(b"x" * 64)

    checkpoint.snapshot(str(target), "delete_dir")

    (entry,) = _index_entries(tmp_path)
    # The entry still records the mutation; only the payload is missing.
    assert entry["backup"] is None
    assert entry["note"] == "directory too large to back up"
    # Undo must report the gap, not claim a restore.
    assert checkpoint.undo().startswith("Error")


def test_index_cap_prunes_directory_backups(tmp_path):
    for i in range(51):
        d = tmp_path / f"d{i}"
        d.mkdir()
        (d / "f.txt").write_text("x", encoding="utf-8")
        checkpoint.snapshot(str(d), "delete_dir")

    entries = _index_entries(tmp_path)
    assert len(entries) == 50
    kept = {e["backup"] for e in entries if e["backup"]}
    remaining = {p.name for p in _cp_dir(tmp_path).glob("*.bakdir")}
    # The evicted entry's tree backup was removed with it.
    assert remaining == kept
    assert len(remaining) == 50
