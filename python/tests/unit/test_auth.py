"""Unit tests for the auth layer (E11-S8).

Tests the token store, scope validation, and the auth extractor logic in isolation.
"""

from __future__ import annotations

import hashlib
import tempfile
from pathlib import Path

import pytest

from honba.api.auth import (
    AuthError,
    AuthErrorCode,
    FileTokenStore,
    TokenRecord,
    required_scope_for_route,
    validate_token,
)

# Test token: 32 bytes hex = 64 chars
TEST_TOKEN = "a" * 64
TEST_TOKEN_HASH = hashlib.sha256(TEST_TOKEN.encode()).hexdigest()


def test_required_scope_for_route() -> None:
    assert required_scope_for_route("GET", "/capabilities") is None
    assert required_scope_for_route("GET", "/health") is None
    assert required_scope_for_route("GET", "/schema") is None
    assert required_scope_for_route("GET", "/instruments") == "read:market"
    assert required_scope_for_route("GET", "/instruments/ABC") == "read:market"
    assert required_scope_for_route("GET", "/quotes") == "read:market"
    assert required_scope_for_route("GET", "/bars/123") == "read:market"
    assert required_scope_for_route("GET", "/depth/123") == "read:market"
    assert required_scope_for_route("GET", "/screener/scan") == "read:market"
    assert required_scope_for_route("GET", "/strategies") == "research"
    assert required_scope_for_route("POST", "/strategies") == "research"
    assert required_scope_for_route("POST", "/strategies/verify") == "research"
    assert required_scope_for_route("GET", "/backtests") == "research"
    assert required_scope_for_route("GET", "/backtests/abc") == "research"
    assert required_scope_for_route("GET", "/backtests/abc/journal") == "research"
    assert required_scope_for_route("GET", "/sweeps") == "research"
    assert required_scope_for_route("GET", "/sweeps/abc") == "research"
    assert required_scope_for_route("GET", "/journals/abc") == "research"
    assert required_scope_for_route("POST", "/backtests") == "research"
    assert required_scope_for_route("POST", "/sweeps") == "research"
    assert required_scope_for_route("POST", "/orders") == "write:orders"
    assert required_scope_for_route("DELETE", "/orders/abc") == "write:orders"
    assert required_scope_for_route("POST", "/positions/close") == "write:orders"
    assert required_scope_for_route("GET", "/approval/queue") == "write:orders"
    assert required_scope_for_route("POST", "/approval/approve") == "write:orders"
    assert required_scope_for_route("POST", "/approval/reject") == "write:orders"
    # Unknown route -> None (will 404 anyway)
    assert required_scope_for_route("GET", "/unknown") is None


class TestTokenRecord:
    def test_token_record_roundtrip(self) -> None:
        record = TokenRecord(
            token_id="a1b2c3d4",
            token_hash=f"sha256:{TEST_TOKEN_HASH}",
            scopes=["read:market", "research"],
            label="test-token",
            expires_at=None,
            created_at="2026-10-11T00:00:00Z",
        )
        assert record.matches_token(TEST_TOKEN) is True
        assert record.matches_token("b" * 64) is False
        assert record.is_expired() is False

    def test_token_record_expired(self) -> None:
        from datetime import datetime, timedelta, timezone

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        record = TokenRecord(
            token_id="a1b2c3d4",
            token_hash=f"sha256:{TEST_TOKEN_HASH}",
            scopes=["read:market"],
            label="expired",
            expires_at=past,
            created_at="2026-10-11T00:00:00Z",
        )
        assert record.is_expired() is True


class TestFileTokenStore:
    def test_load_tokens_from_toml(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / "tokens.toml"
            tokens_file.write_text(
                f"""[[tokens]]
token_id = "a1b2c3d4"
token_hash = "sha256:{TEST_TOKEN_HASH}"
scopes = ["read:market", "research"]
label = "test-token"
""",
                encoding="utf-8",
            )
            store = FileTokenStore(str(tokens_file))
            info = store.validate(TEST_TOKEN)
            assert info.token_id == "a1b2c3d4"
            assert info.scopes == ["read:market", "research"]

    def test_invalid_token_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / "tokens.toml"
            tokens_file.write_text(
                f"""[[tokens]]
token_id = "a1b2c3d4"
token_hash = "sha256:{TEST_TOKEN_HASH}"
scopes = ["read:market"]
label = "test-token"
""",
                encoding="utf-8",
            )
            store = FileTokenStore(str(tokens_file))
            with pytest.raises(AuthError) as exc:
                store.validate("b" * 64)
            assert exc.value.code == AuthErrorCode.INVALID_TOKEN

    def test_missing_token_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / "tokens.toml"
            tokens_file.write_text(
                f"""[[tokens]]
token_id = "a1b2c3d4"
token_hash = "sha256:{TEST_TOKEN_HASH}"
scopes = ["read:market"]
label = "test-token"
""",
                encoding="utf-8",
            )
            store = FileTokenStore(str(tokens_file))
            with pytest.raises(AuthError) as exc:
                store.validate("")
            assert exc.value.code == AuthErrorCode.MISSING_TOKEN

    def test_expired_token_raises(self) -> None:
        from datetime import datetime, timedelta, timezone

        past = (datetime.now(timezone.utc) - timedelta(days=1)).isoformat()
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / "tokens.toml"
            tokens_file.write_text(
                f"""[[tokens]]
token_id = "expired1"
token_hash = "sha256:{TEST_TOKEN_HASH}"
scopes = ["read:market"]
label = "expired-token"
expires_at = "{past}"
""",
                encoding="utf-8",
            )
            store = FileTokenStore(str(tokens_file))
            with pytest.raises(AuthError) as exc:
                store.validate(TEST_TOKEN)
            assert exc.value.code == AuthErrorCode.EXPIRED

    def test_scope_mismatch_in_validate(self) -> None:
        """validate_token checks scope, FileTokenStore does not."""
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / "tokens.toml"
            tokens_file.write_text(
                f"""[[tokens]]
token_id = "a1b2c3d4"
token_hash = "sha256:{TEST_TOKEN_HASH}"
scopes = ["read:market"]
label = "test-token"
""",
                encoding="utf-8",
            )
            store = FileTokenStore(str(tokens_file))
            # Store validates token but doesn't check scope
            info = store.validate(TEST_TOKEN)
            assert info.scopes == ["read:market"]


class TestValidateToken:
    def test_validate_token_grants_on_scope_match(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / "tokens.toml"
            tokens_file.write_text(
                f"""[[tokens]]
token_id = "a1b2c3d4"
token_hash = "sha256:{TEST_TOKEN_HASH}"
scopes = ["read:market", "research"]
label = "test-token"
""",
                encoding="utf-8",
            )
            store = FileTokenStore(str(tokens_file))
            info = validate_token(store, TEST_TOKEN, "read:market")
            assert info.token_id == "a1b2c3d4"

    def test_validate_token_denies_on_scope_mismatch(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            tokens_file = Path(tmp) / "tokens.toml"
            tokens_file.write_text(
                f"""[[tokens]]
token_id = "a1b2c3d4"
token_hash = "sha256:{TEST_TOKEN_HASH}"
scopes = ["read:market"]
label = "test-token"
""",
                encoding="utf-8",
            )
            store = FileTokenStore(str(tokens_file))
            with pytest.raises(AuthError) as exc:
                validate_token(store, TEST_TOKEN, "write:orders")
            assert exc.value.code == AuthErrorCode.FORBIDDEN
            assert exc.value.context is not None
            assert exc.value.context["required_scope"] == "write:orders"
            assert exc.value.context["token_scopes"] == ["read:market"]


class TestAuthErrorCode:
    def test_codes_are_stable_strings(self) -> None:
        assert AuthErrorCode.MISSING_TOKEN == "missing_token"
        assert AuthErrorCode.INVALID_TOKEN == "invalid_token"
        assert AuthErrorCode.EXPIRED == "expired"
        assert AuthErrorCode.FORBIDDEN == "forbidden"
