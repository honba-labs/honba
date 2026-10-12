"""Encrypted at-rest storage for broker secrets (E1-S5).

A broker token, once exchanged, is a bearer credential: it must not sit in plaintext on disk
and must never reach a log. :class:`FileSecretStore` keeps a JSON object of
``{key: fernet_ciphertext}`` at ``0600``, encrypted with :class:`cryptography.fernet.Fernet`
(a 32-byte key, base64url-encoded). The plaintext only ever exists inside :meth:`get`; the
store has no logger and its ``repr``/``str`` redact the key, so a secret cannot leak by
accident.

:meth:`FileSecretStore.rotate_key` decrypts every entry with the current key and re-encrypts
it under a new one, atomically replacing the file.

Key derivation: an Argon2id ``derive_key(password, salt)`` helper is **not** implemented here
because the ``argon2-cffi`` crate is not a dependency. Until it is added (as an optional
``adapters`` extra), callers supply 32 raw key bytes from a keyring, a KMS, or an env var.
Argon2id derivation is a documented next step, not a silent fallback.

Deliberately out of scope: TOTP auto-login / per-broker token exchange (ROADMAP section
3.5/3.1, deferred), and secret rotation on a schedule (an operator calls ``rotate_key``).
"""

from __future__ import annotations

import base64
import json
import os
import tempfile
from abc import ABC, abstractmethod
from pathlib import Path

from cryptography.fernet import Fernet

__all__ = ["FileSecretStore", "SecretStore"]


def _fernet_key(master_key: bytes) -> bytes:
    if len(master_key) != 32:
        raise ValueError(f"master_key must be exactly 32 bytes, got {len(master_key)}")
    return base64.urlsafe_b64encode(master_key)


class SecretStore(ABC):
    """A key/value store for broker secrets: values are encrypted at rest, never logged."""

    @abstractmethod
    def put(self, key: str, secret: str) -> None:
        """Store (or replace) the secret under ``key``."""
        ...

    @abstractmethod
    def get(self, key: str) -> str | None:
        """The decrypted secret for ``key``, or ``None`` when it is absent."""
        ...

    @abstractmethod
    def delete(self, key: str) -> None:
        """Remove ``key``; a missing key is a no-op."""
        ...


class FileSecretStore(SecretStore):
    """A :class:`SecretStore` persisted as an encrypted JSON file with ``0600`` permissions."""

    def __init__(self, path: str | os.PathLike[str], master_key: bytes) -> None:
        self._path = Path(path)
        self._master_key = master_key
        self._fernet = Fernet(_fernet_key(master_key))
        if not self._path.exists():
            self._write({})

    def _read(self) -> dict[str, str]:
        try:
            raw = self._path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        if not raw.strip():
            return {}
        payload = json.loads(raw)
        if not isinstance(payload, dict):
            raise TypeError("secret store file must contain a JSON object")
        return {str(entry_key): str(value) for entry_key, value in payload.items()}

    def _write(self, payload: dict[str, str]) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(dir=self._path.parent, prefix=".honba-secrets-")
        tmp = Path(tmp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(json.dumps(payload))
            os.chmod(tmp, 0o600)
            os.replace(tmp, self._path)
        except BaseException:
            tmp.unlink(missing_ok=True)
            raise
        os.chmod(self._path, 0o600)

    def put(self, key: str, secret: str) -> None:
        payload = self._read()
        payload[key] = self._fernet.encrypt(secret.encode("utf-8")).decode("ascii")
        self._write(payload)

    def get(self, key: str) -> str | None:
        encrypted = self._read().get(key)
        if encrypted is None:
            return None
        return self._fernet.decrypt(encrypted.encode("ascii")).decode("utf-8")

    def delete(self, key: str) -> None:
        payload = self._read()
        if payload.pop(key, None) is not None:
            self._write(payload)

    def rotate_key(self, new_key: bytes) -> None:
        """Re-encrypt every stored secret under ``new_key`` and atomically replace the file."""
        rotated_fernet = Fernet(_fernet_key(new_key))
        payload = {
            key: rotated_fernet.encrypt(self._fernet.decrypt(value.encode("ascii"))).decode("ascii")
            for key, value in self._read().items()
        }
        self._master_key = new_key
        self._fernet = rotated_fernet
        self._write(payload)

    def __repr__(self) -> str:
        return f"FileSecretStore(path={str(self._path)!r}, key=<redacted>)"

    __str__ = __repr__
