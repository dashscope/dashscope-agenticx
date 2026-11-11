# -*- coding: utf-8 -*-
from __future__ import annotations

import os
from urllib.parse import urlparse

from dashscope_agenticx import SDK_SESSION_ID, __version__

_DASHSCOPE_HOSTS = {
    "dashscope.aliyuncs.com",
    "dashscope-intl.aliyuncs.com",
}


def is_dashscope_endpoint(base_url: str | None) -> bool:
    if not base_url:
        return False
    parsed = urlparse(base_url if "://" in base_url else f"https://{base_url}")
    host = (parsed.hostname or "").lower()
    return host in _DASHSCOPE_HOSTS or host.endswith(".maas.aliyuncs.com")


def dashscope_sdk_headers(
    module: str = "app",
    *,
    base_url: str | None = None,
) -> dict[str, str]:
    if os.environ.get("DASHSCOPE_DISABLE_SDK_HEADERS"):
        return {}
    if base_url is not None and not is_dashscope_endpoint(base_url):
        return {}
    parts = ["dashscope-agenticx", __version__]
    if module:
        parts.append(module)
    return {
        "x-dashscope-sdk-client": "/".join(parts),
        "x-dashscope-sdk-session-id": SDK_SESSION_ID,
    }


def mark_dashscope_sdk_client() -> None:
    from dashscope.legacy_1x.common.utils import set_sdk_client

    set_sdk_client("dashscope-agenticx")
