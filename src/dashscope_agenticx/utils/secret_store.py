# -*- coding: utf-8 -*-
"""OS keyring-backed secret references with legacy ENC migration support."""
from __future__ import annotations

import re
import sys
import uuid
from typing import Protocol

_SERVICE = "dashscope-agenticx"
_LEGACY_SERVICE = "agenticcli"
_REF_PREFIX = "KEYRING:v1:"
_ACCOUNT_RE = re.compile(r"[^A-Za-z0-9_.:@/-]+")


class SecretBackend(Protocol):
    def set_password(
        self,
        service: str,
        account: str,
        secret: str,
    ) -> None:
        ...

    def get_password(self, service: str, account: str) -> str | None:
        ...

    def delete_password(self, service: str, account: str) -> None:
        ...


_backend_override: SecretBackend | None = None


def set_secret_backend(backend: SecretBackend | None) -> None:
    """Override the backend for tests or embedded hosts."""
    global _backend_override
    _backend_override = backend


def _backend() -> SecretBackend:
    if _backend_override is not None:
        return _backend_override
    try:
        import keyring
    except ImportError as exc:  # pragma: no cover - installed dependency
        raise RuntimeError(
            "OS keyring support is unavailable; install the 'keyring' "
            "package or provide the credential through an environment "
            "variable",
        ) from exc
    return keyring


def _account(value: str | None = None) -> str:
    raw = value or f"secret:{uuid.uuid4().hex}"
    cleaned = _ACCOUNT_RE.sub("_", raw).strip("_")
    if not cleaned:
        raise ValueError("secret account must not be empty")
    return cleaned[:240]


def is_keyring_reference(value: str) -> bool:
    return value.startswith(_REF_PREFIX)


def store_secret(secret: str, *, account: str | None = None) -> str:
    """Store a secret in the OS keyring and return a non-secret reference."""
    if not secret:
        return ""
    key = _account(account)
    try:
        _backend().set_password(_SERVICE, key, secret)
    except Exception as exc:
        raise RuntimeError(
            "Failed to store credential in the OS keyring; no secret was "
            "written to configuration",
        ) from exc
    return _REF_PREFIX + key


def resolve_secret(stored: str) -> str:
    """Resolve a keyring reference or read a legacy ENC/plaintext value."""
    if not stored:
        return ""
    if is_keyring_reference(stored):
        account = stored[len(_REF_PREFIX) :]
        backend = _backend()
        errors = []
        for service in (_SERVICE, _LEGACY_SERVICE):
            try:
                value = backend.get_password(service, account)
            except Exception as exc:
                errors.append(exc)
                continue
            if value is not None:
                return value
        if errors:
            print(
                "agenticx: failed to read credential from OS keyring: "
                f"{errors[-1]}",
                file=sys.stderr,
            )
        else:
            print(
                "agenticx: credential reference is missing from the OS "
                "keyring; re-enter it via /provider",
                file=sys.stderr,
            )
        return ""
    if stored.startswith("ENC:"):
        from dashscope_agenticx.utils.crypto import decrypt_value

        return decrypt_value(stored)
    # Legacy plaintext is accepted for migration only. The next config save
    # replaces it with a keyring reference.
    return stored


def delete_secret(reference: str) -> None:
    if not is_keyring_reference(reference):
        return
    account = reference[len(_REF_PREFIX) :]
    try:
        backend = _backend()
    except Exception:
        return
    for service in (_SERVICE, _LEGACY_SERVICE):
        try:
            backend.delete_password(service, account)
        except Exception:
            pass
