# -*- coding: utf-8 -*-
from pathlib import Path

import pytest

from dashscope_agenticx.utils.toml import (
    load_toml,
    loads_toml,
    parse_toml_inline_table,
    parse_value,
    parse_value_raw,
)


class TestLoadToml:
    def test_load_valid_file(self, tmp_path: Path):
        path = tmp_path / "test.toml"
        path.write_text('key = "value"\nnum = 42\n', encoding="utf-8")
        result = load_toml(path)
        assert result == {"key": "value", "num": 42}

    def test_missing_file(self, tmp_path: Path):
        assert load_toml(tmp_path / "missing.toml") is None

    def test_invalid_file(self, tmp_path: Path):
        path = tmp_path / "bad.toml"
        path.write_text("[not closed", encoding="utf-8")
        assert load_toml(path) is None


class TestLoadsToml:
    def test_valid(self):
        assert loads_toml('name = "agenticx"') == {"name": "agenticx"}

    def test_invalid(self):
        assert loads_toml("[[[") is None


class TestParseValue:
    def test_quoted(self):
        assert parse_value('key = "value"') == "value"

    def test_single_quoted(self):
        assert parse_value("key = 'value'") == "value"

    def test_unquoted(self):
        assert parse_value("key = 42") == "42"


class TestParseValueRaw:
    def test_keeps_quotes(self):
        assert parse_value_raw('key = "value"') == '"value"'

    def test_keeps_structure(self):
        assert parse_value_raw("key = [1, 2]") == "[1, 2]"


class TestParseTomlInlineTable:
    def test_empty(self):
        assert parse_toml_inline_table("{}") == {}

    def test_string_int_bool_float(self):
        raw = '{name = "agenticx", count = 3, active = true, ratio = 1.5}'
        result = parse_toml_inline_table(raw)
        assert result == {
            "name": "agenticx",
            "count": 3,
            "active": True,
            "ratio": 1.5,
        }

    def test_false(self):
        result = parse_toml_inline_table("{flag = false}")
        assert result == {"flag": False}

    def test_malformed(self):
        assert parse_toml_inline_table("not a table") is None

    def test_whitespace_and_empty_pairs(self):
        raw = '{ a = "1" , , b = "2" , }'
        result = parse_toml_inline_table(raw)
        assert result == {"a": 1, "b": 2}
