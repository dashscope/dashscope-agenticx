# -*- coding: utf-8 -*-
"""Tests for dashscope_agenticx.utils.images.image_to_data_url."""

from __future__ import annotations

from pathlib import Path

import pytest

from dashscope_agenticx.cli.constants import _AT_IMAGE_MAX_BYTES, _IMAGE_MIME
from dashscope_agenticx.utils.images import image_to_data_url


class TestImageToDataUrl:
    def test_png_roundtrip(self, tmp_path: Path):
        data = b"\x89PNG\r\n\x1a\n" + b"fake png body" * 5
        p = tmp_path / "pic.png"
        p.write_bytes(data)

        url = image_to_data_url(p)

        assert url.startswith("data:image/png;base64,")
        b64 = url.split(",", 1)[1]
        import base64

        assert base64.b64decode(b64) == data

    @pytest.mark.parametrize(
        "suffix,mime",
        [
            (".jpg", "image/jpeg"),
            (".jpeg", "image/jpeg"),
            (".gif", "image/gif"),
            (".webp", "image/webp"),
            (".bmp", "image/bmp"),
        ],
    )
    def test_supported_extensions(
        self,
        tmp_path: Path,
        suffix: str,
        mime: str,
    ):
        p = tmp_path / f"img{suffix}"
        p.write_bytes(b"fake-image-bytes")
        url = image_to_data_url(p)
        assert url.startswith(f"data:{mime};base64,")

    def test_unsupported_extension_raises_value_error(self, tmp_path: Path):
        p = tmp_path / "file.txt"
        p.write_bytes(b"not an image")
        with pytest.raises(ValueError, match="Unsupported image extension"):
            image_to_data_url(p)

    def test_oversize_raises_value_error(self, tmp_path: Path):
        p = tmp_path / "big.png"
        p.write_bytes(b"x" * (_AT_IMAGE_MAX_BYTES + 1))
        with pytest.raises(ValueError, match="Image too large"):
            image_to_data_url(p)

    def test_missing_file_raises_oserror(self, tmp_path: Path):
        p = tmp_path / "nope.png"
        with pytest.raises(OSError):
            image_to_data_url(p)

    def test_string_path_works(self, tmp_path: Path):
        p = tmp_path / "pic.png"
        p.write_bytes(b"png")
        url = image_to_data_url(str(p))
        assert url.startswith("data:image/png;base64,")

    def test_user_expansion(self, tmp_path: Path, monkeypatch):
        # Point HOME at the tmp_path so ~/pic.png resolves there.
        monkeypatch.setenv("HOME", str(tmp_path))
        p = tmp_path / "pic.png"
        p.write_bytes(b"png")
        url = image_to_data_url("~/pic.png")
        assert url.startswith("data:image/png;base64,")

    def test_extension_lookup_is_case_insensitive(self, tmp_path: Path):
        # _IMAGE_MIME keys lowercase suffixes; a mixed-case suffix should
        # still resolve to the right mime.
        p = tmp_path / "PIC.PNG"
        p.write_bytes(b"png")
        url = image_to_data_url(p)
        assert url.startswith("data:image/png;base64,")
        # sanity check that .PNG is in the mime map (not a new suffix)
        assert ".png" in _IMAGE_MIME
