# -*- coding: utf-8 -*-
import base64
from pathlib import Path

import pytest

from dashscope_agenticx.cli import (
    _expand_at_references,
    _to_multimodal_content,
)
from dashscope_agenticx.config import (
    AUDIO_MODELS,
    VISION_MODELS,
    is_audio_model,
    is_vision_model,
)
from dashscope_agenticx.skills.package import SkillPackage
from dashscope_agenticx.tools.registry import ToolRegistry
from dashscope_agenticx.utils import normalize_for_model


@pytest.fixture(autouse=True)
def _seed_modality_sets():
    """VISION_MODELS/AUDIO_MODELS are populated at runtime by
    apply_extensions() from the toml; seed them here for unit tests."""
    VISION_MODELS.clear()
    VISION_MODELS.update(
        {
            "qwen-vl-max",
            "qwen-vl-plus",
            "qwen-omni-turbo",
            "claude-sonnet-4",
            "gpt-4o",
            "gpt-4-turbo",
            "glm-4v",
        },
    )
    AUDIO_MODELS.clear()
    AUDIO_MODELS.update({"qwen-omni-turbo"})
    yield
    VISION_MODELS.clear()
    AUDIO_MODELS.clear()


class TestModelCapabilityChecks:
    def test_audio_model_recognized(self):
        assert is_audio_model("qwen-omni-turbo") is True
        assert is_audio_model("qwen-omni-turbo-latest") is True

    def test_non_audio_model_rejected(self):
        assert is_audio_model("qwen-plus") is False
        assert is_audio_model("gpt-4o") is False

    def test_vision_model_still_recognized(self):
        assert is_vision_model("qwen-vl-max") is True
        assert is_vision_model("qwen-omni-turbo") is True


class TestAudioExpansion:
    def test_expand_audio_file(self, tmp_path: Path):
        audio_path = tmp_path / "test.mp3"
        audio_bytes = b"\xff\xfb" + b"fake audio data" * 20
        audio_path.write_bytes(audio_bytes)

        text, images, audio_clips = _expand_at_references(
            f"请分析 @{audio_path}",
        )

        assert images == []
        assert len(audio_clips) == 1
        assert audio_clips[0]["mime"] == "audio/mp3"
        assert audio_clips[0]["format"] == "mp3"
        assert audio_clips[0]["data"] == base64.b64encode(audio_bytes).decode()
        assert "<<__AGENTICX_AUDIO_0__>>" in text

    def test_expand_wav_file(self, tmp_path: Path):
        audio_path = tmp_path / "test.wav"
        audio_path.write_bytes(b"RIFFfake wav data")

        text, images, audio_clips = _expand_at_references(f"@{audio_path}")

        assert len(audio_clips) == 1
        assert audio_clips[0]["mime"] == "audio/wav"
        assert audio_clips[0]["format"] == "wav"

    def test_oversize_audio_is_dropped(self, tmp_path: Path):
        from dashscope_agenticx.cli import _AT_AUDIO_MAX_BYTES

        audio_path = tmp_path / "big.mp3"
        audio_path.write_bytes(b"x" * (_AT_AUDIO_MAX_BYTES + 1))

        text, images, audio_clips = _expand_at_references(f"@{audio_path}")

        assert audio_clips == []
        assert "audio too large" in text


class TestImageExpansion:
    def test_expand_png_file(self, tmp_path: Path):
        img = tmp_path / "pic.png"
        img.write_bytes(b"\x89PNG\r\n\x1a\n" + b"fake body")

        text, images, audio_clips = _expand_at_references(f"@{img}")

        assert audio_clips == []
        assert len(images) == 1
        assert images[0].startswith("data:image/png;base64,")
        assert "<<__AGENTICX_IMG_0__>>" in text

    def test_oversize_image_is_dropped(self, tmp_path: Path):
        from dashscope_agenticx.cli import _AT_IMAGE_MAX_BYTES

        img = tmp_path / "big.png"
        img.write_bytes(b"x" * (_AT_IMAGE_MAX_BYTES + 1))

        text, images, audio_clips = _expand_at_references(f"@{img}")

        assert images == []
        assert "Image too large" in text

    def test_unsupported_extension_passthrough(self, tmp_path: Path):
        # Non-image, non-audio file: @-reference is left untouched
        p = tmp_path / "file.txt"
        p.write_bytes(b"hello")

        text, images, audio_clips = _expand_at_references(f"@{p}")

        assert images == []
        assert audio_clips == []
        assert f"@{p}" in text

    def test_missing_image_file_passthrough(self, tmp_path: Path):
        # Missing file: @-reference is left untouched (no crash)
        missing = tmp_path / "nope.png"
        text, images, audio_clips = _expand_at_references(f"@{missing}")
        assert images == []
        assert f"@{missing}" in text


class TestMultimodalContent:
    def test_audio_content_block(self):
        text = "请听这段音频: <<__AGENTICX_AUDIO_0__>>"
        audio_clips = [
            {"mime": "audio/mp3", "format": "mp3", "data": "YmFzZTY0"},
        ]
        content = _to_multimodal_content(text, [], audio_clips)

        assert isinstance(content, list)
        assert content[0]["type"] == "text"
        assert content[1]["type"] == "input_audio"
        assert content[1]["input_audio"]["format"] == "mp3"
        assert content[1]["input_audio"]["data"] == "YmFzZTY0"

    def test_mixed_image_audio_order_preserved(self):
        text = "先看图 <<__AGENTICX_IMG_0__>> 再听音 <<__AGENTICX_AUDIO_0__>>"
        images = ["data:image/png;base64,aaa"]
        audio_clips = [{"mime": "audio/wav", "format": "wav", "data": "bbb"}]
        content = _to_multimodal_content(text, images, audio_clips)

        assert isinstance(content, list)
        assert [b["type"] for b in content] == [
            "text",
            "image_url",
            "text",
            "input_audio",
        ]

    def test_no_media_returns_plain_string(self):
        assert _to_multimodal_content("just text", [], []) == "just text"


class TestNormalizeForModel:
    def test_audio_preserved_for_omni_model(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "x", "format": "mp3"},
                    },
                ],
            },
        ]
        normalized = normalize_for_model(messages, "qwen-omni-turbo")
        assert normalized[0]["content"][1]["type"] == "input_audio"

    def test_audio_dropped_for_vision_only_model(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "x", "format": "mp3"},
                    },
                ],
            },
        ]
        normalized = normalize_for_model(messages, "qwen-vl-max")
        blocks = normalized[0]["content"]
        assert any(b.get("type") == "image_url" for b in blocks)
        assert any(
            b.get("type") == "text" and "audio omitted" in b.get("text", "")
            for b in blocks
        )
        assert not any(b.get("type") == "input_audio" for b in blocks)

    def test_all_media_dropped_for_text_only_model(self):
        messages = [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": "hello"},
                    {
                        "type": "image_url",
                        "image_url": {"url": "data:image/png;base64,abc"},
                    },
                    {
                        "type": "input_audio",
                        "input_audio": {"data": "x", "format": "mp3"},
                    },
                ],
            },
        ]
        normalized = normalize_for_model(messages, "qwen-plus")
        assert isinstance(normalized[0]["content"], str)
        assert "image omitted" in normalized[0]["content"]
        assert "audio omitted" in normalized[0]["content"]


class TestMultimodalInputHandling:
    """String-expecting consumers must not crash on list content blocks."""

    def test_skill_package_is_active_accepts_list_content(self):
        pkg = SkillPackage(
            name="test",
            version="1.0",
            description="test",
            author="test",
            path=Path("/tmp/test"),
            triggers=["*deploy*"],
        )
        content = [
            {"type": "text", "text": "please deploy"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc"},
            },
        ]
        assert pkg.is_active(content) is True

    def test_registry_to_schema_list_accepts_list_content(self):
        registry = ToolRegistry()
        content = [
            {"type": "text", "text": "read file"},
            {
                "type": "image_url",
                "image_url": {"url": "data:image/png;base64,abc"},
            },
        ]
        # Should not raise; exact schema contents depend on registered tools
        schemas = registry.to_schema_list(content)
        assert isinstance(schemas, list)
