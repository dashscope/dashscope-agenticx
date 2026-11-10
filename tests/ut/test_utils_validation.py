# -*- coding: utf-8 -*-
from __future__ import annotations

import sys
from typing import Optional, Union

import pytest

from dashscope_agenticx.tools.registry import PermissionLevel, ToolDefinition
from dashscope_agenticx.utils.validation import (
    coerce_types,
    missing_required_args,
    normalize_tool_arguments,
    parse_string_annotations,
)


def plain_fn(a: int, b: str, c: float, d: bool):
    return a, b, c, d


def optional_fn(x: int | None = None, y: Optional[str] = None):
    return x, y


def string_annotated_fn(a: int | None, b: float):
    return a, b


class TestParseStringAnnotations:
    def test_int_and_float(self):
        hints = parse_string_annotations(string_annotated_fn)
        assert hints == {"a": int, "b": float}

    def test_no_annotations(self):
        def no_ann(x):
            return x

        assert parse_string_annotations(no_ann) == {}

    def test_regular_type_annotations(self):
        assert parse_string_annotations(plain_fn) == {
            "a": int,
            "b": str,
            "c": float,
            "d": bool,
        }


class TestCoerceTypes:
    def test_int_from_string(self):
        assert coerce_types(plain_fn, {"a": "42"}) == {"a": 42}

    def test_int_from_decimal_string(self):
        assert coerce_types(plain_fn, {"a": "42.0"}) == {"a": 42}

    def test_float_from_string(self):
        assert coerce_types(plain_fn, {"c": "3.14"}) == {"c": 3.14}

    @pytest.mark.parametrize(
        "value,expected",
        [
            ("true", True),
            ("True", True),
            ("1", True),
            ("yes", True),
            ("false", False),
            ("no", False),
        ],
    )
    def test_bool_from_string(self, value, expected):
        assert coerce_types(plain_fn, {"d": value}) == {"d": expected}

    def test_non_string_passthrough(self):
        assert coerce_types(plain_fn, {"a": 5, "b": [1]}) == {"a": 5, "b": [1]}

    def test_unexpected_arg_passthrough(self):
        assert coerce_types(plain_fn, {"z": "keep"}) == {"z": "keep"}

    def test_optional_int(self):
        assert coerce_types(optional_fn, {"x": "7"}) == {"x": 7}

    def test_invalid_int_kept_as_string(self):
        assert coerce_types(plain_fn, {"a": "not a number"}) == {
            "a": "not a number",
        }


class TestMissingRequiredArgs:
    def test_missing(self):
        tool = ToolDefinition(
            name="t",
            description="t",
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        assert missing_required_args(tool, {}) == ["x"]

    def test_none_value(self):
        tool = ToolDefinition(
            name="t",
            description="t",
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        assert missing_required_args(tool, {"x": None}) == ["x"]

    def test_empty_string(self):
        tool = ToolDefinition(
            name="t",
            description="t",
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        assert missing_required_args(tool, {"x": "   "}) == ["x"]

    def test_present(self):
        tool = ToolDefinition(
            name="t",
            description="t",
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {"x": {"type": "string"}},
                "required": ["x"],
            },
        )
        assert missing_required_args(tool, {"x": "value"}) == []

    def test_non_string_param_empty(self):
        tool = ToolDefinition(
            name="t",
            description="t",
            permission=PermissionLevel.AUTO,
            parameters={
                "type": "object",
                "properties": {"n": {"type": "integer"}},
                "required": ["n"],
            },
        )
        assert missing_required_args(tool, {"n": 0}) == []

    def test_no_parameters(self):
        tool = ToolDefinition(
            name="t",
            description="t",
            permission=PermissionLevel.AUTO,
        )
        assert missing_required_args(tool, {}) == []


class TestNormalizeToolArguments:
    @staticmethod
    def _tool(properties=None):
        return ToolDefinition(
            name="run_command",
            description="run",
            permission=PermissionLevel.CONFIRM,
            parameters={
                "type": "object",
                "properties": properties
                or {
                    "command": {"type": "string"},
                    "timeout": {"type": "integer"},
                },
                "required": ["command"],
            },
        )

    @pytest.mark.parametrize(
        "inner",
        [{"command": "pwd"}, '{"command": "pwd", "timeout": 10}'],
    )
    def test_unwraps_only_redundant_valid_object(self, inner):
        result = normalize_tool_arguments(self._tool(), {"arguments": inner})
        assert result["command"] == "pwd"
        assert "arguments" not in result

    @pytest.mark.parametrize(
        "arguments",
        [
            {},
            {"command": "pwd"},
            {"arguments": '{"command": "pwd"'},
            {"arguments": "[]"},
            {"arguments": {"command": ""}},
            {"arguments": {"command": None}},
            {"arguments": {"command": 1}},
            {"arguments": {"command": "pwd", "timeout": True}},
            {"arguments": {"command": "pwd", "unknown": "value"}},
            {"arguments": {"arguments": {"command": "pwd"}}},
            {"arguments": {"command": "pwd"}, "other": "value"},
        ],
    )
    def test_does_not_guess_malformed_or_ambiguous_arguments(self, arguments):
        assert normalize_tool_arguments(self._tool(), arguments) is arguments

    def test_preserves_schema_with_real_arguments_parameter(self):
        tool = self._tool(
            {"command": {"type": "string"}, "arguments": {"type": "object"}},
        )
        arguments = {"arguments": {"command": "pwd"}}
        assert normalize_tool_arguments(tool, arguments) is arguments

    def test_optional_only_schema_is_not_unwrapped(self):
        tool = self._tool()
        tool.parameters["required"] = []
        arguments = {"arguments": {"command": "pwd"}}
        assert normalize_tool_arguments(tool, arguments) is arguments
