# -*- coding: utf-8 -*-
"""Tests for tools/filesystem.py — path validation, read/write/delete/search."""

from __future__ import annotations

import os

import pytest

from dashscope_agenticx.tools.filesystem import (
    _READ_DEFAULT_LINES,
    _READ_MAX_CHARS,
    create_directory,
    delete_directory,
    delete_file,
    list_directory,
    move_file,
    read_file,
    search_files,
    write_file,
)
from dashscope_agenticx.utils import paths
from dashscope_agenticx.utils.paths import (
    SENSITIVE_NAMES,
    validate_path,
    validate_write_path,
)


@pytest.fixture
def no_temp_root(monkeypatch):
    """Restore the strict cwd-only write rule for one test.

    pytest builds ``tmp_path`` under the real system temp dir, so a fixture
    directory used as "somewhere outside cwd" is now a legitimate write target.
    These tests are about out-of-cwd writes and symlink escape, not about the
    temp allowance, so the allowance is switched off rather than the fixtures
    moved somewhere unportable.
    """
    monkeypatch.setattr(paths, "_temp_write_roots", lambda: ())


@pytest.fixture(autouse=True)
def _no_perception_ctx():
    """register_vision_tools stores a process-global perception context
    for read_file's auto-perceive hook; without a reset, a ctx leaked by
    the vision tests would make read_file run the perception pipeline
    here instead of returning the text redirect."""
    from dashscope_agenticx.tools import vision

    vision._PERCEPTION_CTX = None
    vision._reset_auto_perceive()
    yield
    vision._PERCEPTION_CTX = None
    vision._reset_auto_perceive()


@pytest.fixture
def temp_root(tmp_path, monkeypatch):
    """A controlled stand-in for the system temp dir."""
    root = tmp_path / "system-temp"
    root.mkdir()
    monkeypatch.setattr(paths, "_temp_write_roots", lambda: (str(root),))
    return root


# ── validate_path ─────────────────────────────────────────────────────────


def test_validate_path_normal(tmp_path):
    p = str(tmp_path / "file.txt")
    result = validate_path(p)
    assert result == p


def test_validate_path_rejects_ssh(tmp_path):
    with pytest.raises(ValueError, match="sensitive"):
        validate_path(str(tmp_path / ".ssh" / "id_rsa"))


def test_validate_path_rejects_aws(tmp_path):
    with pytest.raises(ValueError, match="sensitive"):
        validate_path(str(tmp_path / ".aws" / "credentials"))


def test_validate_path_rejects_env(tmp_path):
    with pytest.raises(ValueError, match="sensitive"):
        validate_path(str(tmp_path / ".env"))


def test_validate_path_expands_tilde(monkeypatch, tmp_path):
    monkeypatch.setenv("HOME", str(tmp_path))
    result = validate_path("~/somefile.txt")
    assert not result.startswith("~")


# ── validate_write_path ───────────────────────────────────────────────────


def test_validate_write_path_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = str(tmp_path / "out.txt")
    result = validate_write_path(p)
    assert result == p


def test_validate_write_path_outside_cwd_rejected(
    tmp_path,
    monkeypatch,
    tmp_path_factory,
    no_temp_root,
):
    monkeypatch.chdir(tmp_path)
    outside = str(tmp_path_factory.mktemp("other") / "file.txt")
    with pytest.raises(ValueError, match="path traversal"):
        validate_write_path(outside)


def test_validate_write_path_sensitive_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="sensitive"):
        validate_write_path(str(tmp_path / ".ssh" / "config"))


def test_validate_path_symlink_to_sensitive_blocked(tmp_path):
    # Symlink inside the workspace pointing at a sensitive dir must be
    # resolved before the blocklist check (symlink escape防护).
    ssh_dir = tmp_path / "home" / ".ssh"
    ssh_dir.mkdir(parents=True)
    (ssh_dir / "id_rsa").write_text("secret")
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    link = workspace / "innocent"
    link.symlink_to(ssh_dir)
    with pytest.raises(ValueError, match="sensitive"):
        validate_path(str(link / "id_rsa"))


def test_validate_write_path_symlink_escape_blocked(
    tmp_path,
    monkeypatch,
    no_temp_root,
):
    # Symlink inside cwd pointing outside must not escape the cwd check.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(workspace)
    link = workspace / "link"
    link.symlink_to(outside)
    with pytest.raises(ValueError, match="path traversal"):
        validate_write_path(str(link / "evil.txt"))


def test_validate_write_path_relative_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = validate_write_path("sub/file.txt")
    assert result == "sub/file.txt"


def test_validate_write_path_symlink_within_cwd_ok(tmp_path, monkeypatch):
    # A symlink that stays inside cwd is still allowed.
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    real_dir = workspace / "real"
    real_dir.mkdir()
    monkeypatch.chdir(workspace)
    link = workspace / "link"
    link.symlink_to(real_dir)
    result = validate_write_path(str(link / "out.txt"))
    assert result == str(link / "out.txt")


# ── the system temp dir as a writable root ────────────────────────────────


def test_write_file_allows_the_system_temp_dir(temp_root):
    result = write_file(str(temp_root / "scratch.py"), "print(1)\n")
    assert result.startswith("Created file:")
    assert (temp_root / "scratch.py").read_text() == "print(1)\n"


def test_temp_allowance_is_scoped_to_the_root(
    temp_root,
    tmp_path,
    monkeypatch,
):
    """Widening to temp must not widen to "anywhere but cwd"."""
    monkeypatch.chdir(temp_root)
    sibling = tmp_path / "not-temp"
    sibling.mkdir()
    with pytest.raises(ValueError, match="path traversal"):
        validate_write_path(str(sibling / "file.txt"))


def test_symlink_inside_temp_cannot_escape_it(temp_root, tmp_path):
    """The temp root is realpath'd too, not just the cwd side."""
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    link = temp_root / "link"
    link.symlink_to(elsewhere)
    with pytest.raises(ValueError, match="path traversal"):
        validate_write_path(str(link / "evil.txt"))


def test_sensitive_names_are_still_blocked_in_temp(temp_root):
    with pytest.raises(ValueError, match="sensitive"):
        validate_write_path(str(temp_root / ".ssh" / "config"))


def test_every_write_guarded_tool_reaches_temp(
    temp_root,
    tmp_path,
    monkeypatch,
):
    monkeypatch.chdir(temp_root)
    assert create_directory(str(temp_root / "d" / "e")).startswith("Created")
    source = tmp_path / "src.txt"
    source.write_text("payload")
    assert move_file(str(source), str(temp_root / "moved.txt")).startswith(
        "Moved",
    )
    assert (temp_root / "moved.txt").read_text() == "payload"


def test_refusal_names_both_writable_roots(
    tmp_path,
    monkeypatch,
    no_temp_root,
):
    """The message is the model's only chance to self-correct in one step."""
    cwd = tmp_path / "cwd"
    cwd.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.chdir(cwd)
    with pytest.raises(ValueError) as exc:
        validate_write_path(str(outside / "f.txt"))
    message = str(exc.value)
    assert "working directory" in message
    assert "temp dir" in message


def test_real_temp_roots_are_absolute_existing_dirs():
    import tempfile

    roots = paths._temp_write_roots()
    assert os.path.realpath(tempfile.gettempdir()) in roots
    assert all(os.path.isabs(r) and os.path.isdir(r) for r in roots)
    # The guard compares string prefixes, so a duplicate or a trailing
    # separator would silently change what is inside a root.
    assert len(roots) == len(set(roots))
    assert all(not r.endswith(os.sep) for r in roots)


def test_write_file_into_the_real_temp_dir(tmp_path, monkeypatch):
    """The reported regression, end to end and with no stubbed roots.

    A model asked for a throwaway script writes /tmp/scratch.py first. Refusing
    it cost a tool call and then put the scratch file in the user's repo, which
    is the worse outcome -- the model had to offer to clean up after itself.
    """
    import shutil
    import tempfile

    scratch = tempfile.mkdtemp(prefix="agenticx-write-guard-")
    target = os.path.join(scratch, "qwen_plus_stream_usage.py")
    original = paths._temp_write_roots
    try:
        monkeypatch.chdir(tmp_path)
        # A/B on the same target: without the refusal half this would also pass
        # on a host whose tmp_path happened to contain the scratch dir.
        monkeypatch.setattr(paths, "_temp_write_roots", lambda: ())
        with pytest.raises(ValueError, match="path traversal"):
            validate_write_path(target)
        monkeypatch.setattr(paths, "_temp_write_roots", original)
        assert write_file(target, "print(1)\n").startswith("Created file:")
        assert open(target, encoding="utf-8").read() == "print(1)\n"
    finally:
        shutil.rmtree(scratch, ignore_errors=True)


# ── read_file ──────────────────────────────────────────────────────────────


def test_read_file_basic(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_text("line1\nline2\nline3\n", encoding="utf-8")
    result = read_file(str(f))
    assert "line1" in result
    assert "line3" in result


def test_read_file_image_redirects_to_view_image(tmp_path):
    # solve-sudoku (2026-10-13__fix-071e): the agent read the 23KB jpeg
    # with read_file, got binary noise, and never perceived it. Binary
    # images now redirect to view_image instead of dumping noise.
    f = tmp_path / "grid.jpeg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    result = read_file(str(f))
    assert "view_image" in result
    assert "binary noise" in result
    assert "\x00" not in result


def test_read_file_image_auto_perceives(tmp_path, monkeypatch):
    # 071i: the redirect was read at t=136s and ignored within 3s —
    # advice is ignorable, delivered perception is not. With a
    # perception context available, read_file returns the vision read
    # itself instead of the pointer.
    from dashscope_agenticx.tools import vision

    f = tmp_path / "grid.jpeg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    monkeypatch.setattr(
        vision,
        "auto_perceive",
        lambda path: "[auto-perception] a 9 by 9 digit grid",
    )
    result = read_file(str(f))
    assert "9 by 9 digit grid" in result
    assert "binary noise" not in result


def test_read_file_image_redirects_when_perception_unavailable(
    tmp_path,
    monkeypatch,
):
    # auto_perceive returning None (disabled, unconfigured, out of
    # budget, or failed) must fall back to the redirect, not to noise.
    from dashscope_agenticx.tools import vision

    f = tmp_path / "grid.jpeg"
    f.write_bytes(b"\xff\xd8\xff\xe0" + b"\x00" * 64)
    monkeypatch.setattr(vision, "auto_perceive", lambda path: None)
    result = read_file(str(f))
    assert "view_image" in result
    assert "\x00" not in result


def test_read_file_svg_stays_readable(tmp_path):
    # SVG is XML markup, not binary — reading it as text is legitimate.
    f = tmp_path / "icon.svg"
    f.write_text('<svg xmlns="http://www.w3.org/2000/svg"><rect/></svg>')
    result = read_file(str(f))
    assert "<rect/>" in result


def _write_rows(tmp_path, count, name="data.txt"):
    f = tmp_path / name
    f.write_text(
        "".join(f"ROW{i:03d}\n" for i in range(count)),
        encoding="utf-8",
    )
    return str(f)


def test_read_file_offset_limit(tmp_path):
    p = _write_rows(tmp_path, 10)
    # offset is 1-based: line 2 is ROW001, so lines 2-4 are ROW001..ROW003.
    result = read_file(p, offset=2, limit=3)
    assert "ROW001" in result
    assert "ROW003" in result
    assert "ROW000" not in result
    assert "ROW004" not in result


def test_read_file_offset_matches_printed_line_number(tmp_path):
    p = _write_rows(tmp_path, 10)
    result = read_file(p, offset=4, limit=1)
    # The number the model sees and the content on that line agree, so an
    # offset copied out of a previous result lands on the intended line.
    assert result.startswith("   4\tROW003")


@pytest.mark.parametrize("offset", [None, 0, 1])
def test_read_file_offset_at_or_below_one_starts_at_top(tmp_path, offset):
    p = _write_rows(tmp_path, 5)
    assert read_file(p, offset=offset).startswith("   1\tROW000")


def test_read_file_small_file_has_no_trailer(tmp_path):
    result = read_file(_write_rows(tmp_path, 3))
    assert "omitted" not in result
    assert "ROW002" in result


def test_read_file_default_window_applies_without_limit(tmp_path):
    total = _READ_DEFAULT_LINES + 300
    result = read_file(_write_rows(tmp_path, total))
    shown = [ln for ln in result.splitlines() if "\t" in ln]
    assert len(shown) == _READ_DEFAULT_LINES
    assert f"ROW{total - 1:03d}" not in result
    assert f"[omitted 300 of {total} lines" in result


def test_read_file_trailer_offset_continues_without_gap(tmp_path):
    total = _READ_DEFAULT_LINES + 50
    p = _write_rows(tmp_path, total)
    first = read_file(p)
    trailer = first.splitlines()[-1]
    next_offset = int(trailer.split("offset=")[1].split(" ")[0])

    second = read_file(p, offset=next_offset)
    assert second.startswith(f"{next_offset:>4}\t")
    # No overlap with the first window and no skipped row between them.
    first_rows = {ln.split("\t")[1] for ln in first.splitlines() if "\t" in ln}
    second_rows = {
        ln.split("\t")[1] for ln in second.splitlines() if "\t" in ln
    }
    assert not (first_rows & second_rows)
    assert first_rows | second_rows == {f"ROW{i:03d}" for i in range(total)}


def test_read_file_char_ceiling_drops_partial_line(tmp_path):
    p = tmp_path / "wide.txt"
    wide = "X" * (_READ_MAX_CHARS // 2)
    p.write_text(f"{wide}\n{wide}\n{wide}\n", encoding="utf-8")
    result = read_file(str(p))
    # Backs off to the last complete line instead of emitting a half line
    # whose number would mislead a follow-up offset.
    assert "\n   2\t" not in result
    assert "[omitted 2 of 3 lines" in result


def test_read_file_offset_past_end_of_file(tmp_path):
    result = read_file(_write_rows(tmp_path, 5), offset=99)
    assert "past the end" in result
    assert "(5 lines)" in result


def test_read_file_not_found():
    result = read_file("/nonexistent/file.txt")
    assert "Error" in result


def test_read_file_sensitive_path_blocked():
    result = read_file(os.path.expanduser("~/.ssh/id_rsa"))
    assert "Error" in result


# ── write_file ─────────────────────────────────────────────────────────────


def test_write_file_creates_new(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = str(tmp_path / "new.txt")
    result = write_file(p, "hello world")
    assert "Created" in result
    assert open(p).read() == "hello world"


def test_write_file_overwrite_shows_diff(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = str(tmp_path / "existing.txt")
    open(p, "w").write("old content\n")
    result = write_file(p, "new content\n")
    assert "diff" in result or "已写入" in result


def test_write_file_unchanged_content(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = str(tmp_path / "same.txt")
    open(p, "w").write("data")
    result = write_file(p, "data")
    assert "unchanged" in result


def test_write_file_too_large_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    p = str(tmp_path / "big.txt")
    big = "x" * (51 * 1024 * 1024)
    result = write_file(p, big)
    assert "Error" in result and "too large" in result


def test_write_file_outside_cwd_rejected(
    tmp_path,
    monkeypatch,
    tmp_path_factory,
    no_temp_root,
):
    monkeypatch.chdir(tmp_path)
    outside = str(tmp_path_factory.mktemp("other") / "hack.txt")
    result = write_file(outside, "evil")
    assert "Error" in result


# ── list_directory ─────────────────────────────────────────────────────────


def test_list_directory_basic(tmp_path):
    (tmp_path / "a.txt").write_text("a")
    (tmp_path / "sub").mkdir()
    result = list_directory(str(tmp_path))
    assert "a.txt" in result
    assert "sub" in result


def test_list_directory_not_found():
    result = list_directory("/no/such/dir")
    assert "Error" in result


# ── create_directory ───────────────────────────────────────────────────────


def test_create_directory_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    new_dir = str(tmp_path / "newdir")
    result = create_directory(new_dir)
    assert "Created" in result
    assert os.path.isdir(new_dir)


def test_create_directory_outside_cwd_rejected(
    tmp_path,
    monkeypatch,
    tmp_path_factory,
    no_temp_root,
):
    monkeypatch.chdir(tmp_path)
    outside = str(tmp_path_factory.mktemp("other") / "evil")
    result = create_directory(outside)
    assert "Error" in result


# ── delete_file ────────────────────────────────────────────────────────────


def test_delete_file_success(tmp_path):
    f = tmp_path / "del.txt"
    f.write_text("bye")
    result = delete_file(str(f))
    assert "Deleted" in result
    assert not f.exists()


def test_delete_file_not_found(tmp_path):
    result = delete_file(str(tmp_path / "ghost.txt"))
    assert "Error" in result


def test_delete_file_sensitive_blocked():
    result = delete_file(os.path.expanduser("~/.ssh/id_rsa"))
    assert "Error" in result


# ── delete_directory ───────────────────────────────────────────────────────


def test_delete_directory_success(tmp_path):
    d = tmp_path / "todelete"
    d.mkdir()
    result = delete_directory(str(d))
    assert "Deleted" in result
    assert not d.exists()


def test_delete_directory_cannot_delete_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = delete_directory(str(tmp_path))
    assert "Error" in result


def test_delete_directory_not_found(tmp_path):
    result = delete_directory(str(tmp_path / "nope"))
    assert "Error" in result


# ── search_files ───────────────────────────────────────────────────────────


def test_search_files_matches(tmp_path):
    (tmp_path / "foo.py").write_text("")
    (tmp_path / "bar.py").write_text("")
    (tmp_path / "readme.md").write_text("")
    result = search_files("*.py", str(tmp_path))
    assert "foo.py" in result
    assert "bar.py" in result
    assert "readme.md" not in result


def test_search_files_no_match(tmp_path):
    result = search_files("*.xyz", str(tmp_path))
    assert "No files match" in result


def test_search_files_skips_sensitive_filenames(tmp_path):
    # Create a file with a sensitive name pattern inside a normal directory
    sensitive = tmp_path / "passwd"
    sensitive.write_text("root:x:0:0")
    result = search_files("passwd", str(tmp_path))
    # The file path itself should not appear in the results
    assert str(sensitive) not in result


def test_search_files_matches_a_path_pattern_in_a_dot_directory(tmp_path):
    """`.agenticx/skills/*.md` reported no matches for a directory full of them.

    Two bugs compounded: the pattern was matched against the basename only, so
    anything containing a separator could never match, and every dot-directory
    was pruned from the walk. The reply was indistinguishable from "not
    installed", which sent the model off to re-download a working config.
    """
    skills = tmp_path / ".agenticx" / "skills"
    skills.mkdir(parents=True)
    (skills / "text-generation.md").write_text("")
    (skills / "cli.md").write_text("")
    (tmp_path / "top.md").write_text("")

    result = search_files(".agenticx/skills/*.md", str(tmp_path))

    assert "text-generation.md" in result
    assert "cli.md" in result
    assert "top.md" not in result


def test_search_files_skips_dot_directories_unless_asked(tmp_path):
    hidden = tmp_path / ".agenticx"
    hidden.mkdir()
    (hidden / "notes.md").write_text("")
    (tmp_path / "top.md").write_text("")

    result = search_files("*.md", str(tmp_path))

    assert "top.md" in result
    assert "notes.md" not in result


def test_search_files_never_enters_sensitive_directories(tmp_path):
    secret = tmp_path / ".aws"
    secret.mkdir()
    (secret / "config.json").write_text("")

    result = search_files(".aws/*.json", str(tmp_path))

    assert "No files match" in result
    assert "config.json" not in result


def test_search_files_still_finds_a_nested_file_by_bare_name(tmp_path):
    nested = tmp_path / "src" / "pkg"
    nested.mkdir(parents=True)
    (nested / "foo.py").write_text("")

    result = search_files("foo.py", str(tmp_path))

    assert str(nested / "foo.py") in result


def test_search_files_no_match_explains_the_rules(tmp_path):
    result = search_files("*.xyz", str(tmp_path))

    assert "No files match" in result
    assert str(tmp_path) in result
    assert "dot-directories" in result


# ── move_file ──────────────────────────────────────────────────────────────


def test_move_file_success(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    src = tmp_path / "src.txt"
    src.write_text("move me")
    dst = str(tmp_path / "dst.txt")
    result = move_file(str(src), dst)
    assert "Moved" in result
    assert not src.exists()
    assert os.path.exists(dst)


def test_move_file_src_not_found(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = move_file(str(tmp_path / "ghost.txt"), str(tmp_path / "dst.txt"))
    assert "Error" in result
