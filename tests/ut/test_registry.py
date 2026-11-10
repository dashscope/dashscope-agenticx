# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.tools.registry — ToolRegistry, tool decorator, parameter schema."""

import pytest

from dashscope_agenticx.tools.registry import (
    PermissionLevel,
    Tool,
    ToolDefinition,
    ToolRegistry,
    _build_parameters_schema,
    registry,
)


class TestPermissionLevel:
    def test_values(self):
        assert PermissionLevel.AUTO.value == "auto"
        assert PermissionLevel.CONFIRM.value == "confirm"
        assert PermissionLevel.DANGEROUS.value == "dangerous"


class TestToolDefinition:
    def test_defaults(self):
        td = ToolDefinition(name="t", description="d")
        assert td.permission == PermissionLevel.AUTO
        assert td.func is None
        assert td.parameters == {}

    def test_alias(self):
        assert Tool is ToolDefinition


class TestBuildParametersSchema:
    def test_basic_types(self):
        def func(a: str, b: int, c: bool = False):
            pass

        schema = _build_parameters_schema(func)
        assert schema["type"] == "object"
        assert schema["properties"]["a"]["type"] == "string"
        assert schema["properties"]["b"]["type"] == "integer"
        assert schema["properties"]["c"]["type"] == "boolean"
        assert schema["required"] == ["a", "b"]

    def test_optional_param(self):
        from typing import Optional

        def func(a: str, b: Optional[int] = None):
            pass

        schema = _build_parameters_schema(func)
        assert "b" in schema["properties"]
        assert "b" not in schema.get("required", [])
        assert schema["required"] == ["a"]

    def test_no_args(self):
        def func():
            pass

        schema = _build_parameters_schema(func)
        assert schema["properties"] == {}

    def test_self_skipped(self):
        def func(self, a: str):
            pass

        schema = _build_parameters_schema(func)
        assert "self" not in schema["properties"]
        assert schema["required"] == ["a"]

    def test_float_and_list(self):
        def func(a: float, b: list, c: dict):
            pass

        schema = _build_parameters_schema(func)
        assert schema["properties"]["a"]["type"] == "number"
        assert schema["properties"]["b"]["type"] == "array"
        assert schema["properties"]["c"]["type"] == "object"


class TestToolRegistry:
    def test_register_and_get(self):
        reg = ToolRegistry()
        td = ToolDefinition(name="my_tool", description="test")
        reg.register(td)
        assert reg.get("my_tool") is td

    def test_get_missing(self):
        reg = ToolRegistry()
        assert reg.get("nope") is None

    def test_register_overwrites(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(name="t", description="v1"))
        reg.register(ToolDefinition(name="t", description="v2"))
        assert reg.get("t").description == "v2"

    def test_list_tools(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(name="a", description=""))
        reg.register(ToolDefinition(name="b", description=""))
        names = {t.name for t in reg.list_tools()}
        assert names == {"a", "b"}

    def test_unregister(self):
        reg = ToolRegistry()
        reg.register(ToolDefinition(name="t", description=""))
        reg.unregister("t")
        assert reg.get("t") is None

    def test_unregister_missing(self):
        reg = ToolRegistry()
        reg.unregister("nope")

    def test_register_mcp_tool(self):
        reg = ToolRegistry()
        reg.register_mcp_tool(
            name="mcp_test",
            description="an mcp tool",
            parameters={"type": "object"},
            call_fn=lambda **kw: "ok",
        )
        td = reg.get("mcp_test")
        assert td is not None
        assert td.description == "an mcp tool"
        assert td.func is not None
        assert td.parameters == {"type": "object"}

    def test_register_decorator_pattern(self):
        reg = ToolRegistry()
        td = ToolDefinition(name="dec_tool", description="decorated")
        decorator = reg.register(td)

        @decorator
        def my_func(a: str):
            return a

        assert reg.get("dec_tool").func is my_func
        assert my_func._tool_definition is td

    def test_to_schema_list_includes_core(self):
        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="read_file",
                description="read",
                parameters={"type": "object"},
            ),
        )
        schemas = reg.to_schema_list()
        assert any(s["name"] == "read_file" for s in schemas)

    def test_to_schema_list_excludes_non_core_without_keyword(self):
        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="special_tool",
                description="special",
                parameters={},
            ),
        )
        schemas = reg.to_schema_list()
        assert not any(s["name"] == "special_tool" for s in schemas)

    def test_to_schema_list_includes_mcp_prefix(self):
        reg = ToolRegistry()
        reg.register(
            ToolDefinition(
                name="mcp_custom",
                description="custom mcp",
                parameters={},
            ),
        )
        schemas = reg.to_schema_list()
        assert any(s["name"] == "mcp_custom" for s in schemas)

    def _reg_with_bailian(self):
        reg = ToolRegistry()
        for name in (
            "bailian_text_chat",
            "bailian_image_generate",
            "bailian_usage_free",
        ):
            reg.register(
                ToolDefinition(name=name, description="", parameters={}),
            )
        return reg

    def test_bailian_excluded_without_keyword(self):
        reg = self._reg_with_bailian()
        names = {s["name"] for s in reg.to_schema_list("帮我改一下这个文件")}
        assert not any(n.startswith("bailian_") for n in names)

    def test_bailian_included_on_capability_keyword(self):
        reg = self._reg_with_bailian()
        names = {s["name"] for s in reg.to_schema_list("帮我画一张图片")}
        assert "bailian_image_generate" in names
        assert "bailian_text_chat" not in names

    def test_bailian_catchall_unlocks_all(self):
        reg = self._reg_with_bailian()
        names = {s["name"] for s in reg.to_schema_list("查一下百炼用量")}
        assert {
            "bailian_text_chat",
            "bailian_image_generate",
            "bailian_usage_free",
        } <= names

    def _reg_with_evolve(self):
        reg = ToolRegistry()
        reg.register(
            ToolDefinition(name="evolve_skill", description="", parameters={}),
        )
        return reg

    def test_evolve_skill_hidden_by_default(self, monkeypatch):
        monkeypatch.delenv("AGENTICX_EVOLVE_SKILL", raising=False)
        reg = self._reg_with_evolve()
        names = {s["name"] for s in reg.to_schema_list()}
        assert "evolve_skill" not in names

    def test_evolve_skill_visible_with_env_gate(self, monkeypatch):
        monkeypatch.setenv("AGENTICX_EVOLVE_SKILL", "1")
        reg = self._reg_with_evolve()
        names = {s["name"] for s in reg.to_schema_list()}
        assert "evolve_skill" in names


class TestGlobalRegistry:
    def test_global_registry_exists(self):
        from dashscope_agenticx.tools.registry import registry as global_reg

        assert isinstance(global_reg, ToolRegistry)

    def test_tool_decorator_registers(self):
        # Save and restore state
        original = registry.get("test_dec_fn")
        try:
            from dashscope_agenticx.tools.registry import tool

            @tool("test_dec_fn", "a test", PermissionLevel.AUTO)
            def my_fn(a: str):
                return a

            td = registry.get("test_dec_fn")
            assert td is not None
            assert td.name == "test_dec_fn"
            assert td.func is my_fn
            assert td.parameters["properties"]["a"]["type"] == "string"
        finally:
            registry.unregister("test_dec_fn")
            if original:
                registry.register(original)
