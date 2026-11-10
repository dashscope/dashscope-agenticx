# -*- coding: utf-8 -*-
"""Tests for history export features: export_history, _generate_html, _esc,
plus the existing stats() and list_history() helpers.

All tests mock ``WORKSPACE_DIR`` so the real workspace memory file is never
touched.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from dashscope_agenticx.platforms.local import history

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
def workspace(tmp_path, monkeypatch):
    """Redirect the history module's WORKSPACE_DIR to an isolated tmp dir."""
    fake_workspace = tmp_path / "ws"
    fake_workspace.mkdir()
    monkeypatch.setattr(history, "WORKSPACE_DIR", fake_workspace)
    return fake_workspace


def _seed(entries: list[dict]) -> None:
    """Write raw entries straight to the (mocked) history store."""
    history._save(entries)


SAMPLE_ENTRIES = [
    {
        "id": "abc12345",
        "summary": "帮我写一个 Python 脚本",
        "created_at": "2024-01-01T10:00:00",
        "turns": 3,
    },
    {
        "id": "def67890",
        "summary": "检查 git 状态 <danger> & stuff",
        "created_at": "2024-01-02T11:30:00",
        "turns": 5,
    },
]


# ---------------------------------------------------------------------------
# _esc
# ---------------------------------------------------------------------------


class TestEsc:
    def test_escapes_ampersand(self):
        assert history._esc("a&b") == "a&amp;b"

    def test_escapes_less_than(self):
        assert history._esc("a<b") == "a&lt;b"

    def test_escapes_greater_than(self):
        assert history._esc("a>b") == "a&gt;b"

    def test_escapes_double_quote(self):
        assert history._esc('a"b') == "a&quot;b"

    def test_escapes_all_together(self):
        assert (
            history._esc('<a href="x">&')
            == "&lt;a href=&quot;x&quot;&gt;&amp;"
        )

    def test_no_special_chars_unchanged(self):
        assert history._esc("plain text 123") == "plain text 123"

    def test_empty_string(self):
        assert history._esc("") == ""

    def test_ampersand_escaped_first(self):
        """& must be escaped before introducing new & entities."""
        # If & were not escaped first, the & in &lt; would be double-escaped.
        assert history._esc("<") == "&lt;"
        assert history._esc("&lt;") == "&amp;lt;"


# ---------------------------------------------------------------------------
# _generate_html
# ---------------------------------------------------------------------------


class TestGenerateHtml:
    def test_empty_entries_has_structure(self):
        html = history._generate_html([])
        assert "<html" in html
        assert "<table>" in html
        assert "<tr>" in html
        # Header row is always present
        assert "<th>Summary</th>" in html
        # Zero counts reported
        assert "0 records" in html
        assert "0 turns" in html

    def test_multiple_entries_render_rows(self):
        html = history._generate_html(SAMPLE_ENTRIES)
        assert "<html" in html
        assert "<table>" in html
        # One header row + two data rows
        assert html.count("<tr>") == 3
        # Summaries present (escaped)
        assert "帮我写一个 Python 脚本" in html
        # Special chars escaped in output
        assert "&lt;danger&gt;" in html
        assert "&amp; stuff" in html
        # Stats reflect both entries
        assert "2 records" in html
        assert "8 turns" in html  # 3 + 5

    def test_doctype_present(self):
        assert history._generate_html([]).startswith("<!DOCTYPE html>")

    def test_entries_with_missing_fields_use_defaults(self):
        html = history._generate_html([{"id": "x"}])
        # Missing summary -> default placeholder
        assert "(no summary)" in html
        # Missing turns -> 0
        assert "1 records" in html


# ---------------------------------------------------------------------------
# export_history — explicit fmt
# ---------------------------------------------------------------------------


class TestExportHistoryFormats:
    def test_json_format_valid_structure(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "hist.json"
        result = history.export_history(str(out), fmt="json")

        data = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 2
        assert data[0]["summary"] == "帮我写一个 Python 脚本"
        assert data[0]["turns"] == 3
        assert data[1]["id"] == "def67890"

    def test_markdown_format_headers_and_bullets(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "hist.md"
        history.export_history(str(out), fmt="markdown")

        text = out.read_text(encoding="utf-8")
        # Top-level header
        assert text.startswith("# Conversation History")
        # Per-entry headers
        assert "## 帮我写一个 Python 脚本" in text
        assert "## 检查 git 状态" in text
        # Bullet points for metadata
        assert "- **Time**:" in text
        assert "- **Turns**:" in text
        assert "3" in text  # turns for first entry

    def test_html_format_tags(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "hist.html"
        history.export_history(str(out), fmt="html")

        text = out.read_text(encoding="utf-8")
        assert "<html" in text
        assert "<table>" in text
        assert "<tr>" in text
        assert "帮我写一个 Python 脚本" in text

    def test_html_is_default_format(self, workspace, tmp_path):
        """When fmt is None and extension is unrecognized, html is used."""
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "hist.dat"
        history.export_history(str(out))  # fmt defaults to None -> html

        text = out.read_text(encoding="utf-8")
        assert "<html" in text
        assert "<table>" in text

    def test_empty_history_exports_valid_empty(self, workspace, tmp_path):
        out = tmp_path / "empty.json"
        history.export_history(str(out), fmt="json")
        assert json.loads(out.read_text(encoding="utf-8")) == []


# ---------------------------------------------------------------------------
# export_history — auto-detect from extension
# ---------------------------------------------------------------------------


class TestExportHistoryAutoDetect:
    def test_json_extension_auto_detected(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "auto.json"
        history.export_history(str(out))  # no fmt

        data = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(data, list)
        assert len(data) == 2

    def test_md_extension_auto_detected(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "auto.md"
        history.export_history(str(out))

        text = out.read_text(encoding="utf-8")
        assert text.startswith("# Conversation History")
        assert "## " in text

    def test_html_extension_auto_detected(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "auto.html"
        history.export_history(str(out))

        text = out.read_text(encoding="utf-8")
        assert "<html" in text
        assert "<table>" in text

    def test_explicit_fmt_overrides_extension(self, workspace, tmp_path):
        """Explicit fmt wins over the extension-derived format."""
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "conflict.json"
        # Extension says json, but explicit fmt says html -> html wins.
        history.export_history(str(out), fmt="html")

        text = out.read_text(encoding="utf-8")
        assert "<html" in text
        # Not valid JSON
        with pytest.raises(json.JSONDecodeError):
            json.loads(text)

    def test_uppercase_extension_auto_detected(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "upper.JSON"
        history.export_history(str(out))

        data = json.loads(out.read_text(encoding="utf-8"))
        assert isinstance(data, list)


# ---------------------------------------------------------------------------
# export_history — return value
# ---------------------------------------------------------------------------


class TestExportHistoryReturnPath:
    def test_returns_resolved_path_string(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "ret.json"
        result = history.export_history(str(out), fmt="json")

        assert isinstance(result, str)
        assert result == str(out)
        assert Path(result).exists()

    def test_returns_path_for_each_format(self, workspace, tmp_path):
        _seed(SAMPLE_ENTRIES)
        for name, fmt in [
            ("a.json", "json"),
            ("b.md", "markdown"),
            ("c.html", "html"),
        ]:
            result = history.export_history(str(tmp_path / name), fmt=fmt)
            assert isinstance(result, str)
            assert Path(result).exists()


# ---------------------------------------------------------------------------
# stats() and list_history()
# ---------------------------------------------------------------------------


class TestStatsAndList:
    def test_stats_empty(self, workspace):
        stats = history.stats()
        assert stats == {"count": 0, "total_turns": 0}

    def test_stats_with_entries(self, workspace):
        _seed(SAMPLE_ENTRIES)
        stats = history.stats()
        assert stats == {"count": 2, "total_turns": 8}

    def test_list_history_empty(self, workspace):
        assert history.list_history() == []

    def test_list_history_returns_entries(self, workspace):
        _seed(SAMPLE_ENTRIES)
        entries = history.list_history()
        assert len(entries) == 2
        assert entries[0]["id"] == "abc12345"
        assert entries[1]["id"] == "def67890"

    def test_list_history_respects_limit(self, workspace):
        _seed(SAMPLE_ENTRIES)
        entries = history.list_history(limit=1)
        assert len(entries) == 1
        # list_history returns the tail (most recent)
        assert entries[0]["id"] == "def67890"

    def test_list_history_limit_larger_than_count(self, workspace):
        _seed(SAMPLE_ENTRIES)
        entries = history.list_history(limit=100)
        assert len(entries) == 2

    def test_export_reflects_current_stats(self, workspace, tmp_path):
        """Round-trip: seed, export json, re-import and confirm counts match."""
        _seed(SAMPLE_ENTRIES)
        out = tmp_path / "rt.json"
        history.export_history(str(out), fmt="json")
        data = json.loads(out.read_text(encoding="utf-8"))
        stats = history.stats()
        assert len(data) == stats["count"]
        assert sum(e["turns"] for e in data) == stats["total_turns"]
