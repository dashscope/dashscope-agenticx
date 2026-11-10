# -*- coding: utf-8 -*-
import pytest

from dashscope_agenticx.utils.text import (
    has_omitted_marker,
    mask_secret,
    strip_frontmatter,
    truncate,
    truncate_head_tail,
    truncate_text,
    truncate_value,
)


class TestMaskSecret:
    @pytest.mark.parametrize(
        "value,expected",
        [
            ("", ""),
            ("a", "a..."),
            ("ab", "a...b"),
            ("short", "s...t"),
            ("verylongsecret", "very...cret"),
        ],
    )
    def test_mask_secret(self, value, expected):
        assert mask_secret(value) == expected


class TestTruncate:
    def test_no_truncation(self):
        assert truncate("hello", 10) == "hello"

    def test_truncation(self):
        assert truncate("hello world", 8) == "hello wo..."

    def test_exact_length(self):
        assert truncate("hello", 5) == "hello"


class TestTruncateText:
    def test_short_text(self):
        assert truncate_text("hi", 10) == "hi"

    def test_long_text(self):
        text = "a" * 100
        result = truncate_text(text, 40)
        assert result.startswith("a" * 24)
        assert "[truncated 60 chars]" in result
        assert result.endswith("a" * 16)


class TestTruncateHeadTail:
    def test_short_text(self):
        assert truncate_head_tail("hi", 10) == "hi"

    def test_long_text_default_ratio(self):
        text = "b" * 100
        result = truncate_head_tail(text, 40)
        assert result.startswith("b" * 24)
        assert "[omitted 60 chars]" in result
        assert result.endswith("b" * 16)

    def test_custom_ratio(self):
        text = "c" * 100
        result = truncate_head_tail(text, 40, ratio=0.8)
        assert result.startswith("c" * 32)
        assert result.endswith("c" * 8)

    def test_note_goes_inside_the_marker(self):
        result = truncate_head_tail("d" * 100, 40, note=" — re-run read_file")
        assert "[omitted 60 chars — re-run read_file]" in result
        assert result.startswith("d" * 24)
        assert result.endswith("d" * 16)


class TestHasOmittedMarker:
    def test_plain_marker(self):
        assert has_omitted_marker(truncate_head_tail("e" * 100, 40))

    def test_marker_with_note(self):
        text = truncate_head_tail("f" * 100, 40, note=' — re-run {"p": 1}')
        assert has_omitted_marker(text)

    def test_read_file_window_trailer_is_not_a_marker(self):
        trailer = "[omitted 3000 of 5000 lines — read_file path=a offset=1]"
        assert not has_omitted_marker(trailer)

    def test_plain_text(self):
        assert not has_omitted_marker("no marker here")


class TestStripFrontmatter:
    def test_yaml_frontmatter(self):
        text = "---\nfoo: bar\n---\ncontent"
        assert strip_frontmatter(text) == "content"

    def test_no_frontmatter(self):
        text = "plain content"
        assert strip_frontmatter(text) == "plain content"

    def test_only_one_divider(self):
        text = "---\ncontent"
        assert strip_frontmatter(text) == "---\ncontent"


class TestTruncateValue:
    def test_short_value(self):
        assert truncate_value("short") == "short"

    def test_too_many_lines(self):
        value = "\n".join(f"line {i}" for i in range(25))
        result = truncate_value(value)
        assert "(omitted, 25 lines)" in result
        assert result.count("\n") <= 12

    def test_too_many_chars(self):
        value = "x" * 900
        result = truncate_value(value)
        assert "(omitted, 900 chars)" in result
