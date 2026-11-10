# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.tools.web_search — DuckDuckGo web search tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from dashscope_agenticx.tools.web_search import (
    _ABS_MAX_RESULTS,
    _DDGS_HINT,
    _DEFAULT_MAX_RESULTS,
    _MAX_QUERY_LEN,
    _format_results,
    _get_ddgs_class,
    _validate_max_results,
    _validate_query,
    _validate_region,
    web_search,
)

# ---------------------------------------------------------------------------
# _validate_query
# ---------------------------------------------------------------------------


class TestValidateQuery:
    def test_normal_query(self):
        assert _validate_query("Python asyncio") == "Python asyncio"

    def test_strips_whitespace(self):
        assert _validate_query("  hello  ") == "hello"

    def test_empty_query_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_query("")

    def test_whitespace_only_raises(self):
        with pytest.raises(ValueError, match="cannot be empty"):
            _validate_query("   ")

    def test_too_long_raises(self):
        long_query = "a" * (_MAX_QUERY_LEN + 1)
        with pytest.raises(ValueError, match="too long"):
            _validate_query(long_query)

    def test_max_length_ok(self):
        query = "a" * _MAX_QUERY_LEN
        assert _validate_query(query) == query


# ---------------------------------------------------------------------------
# _validate_max_results
# ---------------------------------------------------------------------------


class TestValidateMaxResults:
    def test_default_for_none(self):
        assert _validate_max_results(None) == _DEFAULT_MAX_RESULTS

    def test_default_for_invalid_string(self):
        assert _validate_max_results("abc") == _DEFAULT_MAX_RESULTS

    def test_normal_int(self):
        assert _validate_max_results(5) == 5

    def test_string_int(self):
        assert _validate_max_results("8") == 8

    def test_clamped_to_1(self):
        assert _validate_max_results(0) == 1
        assert _validate_max_results(-5) == 1

    def test_clamped_to_abs_max(self):
        assert _validate_max_results(999) == _ABS_MAX_RESULTS
        assert _validate_max_results(_ABS_MAX_RESULTS) == _ABS_MAX_RESULTS

    def test_boundary_values(self):
        assert _validate_max_results(1) == 1
        assert (
            _validate_max_results(_ABS_MAX_RESULTS - 1) == _ABS_MAX_RESULTS - 1
        )


# ---------------------------------------------------------------------------
# _validate_region
# ---------------------------------------------------------------------------


class TestValidateRegion:
    def test_default_for_empty(self):
        assert _validate_region("") == "wt-wt"

    def test_default_for_none(self):
        assert _validate_region(None) == "wt-wt"

    def test_default_for_whitespace(self):
        assert _validate_region("   ") == "wt-wt"

    def test_valid_regions(self):
        assert _validate_region("cn-zh") == "cn-zh"
        assert _validate_region("us-en") == "us-en"
        assert _validate_region("wt-wt") == "wt-wt"

    def test_strips_whitespace(self):
        assert _validate_region("  cn-zh  ") == "cn-zh"

    def test_invalid_too_short(self):
        with pytest.raises(ValueError, match="Invalid region code"):
            _validate_region("ab")

    def test_invalid_too_long(self):
        with pytest.raises(ValueError, match="Invalid region code"):
            _validate_region("a" * 20)

    def test_short_country_codes_mapped(self):
        assert _validate_region("cn") == "cn-zh"
        assert _validate_region("us") == "us-en"
        assert _validate_region("uk") == "uk-en"
        assert _validate_region("  CN  ") == "cn-zh"

    def test_invalid_missing_dash(self):
        with pytest.raises(ValueError, match="Invalid region code"):
            _validate_region("abcd")


# ---------------------------------------------------------------------------
# _format_results
# ---------------------------------------------------------------------------


class TestFormatResults:
    def test_empty_results(self):
        output = _format_results([], "test")
        data = json.loads(output)
        assert data["query"] == "test"
        assert data["results"] == []
        assert "No results found" in data["message"]

    def test_normal_results(self):
        results = [
            {"title": "Page 1", "href": "https://a.com", "body": "Snippet 1"},
            {"title": "Page 2", "href": "https://b.com", "body": "Snippet 2"},
        ]
        output = _format_results(results, "query")
        data = json.loads(output)
        assert data["count"] == 2
        assert len(data["results"]) == 2
        assert data["results"][0]["rank"] == 1
        assert data["results"][0]["title"] == "Page 1"
        assert data["results"][0]["url"] == "https://a.com"
        assert data["results"][0]["snippet"] == "Snippet 1"
        assert data["results"][1]["rank"] == 2

    def test_alternative_field_names(self):
        """Handles url/link and snippet/description field variants."""
        results = [
            {"title": "T", "url": "https://c.com", "snippet": "S"},
        ]
        data = json.loads(_format_results(results, "q"))
        assert data["results"][0]["url"] == "https://c.com"
        assert data["results"][0]["snippet"] == "S"

    def test_link_field_fallback(self):
        results = [{"title": "T", "link": "https://d.com", "description": "D"}]
        data = json.loads(_format_results(results, "q"))
        assert data["results"][0]["url"] == "https://d.com"
        assert data["results"][0]["snippet"] == "D"

    def test_missing_fields_default_empty(self):
        results = [{}]
        data = json.loads(_format_results(results, "q"))
        assert data["results"][0]["title"] == ""
        assert data["results"][0]["url"] == ""
        assert data["results"][0]["snippet"] == ""


# ---------------------------------------------------------------------------
# _get_ddgs_class
# ---------------------------------------------------------------------------


class TestGetDdgsClass:
    def test_import_success(self):
        """If duckduckgo_search is installed, DDGS class should be returned."""
        try:
            cls = _get_ddgs_class()
            assert cls is not None
        except ImportError:
            pytest.skip("duckduckgo-search not installed")

    def test_import_failure(self):
        with patch.dict("sys.modules", {"duckduckgo_search": None}):
            with pytest.raises(ImportError, match="duckduckgo-search"):
                _get_ddgs_class()


# ---------------------------------------------------------------------------
# web_search (async tool function)
# ---------------------------------------------------------------------------


class TestWebSearch:
    @pytest.mark.asyncio
    async def test_successful_search(self):
        fake_results = [
            {
                "title": "Result 1",
                "href": "https://example.com",
                "body": "Body 1",
            },
        ]
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = fake_results
        mock_ddgs_instance.__enter__ = MagicMock(
            return_value=mock_ddgs_instance,
        )
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)

        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "dashscope_agenticx.tools.web_search._get_ddgs_class",
            return_value=mock_ddgs_class,
        ):
            output = await web_search("test query", max_results=5)

        data = json.loads(output)
        assert data["query"] == "test query"
        assert data["count"] == 1
        assert data["results"][0]["title"] == "Result 1"

    @pytest.mark.asyncio
    async def test_empty_query_raises(self):
        """Empty query raises ValueError before reaching DDGS."""
        with pytest.raises(ValueError, match="cannot be empty"):
            await web_search("")

    @pytest.mark.asyncio
    async def test_search_api_error(self):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.side_effect = RuntimeError("network error")
        mock_ddgs_instance.__enter__ = MagicMock(
            return_value=mock_ddgs_instance,
        )
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)

        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "dashscope_agenticx.tools.web_search._get_ddgs_class",
            return_value=mock_ddgs_class,
        ):
            output = await web_search("test query")

        data = json.loads(output)
        assert "error" in data
        assert "network error" in data["error"]
        assert data["results"] == []

    @pytest.mark.asyncio
    async def test_search_import_error_propagates(self):
        """_get_ddgs_class() is called outside try/except, so ImportError propagates."""
        with patch(
            "dashscope_agenticx.tools.web_search._get_ddgs_class",
            side_effect=ImportError(_DDGS_HINT),
        ):
            with pytest.raises(ImportError, match="duckduckgo-search"):
                await web_search("test query")

    @pytest.mark.asyncio
    async def test_search_passes_region(self):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = []
        mock_ddgs_instance.__enter__ = MagicMock(
            return_value=mock_ddgs_instance,
        )
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)

        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "dashscope_agenticx.tools.web_search._get_ddgs_class",
            return_value=mock_ddgs_class,
        ):
            await web_search("query", max_results=3, region="cn-zh")

        mock_ddgs_instance.text.assert_called_once_with(
            "query",
            region="cn-zh",
            max_results=3,
        )

    @pytest.mark.asyncio
    async def test_max_results_clamped(self):
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = []
        mock_ddgs_instance.__enter__ = MagicMock(
            return_value=mock_ddgs_instance,
        )
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)

        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "dashscope_agenticx.tools.web_search._get_ddgs_class",
            return_value=mock_ddgs_class,
        ):
            await web_search("q", max_results=999)

        _, kwargs = mock_ddgs_instance.text.call_args
        assert kwargs["max_results"] == _ABS_MAX_RESULTS

    @pytest.mark.asyncio
    async def test_search_no_results(self):
        """DDGS returns empty list — should produce formatted 'no results' output."""
        mock_ddgs_instance = MagicMock()
        mock_ddgs_instance.text.return_value = []
        mock_ddgs_instance.__enter__ = MagicMock(
            return_value=mock_ddgs_instance,
        )
        mock_ddgs_instance.__exit__ = MagicMock(return_value=False)

        mock_ddgs_class = MagicMock(return_value=mock_ddgs_instance)

        with patch(
            "dashscope_agenticx.tools.web_search._get_ddgs_class",
            return_value=mock_ddgs_class,
        ):
            output = await web_search("obscure query xyz")

        data = json.loads(output)
        assert data["query"] == "obscure query xyz"
        assert data["results"] == []
        assert "No results found" in data["message"]
