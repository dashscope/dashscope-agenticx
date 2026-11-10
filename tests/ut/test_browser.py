# -*- coding: utf-8 -*-
"""Tests for tools/browser.py — URL validation, timeout clamping,
screenshot path validation, CSS selector sanitisation, and IP safety check.
All tests are pure-Python; no Playwright instance is created.
"""

from __future__ import annotations

import os
import socket

import pytest

from dashscope_agenticx.tools.browser import (
    _MAX_TIMEOUT_MS,
    _MIN_TIMEOUT_MS,
    _clamp_timeout,
    _is_safe_response_ip,
    _validate_screenshot_path,
    _validate_selector,
    _validate_url,
)

# ── _validate_url ──────────────────────────────────────────────────────────


def _public_dns(_hostname, port):
    return [
        (
            socket.AF_INET,
            socket.SOCK_STREAM,
            socket.IPPROTO_TCP,
            "",
            ("93.184.216.34", port),
        ),
    ]


def test_validate_url_https_ok(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    url = "https://example.com/path"
    assert _validate_url(url) == url


def test_validate_url_http_ok(monkeypatch):
    monkeypatch.setattr(socket, "getaddrinfo", _public_dns)
    url = "http://example.com"
    assert _validate_url(url) == url


def test_validate_url_rejects_file_scheme():
    with pytest.raises(ValueError, match="scheme"):
        _validate_url("file:///etc/passwd")


def test_validate_url_rejects_javascript_scheme():
    with pytest.raises(ValueError, match="scheme"):
        _validate_url("javascript:alert(1)")


def test_validate_url_rejects_data_scheme():
    with pytest.raises(ValueError, match="scheme"):
        _validate_url("data:text/html,<h1>hi</h1>")


def test_validate_url_rejects_localhost_ipv4():
    with pytest.raises(ValueError, match="SSRF"):
        _validate_url("http://127.0.0.1/admin")


def test_validate_url_rejects_private_ipv4_10():
    with pytest.raises(ValueError, match="SSRF"):
        _validate_url("http://10.0.0.1/secret")


def test_validate_url_rejects_private_ipv4_192():
    with pytest.raises(ValueError, match="SSRF"):
        _validate_url("http://192.168.1.1/")


def test_validate_url_rejects_ipv6_loopback():
    with pytest.raises(ValueError, match="SSRF"):
        _validate_url("http://[::1]/")


def test_validate_url_rejects_missing_hostname():
    with pytest.raises(ValueError, match="hostname"):
        _validate_url("https:///no-host")


def test_validate_url_rejects_link_local():
    with pytest.raises(ValueError, match="SSRF"):
        _validate_url("http://169.254.169.254/latest/meta-data/")


# ── _clamp_timeout ─────────────────────────────────────────────────────────


def test_clamp_timeout_below_min():
    assert _clamp_timeout(0) == _MIN_TIMEOUT_MS


def test_clamp_timeout_above_max():
    assert _clamp_timeout(999_999) == _MAX_TIMEOUT_MS


def test_clamp_timeout_in_range():
    assert _clamp_timeout(15_000) == 15_000


def test_clamp_timeout_at_min_boundary():
    assert _clamp_timeout(_MIN_TIMEOUT_MS) == _MIN_TIMEOUT_MS


def test_clamp_timeout_at_max_boundary():
    assert _clamp_timeout(_MAX_TIMEOUT_MS) == _MAX_TIMEOUT_MS


# ── _validate_screenshot_path ──────────────────────────────────────────────


def test_validate_screenshot_path_in_cwd(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _validate_screenshot_path("screenshot.png")
    assert result == os.path.realpath(str(tmp_path / "screenshot.png"))


def test_validate_screenshot_path_subdirectory_created(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    result = _validate_screenshot_path("subdir/shot.png")
    assert os.path.isdir(str(tmp_path / "subdir"))
    assert result.endswith("shot.png")


def test_validate_screenshot_path_outside_cwd_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="path traversal"):
        _validate_screenshot_path("/tmp/evil.png")


def test_validate_screenshot_path_traversal_rejected(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(ValueError, match="path traversal"):
        _validate_screenshot_path("../../etc/passwd.png")


# ── _validate_selector ─────────────────────────────────────────────────────


def test_validate_selector_empty_string():
    assert _validate_selector("") == ""


def test_validate_selector_normal_css():
    sel = "div.container > p:first-child"
    assert _validate_selector(sel) == sel


def test_validate_selector_id_selector():
    sel = "#main-content"
    assert _validate_selector(sel) == sel


def test_validate_selector_attribute_selector():
    sel = "input[type='text']"
    assert _validate_selector(sel) == sel


def test_validate_selector_rejects_backtick():
    with pytest.raises(ValueError, match="suspicious"):
        _validate_selector("div`rm -rf`")


def test_validate_selector_rejects_javascript_proto():
    with pytest.raises(ValueError, match="suspicious"):
        _validate_selector("javascript:void(0)")


def test_validate_selector_rejects_eval():
    with pytest.raises(ValueError, match="suspicious"):
        _validate_selector("eval(alert)")


def test_validate_selector_rejects_script_tag():
    with pytest.raises(ValueError, match="suspicious"):
        _validate_selector("<script>")


# ── _is_safe_response_ip ───────────────────────────────────────────────────


def test_is_safe_response_ip_public_ipv4():
    assert _is_safe_response_ip("http://8.8.8.8/") is True


def test_is_safe_response_ip_private_10():
    assert _is_safe_response_ip("http://10.0.0.1/") is False


def test_is_safe_response_ip_loopback():
    assert _is_safe_response_ip("http://127.0.0.1/") is False


def test_is_safe_response_ip_link_local():
    assert _is_safe_response_ip("http://169.254.1.1/") is False


def test_is_safe_response_ip_no_hostname():
    assert _is_safe_response_ip("https:///no-host") is False


def test_is_safe_response_ip_ipv6_loopback():
    assert _is_safe_response_ip("http://[::1]/") is False
