"""Minimal auth layer for honba REST API (E11-S8, ADR 0023).

Provides Bearer token validation, scope enforcement, file-based token store, and audit logging.
"""

from __future__ import annotations

import hashlib
import logging
import threading
from collections.abc import Iterable
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

try:
    import tomllib
except ImportError:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

__all__ = [
    "AUTHENTICATED",
    "PUBLIC_PATHS",
    "SCOPES",
    "AuthError",
    "AuthErrorCode",
    "AuthLayer",
    "FileTokenStore",
    "TokenInfo",
    "TokenRecord",
    "TokenStore",
    "audit_log",
    "required_scope_for_route",
    "unscoped_routes",
    "validate_token",
]

logger = logging.getLogger("honba.auth")

# Audit log singleton
_audit_logger: logging.Logger | None = None
_audit_lock = threading.Lock()


def audit_log() -> logging.Logger:
    """Get or create the auth audit logger (writes NDJSON to file)."""
    global _audit_logger
    if _audit_logger is None:
        with _audit_lock:
            if _audit_logger is None:
                _audit_logger = logging.getLogger("honba.auth.audit")
                _audit_logger.setLevel(logging.INFO)
                _audit_logger.propagate = False
    return _audit_logger


def configure_audit_log(path: str | Path) -> None:
    """Attach a file handler to the audit logger."""
    log = audit_log()
    for handler in log.handlers[:]:
        log.removeHandler(handler)
        handler.close()
    handler = logging.FileHandler(path, encoding="utf-8")
    handler.setFormatter(logging.Formatter("%(message)s"))
    log.addHandler(handler)


class AuthErrorCode:
    """Stable error codes for auth failures."""

    MISSING_TOKEN = "missing_token"
    INVALID_TOKEN = "invalid_token"
    EXPIRED = "expired"
    FORBIDDEN = "forbidden"


class AuthError(Exception):
    """An authentication/authorization failure with a stable code."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        context: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.context = context or {}


@dataclass(frozen=True, slots=True)
class TokenInfo:
    """Validated token information returned to handlers."""

    token_id: str
    scopes: list[str]


@dataclass(frozen=True, slots=True)
class TokenRecord:
    """A token record as stored in the token store."""

    token_id: str
    token_hash: str  # "sha256:<hex>"
    scopes: list[str]
    label: str
    expires_at: str | None
    created_at: str

    def matches_token(self, token: str) -> bool:
        """Check if the given plain token matches this record."""
        if not token:
            return False
        computed = hashlib.sha256(token.encode()).hexdigest()
        expected = self.token_hash
        expected = expected.removeprefix("sha256:")
        return computed == expected

    def is_expired(self) -> bool:
        if self.expires_at is None:
            return False
        try:
            expiry = datetime.fromisoformat(self.expires_at.replace("Z", "+00:00"))
            return datetime.now(timezone.utc) >= expiry
        except ValueError:
            return True


class TokenStore:
    """Abstract token store interface."""

    def validate(self, token: str) -> TokenInfo:
        """Validate a plain token and return its info.

        Raises:
            AuthError: on missing, invalid, or expired token.
        """
        raise NotImplementedError


class FileTokenStore(TokenStore):
    """File-backed token store reading a TOML file.

    The file is watched for changes and reloaded on modification (polling).
    """

    def __init__(self, path: str | Path, poll_interval: float = 1.0) -> None:
        self.path = Path(path)
        self._records: list[TokenRecord] = []
        self._poll_interval = poll_interval
        self._last_mtime: float = 0.0
        self._lock = threading.RLock()
        self._load()

    def _load(self) -> None:
        try:
            mtime = self.path.stat().st_mtime
            if mtime == self._last_mtime:
                return
            data = tomllib.loads(self.path.read_text(encoding="utf-8"))
            tokens = data.get("tokens", [])
            records: list[TokenRecord] = []
            for t in tokens:
                record = TokenRecord(
                    token_id=str(t["token_id"]),
                    token_hash=str(t["token_hash"]),
                    scopes=[str(s) for s in t.get("scopes", [])],
                    label=str(t.get("label", "")),
                    expires_at=t.get("expires_at"),
                    created_at=str(t.get("created_at", datetime.now(timezone.utc).isoformat())),
                )
                records.append(record)
            with self._lock:
                self._records = records
                self._last_mtime = mtime
            logger.info("Loaded %d token(s) from %s", len(records), self.path)
        except FileNotFoundError:
            with self._lock:
                self._records = []
                self._last_mtime = 0.0
            logger.warning("Token file not found: %s", self.path)
        except Exception as exc:
            logger.error("Failed to load token file %s: %s", self.path, exc)
            raise

    def validate(self, token: str) -> TokenInfo:
        self._load()
        if not token:
            raise AuthError(AuthErrorCode.MISSING_TOKEN, "Authorization header missing or empty")
        with self._lock:
            for record in self._records:
                if record.matches_token(token):
                    if record.is_expired():
                        raise AuthError(
                            AuthErrorCode.EXPIRED,
                            f"Token {record.token_id} has expired",
                            context={"token_id": record.token_id},
                        )
                    return TokenInfo(token_id=record.token_id, scopes=list(record.scopes))
        raise AuthError(
            AuthErrorCode.INVALID_TOKEN,
            "Invalid or unknown token",
            context={"token_prefix": token[:8] if len(token) >= 8 else token},
        )


#: Routes that need no token (health and capability metadata).
PUBLIC_PATHS = frozenset({"/capabilities", "/health", "/schema"})

#: The scopes ADR 0023 §2 defines; a route may name no other.
SCOPES = ("read:market", "read:account", "write:orders", "research", "operator")


#: Sentinel for routes that need a valid token but no scope (the `/profile*` routes: any valid
#: token manages its own profile). It is not a scope: no token record may grant it.
AUTHENTICATED = "authenticated"


def required_scope_for_route(method: str, path: str) -> str | None:
    """Return the required scope for a given route, or None if public or unknown.

    Returns :data:`AUTHENTICATED` for routes that accept any valid token.

    ``method`` is accepted for parity with the Rust server; on this surface the
    scope is a property of the path (see ADR 0023 §2).
    """
    # Exact matches first
    if path in PUBLIC_PATHS:
        return None

    # Prefix matches
    if path.startswith(("/instruments", "/quotes", "/bars/", "/depth/")):
        return "read:market"
    if path == "/screener/scan":
        return "read:market"
    if path in {"/strategies", "/strategies/verify"}:
        return "research"
    if path.startswith(("/backtests", "/sweeps", "/journals/")):
        return "research"
    if path == "/trading-state":
        return "operator"
    if path in {"/orders", "/positions/close", "/bars"} or path.startswith("/orders/"):
        return "write:orders"
    if path.startswith("/approval/"):
        return "write:orders"
    if path == "/profile" or path.startswith("/profile/"):
        return AUTHENTICATED
    return None


def unscoped_routes(routes: Iterable[tuple[str, str]]) -> list[str]:
    """Return ``"METHOD path"`` keys for routes that are neither public nor scoped.

    The drift guard: every endpoint the OpenAPI spec exposes must resolve to a
    scope, so a route added without one cannot ship as an accidental public route.
    """
    out: list[str] = []
    for method, path in routes:
        if path in PUBLIC_PATHS:
            continue
        if required_scope_for_route(method, path) is None:
            out.append(f"{method} {path}")
    return out


def validate_token(store: TokenStore, token: str, required_scope: str) -> TokenInfo:
    """Validate token and check it has the required scope (any token for AUTHENTICATED)."""
    info = store.validate(token)
    if required_scope != AUTHENTICATED and required_scope not in info.scopes:
        raise AuthError(
            AuthErrorCode.FORBIDDEN,
            f"Token does not have required scope: {required_scope}",
            context={
                "required_scope": required_scope,
                "token_scopes": info.scopes,
                "token_id": info.token_id,
            },
        )
    return info


@dataclass
class AuthLayer:
    """Axum auth layer: wraps a token store and enforces scopes per route."""

    store: TokenStore
    audit_log_path: str | Path | None = None

    def __post_init__(self) -> None:
        if self.audit_log_path:
            configure_audit_log(self.audit_log_path)

    def _audit(
        self,
        *,
        token_id: str | None,
        route: str,
        method: str,
        decision: str,
        reason: str | None = None,
        required_scope: str | None = None,
        token_scopes: list[str] | None = None,
        client_ip: str | None = None,
    ) -> None:
        log = audit_log()
        if not log.handlers:
            return
        entry = {
            "ts": datetime.now(timezone.utc).isoformat(),
            "event": "auth.decision",
            "token_id": token_id or "unknown",
            "route": route,
            "method": method,
            "decision": decision,
            "reason": reason,
            "required_scope": required_scope,
            "token_scopes": token_scopes,
            "client_ip": client_ip,
        }
        # Remove None values
        entry = {k: v for k, v in entry.items() if v is not None}
        import json

        log.info(json.dumps(entry, separators=(",", ":")))

    def extract(
        self, method: str, path: str, auth_header: str | None, client_ip: str | None = None
    ) -> TokenInfo | None:
        """Extract and validate token from Authorization header.

        Returns TokenInfo on success, raises AuthError on failure.
        Returns None if no auth is required for this route.
        """
        required = required_scope_for_route(method, path)
        if required is None:
            return None

        if not auth_header or not auth_header.startswith("Bearer "):
            self._audit(
                token_id=None,
                route=path,
                method=method,
                decision="denied",
                reason="missing_token",
                required_scope=required,
                client_ip=client_ip,
            )
            raise AuthError(
                AuthErrorCode.MISSING_TOKEN, "Authorization header missing or not Bearer"
            )

        token = auth_header[7:].strip()
        try:
            info = validate_token(self.store, token, required)
        except AuthError as exc:
            self._audit(
                token_id=exc.context.get("token_id"),
                route=path,
                method=method,
                decision="denied",
                reason=exc.code,
                required_scope=required,
                token_scopes=exc.context.get("token_scopes"),
                client_ip=client_ip,
            )
            raise

        self._audit(
            token_id=info.token_id,
            route=path,
            method=method,
            decision="granted",
            required_scope=required,
            token_scopes=info.scopes,
            client_ip=client_ip,
        )
        return info
