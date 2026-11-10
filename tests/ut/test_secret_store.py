# -*- coding: utf-8 -*-
"""OS keyring reference and failure-mode regressions."""
from __future__ import annotations

import pytest

from dashscope_agenticx.utils.secret_store import (
    _backend,
    delete_secret,
    is_keyring_reference,
    resolve_secret,
    set_secret_backend,
    store_secret,
)


def test_store_resolve_delete_roundtrip(isolated_secret_store):
    reference = store_secret("sk-secret", account="provider:tongyi")
    assert reference == "KEYRING:v1:provider:tongyi"
    assert is_keyring_reference(reference)
    assert resolve_secret(reference) == "sk-secret"
    delete_secret(reference)
    assert resolve_secret(reference) == ""


def test_store_writes_only_to_the_agenticx_service(isolated_secret_store):
    reference = store_secret("sk-new-service", account="provider:tongyi")

    assert isolated_secret_store.values == {
        ("dashscope-agenticx", "provider:tongyi"): "sk-new-service",
    }
    assert resolve_secret(reference) == "sk-new-service"


def test_resolve_falls_back_to_legacy_agenticcli_service(
    isolated_secret_store,
):
    reference = "KEYRING:v1:provider:legacy"
    isolated_secret_store.values[
        ("agenticcli", "provider:legacy")
    ] = "sk-legacy"

    assert resolve_secret(reference) == "sk-legacy"


def test_resolve_prefers_new_service_when_both_exist(isolated_secret_store):
    reference = "KEYRING:v1:provider:shared"
    isolated_secret_store.values.update(
        {
            ("dashscope-agenticx", "provider:shared"): "sk-new",
            ("agenticcli", "provider:shared"): "sk-old",
        },
    )

    assert resolve_secret(reference) == "sk-new"


def test_delete_removes_new_and_legacy_service_entries(isolated_secret_store):
    reference = "KEYRING:v1:provider:legacy"
    isolated_secret_store.values.update(
        {
            ("dashscope-agenticx", "provider:legacy"): "sk-new",
            ("agenticcli", "provider:legacy"): "sk-old",
        },
    )

    delete_secret(reference)

    assert (
        "dashscope-agenticx",
        "provider:legacy",
    ) not in isolated_secret_store.values
    assert (
        "agenticcli",
        "provider:legacy",
    ) not in isolated_secret_store.values


def test_legacy_enc_value_still_uses_machine_local_decryption(
    isolated_secret_store,
):
    from dashscope_agenticx.utils.crypto import encrypt_value

    encrypted = encrypt_value("sk-encrypted-legacy")

    assert encrypted.startswith("ENC:")
    assert resolve_secret(encrypted) == "sk-encrypted-legacy"
    assert isolated_secret_store.values == {}


def test_keyring_failure_never_returns_a_config_value():
    class BrokenBackend:
        def set_password(self, service, account, secret):
            raise RuntimeError("locked")

        def get_password(self, service, account):
            raise RuntimeError("locked")

        def delete_password(self, service, account):
            raise RuntimeError("locked")

    set_secret_backend(BrokenBackend())
    with pytest.raises(RuntimeError, match="no secret was written"):
        store_secret("sk-must-not-leak", account="provider:test")


def test_secret_store_edge_paths(capsys, isolated_secret_store):
    assert store_secret("") == ""
    assert resolve_secret("") == ""
    assert resolve_secret("legacy-plaintext") == "legacy-plaintext"
    with pytest.raises(ValueError, match="must not be empty"):
        store_secret("secret", account="!!!")
    delete_secret("not-a-reference")

    class ReadDeleteFailure:
        def set_password(self, service, account, secret):
            pass

        def get_password(self, service, account):
            raise RuntimeError("locked")

        def delete_password(self, service, account):
            raise RuntimeError("locked")

    set_secret_backend(ReadDeleteFailure())
    reference = "KEYRING:v1:provider:test"
    assert resolve_secret(reference) == ""
    assert "failed to read credential" in capsys.readouterr().err
    delete_secret(reference)


def test_default_backend_import_path(monkeypatch):
    import dashscope_agenticx.utils.secret_store as secret_store

    monkeypatch.setattr(secret_store, "_backend_override", None)
    assert _backend().__name__ == "keyring"
