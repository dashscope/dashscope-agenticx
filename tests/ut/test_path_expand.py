# -*- coding: utf-8 -*-
import os

import pytest

from dashscope_agenticx.cli import (
    _expand_at_references,
    _to_multimodal_content,
)


class TestExpandAtReferences:
    """Test @file and @dir expansion with temp files."""

    def test_single_text_file(self, tmp_path):
        f = tmp_path / "test.txt"
        f.write_text("hello world")
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            expanded, images, audio = _expand_at_references("@test.txt")
            assert "hello world" in expanded
            assert "test.txt" in expanded  # filename in fence header
            assert len(images) == 0
            assert len(audio) == 0
        finally:
            os.chdir(old_cwd)

    def test_nonexistent_path_left_literal(self):
        expanded, images, audio = _expand_at_references(
            "@nonexistent_file_xyz.txt",
        )
        assert "@nonexistent_file_xyz.txt" in expanded
        assert len(images) == 0
        assert len(audio) == 0

    def test_multiple_refs(self, tmp_path):
        (tmp_path / "a.txt").write_text("content_a")
        (tmp_path / "b.txt").write_text("content_b")
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            expanded, images, audio = _expand_at_references(
                "@a.txt and @b.txt",
            )
            assert "content_a" in expanded
            assert "content_b" in expanded
            assert len(audio) == 0
        finally:
            os.chdir(old_cwd)

    def test_directory_expansion(self, tmp_path):
        sub = tmp_path / "mydir"
        sub.mkdir()
        (sub / "file1.py").write_text("print(1)")
        (sub / "file2.py").write_text("print(2)")
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            expanded, images, audio = _expand_at_references("@mydir/")
            assert "file1.py" in expanded
            assert "file2.py" in expanded
            assert len(audio) == 0
        finally:
            os.chdir(old_cwd)

    def test_no_at_refs(self):
        expanded, images, audio = _expand_at_references("just plain text")
        assert expanded == "just plain text"
        assert len(images) == 0
        assert len(audio) == 0

    def test_mixed_text_and_refs(self, tmp_path):
        (tmp_path / "config.yaml").write_text("key: value")
        old_cwd = os.getcwd()
        os.chdir(tmp_path)
        try:
            expanded, images, audio = _expand_at_references(
                "请读取 @config.yaml 的内容",
            )
            assert "请读取" in expanded
            assert "key: value" in expanded
            assert len(audio) == 0
        finally:
            os.chdir(old_cwd)


class TestToMultimodalContent:
    """Test conversion of text+images to multimodal format."""

    def test_no_images_returns_string(self):
        result = _to_multimodal_content("plain text", [])
        assert result == "plain text"
        assert isinstance(result, str)

    def test_with_images_returns_blocks(self):
        text = "before <<__AGENTICX_IMG_0__>> after"
        images = ["data:image/png;base64,abc123"]
        result = _to_multimodal_content(text, images)
        assert isinstance(result, list)
        assert len(result) == 3
        assert result[0]["type"] == "text"
        assert "before" in result[0]["text"]
        assert result[1]["type"] == "image_url"
        assert result[1]["image_url"]["url"] == "data:image/png;base64,abc123"
        assert result[2]["type"] == "text"
        assert "after" in result[2]["text"]

    def test_multiple_images(self):
        text = "<<__AGENTICX_IMG_0__>> middle <<__AGENTICX_IMG_1__>>"
        images = ["data:image/png;base64,img0", "data:image/jpeg;base64,img1"]
        result = _to_multimodal_content(text, images)
        assert isinstance(result, list)
        assert result[0]["type"] == "image_url"
        assert result[0]["image_url"]["url"] == "data:image/png;base64,img0"
        assert result[1]["type"] == "text"
        assert "middle" in result[1]["text"]
        assert result[2]["type"] == "image_url"
        assert result[2]["image_url"]["url"] == "data:image/jpeg;base64,img1"

    def test_empty_text_with_image(self):
        text = "<<__AGENTICX_IMG_0__>>"
        images = ["data:image/png;base64,abc"]
        result = _to_multimodal_content(text, images)
        assert isinstance(result, list)
        assert len(result) == 1
        assert result[0]["type"] == "image_url"

    def test_invalid_image_index_fallback(self):
        text = "<<__AGENTICX_IMG_99__>>"
        images = ["data:image/png;base64,only_one"]
        result = _to_multimodal_content(text, images)
        # No valid blocks remain, so it falls back to the original text
        assert result == text
