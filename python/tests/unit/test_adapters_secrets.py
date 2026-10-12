"""Unit tests for the encrypted at-rest secret store (E1-S5).

Acceptance: ``no_secret_in_logs``. Also checks round-trip, ``0600`` file permissions, that the
plaintext never reaches the file, that ``repr``/``str`` redact, and that rotation re-encrypts.
"""

from __future__ import annotations

import logging
import os
import stat

import pytest
from cryptography.fernet import InvalidToken
from honba.adapters.secrets import FileSecretStore

_SECRET = "dhan-access-token-9f3c1d"


def test_store_round_trip_and_delete(tmp_path) -> None:
    store = FileSecretStore(tmp_path / "secrets.json", master_key=os.urandom(32))
    assert store.get("dhan") is None
    store.put("dhan", _SECRET)
    assert store.get("dhan") == _SECRET
    store.delete("dhan")
    assert store.get("dhan") is None


def test_no_secret_in_logs_or_repr(tmp_path, caplog) -> None:
    path = tmp_path / "secrets.json"
    store = FileSecretStore(path, master_key=os.urandom(32))
    with caplog.at_level(logging.DEBUG):
        store.put("dhan", _SECRET)
        assert store.get("dhan") == _SECRET
        logging.getLogger("honba").debug("store is %r / %s", store, store)
        assert _SECRET not in repr(store)
        assert _SECRET not in str(store)

    assert _SECRET not in caplog.text
    assert _SECRET not in path.read_text()


def test_file_is_encrypted_and_mode_0600(tmp_path) -> None:
    path = tmp_path / "secrets.json"
    store = FileSecretStore(path, master_key=os.urandom(32))
    store.put("dhan", _SECRET)

    raw = path.read_text()
    assert _SECRET not in raw
    assert stat.S_IMODE(path.stat().st_mode) == 0o600


def test_rotate_key_re_encrypts(tmp_path) -> None:
    path = tmp_path / "secrets.json"
    old_key = os.urandom(32)
    new_key = os.urandom(32)
    store = FileSecretStore(path, old_key)
    store.put("dhan", _SECRET)
    before = path.read_text()

    store.rotate_key(new_key)
    after = path.read_text()

    assert before != after
    assert store.get("dhan") == _SECRET

    with pytest.raises(InvalidToken):
        FileSecretStore(path, old_key).get("dhan")


def test_rejects_wrong_key_length(tmp_path) -> None:
    with pytest.raises(ValueError):
        FileSecretStore(tmp_path / "secrets.json", master_key=b"too-short")
