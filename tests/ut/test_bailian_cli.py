# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.platforms.bailian.cli — _camel_to_kebab, CLIResult, invoke."""

import asyncio

import pytest

from dashscope_agenticx.platforms.bailian.cli import (
    BailianCLIClient,
    BailianCLIError,
    CLIResult,
    _camel_to_kebab,
)


class TestCamelToKebab:
    @pytest.mark.parametrize(
        "input,expected",
        [
            ("simple", "simple"),
            ("camelCase", "camel-case"),
            ("PascalCase", "pascal-case"),
            ("alreadyKebab", "already-kebab"),
            ("singleCharA", "single-char-a"),
            ("ABCDef", "abc-def"),
            ("APIKey", "api-key"),
            ("withNumber2", "with-number2"),
            ("", ""),
        ],
    )
    def test_conversion(self, input, expected):
        assert _camel_to_kebab(input) == expected


class TestCLIResult:
    def test_ok(self):
        r = CLIResult(code=0, stdout="output", stderr="")
        assert r.ok is True

    def test_not_ok(self):
        r = CLIResult(code=1, stdout="", stderr="error")
        assert r.ok is False

    def test_timeout_code(self):
        r = CLIResult(code=124, stdout="", stderr="timeout")
        assert r.ok is False


class TestInvoke:
    def test_bool_true_becomes_flag(self):
        client = BailianCLIClient(binary="echo")
        params = {"enableThinking": True, "verbose": False}
        captured_args = []

        async def fake_run(args, stdin="", inject_globals=True):
            captured_args.extend(args)
            return CLIResult(code=0, stdout="ok", stderr="")

        client.run = fake_run
        asyncio.run(client.invoke(["chat"], params))
        assert "--enable-thinking" in captured_args
        assert "--verbose" not in captured_args

    def test_string_value(self):
        client = BailianCLIClient(binary="echo")
        captured_args = []

        async def fake_run(args, stdin="", inject_globals=True):
            captured_args.extend(args)
            return CLIResult(code=0, stdout="ok", stderr="")

        client.run = fake_run
        asyncio.run(client.invoke(["chat"], {"model": "qwen-max"}))
        assert "--model" in captured_args
        idx = captured_args.index("--model")
        assert captured_args[idx + 1] == "qwen-max"

    def test_list_value_repeats_flag(self):
        client = BailianCLIClient(binary="echo")
        captured_args = []

        async def fake_run(args, stdin="", inject_globals=True):
            captured_args.extend(args)
            return CLIResult(code=0, stdout="ok", stderr="")

        client.run = fake_run
        asyncio.run(client.invoke(["upload"], {"files": ["a.txt", "b.txt"]}))
        assert captured_args.count("--files") == 2
        assert "a.txt" in captured_args
        assert "b.txt" in captured_args

    def test_none_omitted(self):
        client = BailianCLIClient(binary="echo")
        captured_args = []

        async def fake_run(args, stdin="", inject_globals=True):
            captured_args.extend(args)
            return CLIResult(code=0, stdout="ok", stderr="")

        client.run = fake_run
        asyncio.run(client.invoke(["chat"], {"model": None, "prompt": "hi"}))
        assert "--model" not in captured_args
        assert "--prompt" in captured_args

    def test_error_output(self):
        client = BailianCLIClient(binary="echo")

        async def fake_run(args, stdin="", inject_globals=True):
            return CLIResult(code=1, stdout="", stderr="command not found")

        client.run = fake_run
        result = asyncio.run(client.invoke(["chat"], {"model": "x"}))
        assert "bl exit=1" in result
        assert "command not found" in result

    def test_success_returns_stdout(self):
        client = BailianCLIClient(binary="echo")

        async def fake_run(args, stdin="", inject_globals=True):
            return CLIResult(code=0, stdout="success output", stderr="")

        client.run = fake_run
        result = asyncio.run(client.invoke(["chat"], {"model": "x"}))
        assert result == "success output"


class TestAvailable:
    def test_available_false_for_missing_binary(self):
        client = BailianCLIClient(binary="nonexistent_bl_12345")
        assert client.available() is False
