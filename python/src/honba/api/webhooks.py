"""Webhook order ingestion (ADR 0026, E5-S6).

An ordered, fail-closed pipeline that turns an inbound HTTP webhook into an
:class:`~honba.domain.order.OrderIntent`, gated by the same kill switch, token
hashing, risk stage and journal as every other order write path.

The design is ADR 0026: a fixed stage order (kill switch, IP allowlist, size cap,
token, parse, live gate, idempotency, cool-off, risk, journal) that stops at the
first refusal, one opaque 401 for every authentication failure (stages 1, 2 and 4)
so the endpoint is never an oracle, SHA-256 token hashes stored instead of the
plaintext, a raw-bytes size cap checked before any parse, and TTL caches driven by
an injected monotonic clock so nothing in the decision path reads the wall clock.
Every terminal decision is written to the shared ``honba.api.auth`` audit logger.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field
from enum import Enum
from typing import Any

from honba.api.auth import audit_log
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderType, TimeInForce

__all__ = [
    "TtlCache",
    "WebhookConfig",
    "WebhookIngestor",
    "WebhookRefusal",
    "WebhookRequest",
    "WebhookResponse",
    "WebhookStage",
]

_TOKEN_HASH_RE = re.compile(r"sha256:[0-9a-f]{64}\Z")

_CONFIG_KEYS = frozenset(
    {
        "enabled",
        "token_hashes",
        "ip_allowlist",
        "max_body_bytes",
        "idempotency_ttl_s",
        "cool_off_ttl_s",
        "cool_off_max",
        "require_live",
    }
)


class WebhookStage(str, Enum):
    """The ordered stages of the ingestion pipeline (ADR 0026 §2)."""

    KILL_SWITCH = "kill_switch"
    IP_ALLOWLIST = "ip_allowlist"
    SIZE = "size"
    TOKEN = "token"
    PARSE = "parse"
    LIVE_GATE = "live_gate"
    IDEMPOTENCY = "idempotency"
    COOL_OFF = "cool_off"
    RISK = "risk"
    JOURNAL = "journal"


def _require_bool(value: Any, key: str) -> bool:
    if not isinstance(value, bool):
        raise TypeError(f"webhook config {key} must be a bool, got {type(value).__name__}")
    return value


def _require_int(value: Any, key: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"webhook config {key} must be an int, got {type(value).__name__}")
    return value


def _require_number(value: Any, key: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError(f"webhook config {key} must be a number, got {type(value).__name__}")
    return float(value)


def _require_str_tuple(value: Any, key: str) -> tuple[str, ...]:
    if isinstance(value, (str, bytes)) or not isinstance(value, Sequence):
        raise TypeError(
            f"webhook config {key} must be a sequence of strings, got {type(value).__name__}"
        )
    items: list[str] = []
    for item in value:
        if not isinstance(item, str):
            raise TypeError(
                f"webhook config {key} entries must be strings, got {type(item).__name__}"
            )
        items.append(item)
    return tuple(items)


def _validate_token_hashes(value: Any) -> tuple[str, ...]:
    hashes = _require_str_tuple(value, "token_hashes")
    for entry in hashes:
        if _TOKEN_HASH_RE.fullmatch(entry) is None:
            raise ValueError(
                "token_hashes entries must be 'sha256:<64 lowercase hex>', "
                f"got {entry!r} (store hashes, never plaintext)"
            )
    return hashes


@dataclass(frozen=True, slots=True)
class WebhookConfig:
    """Webhook ingestion settings, parsed from a ``[webhooks]`` TOML table.

    ``token_hashes`` entries are ``"sha256:<64 lowercase hex>"``; the plaintext
    token is never stored. ``from_dict`` refuses unknown keys (like
    :meth:`honba.risk.RiskLimits.from_dict`), and the constructor rejects any
    entry that is not a well-formed SHA-256 hash.
    """

    enabled: bool = True
    token_hashes: tuple[str, ...] = ()
    ip_allowlist: tuple[str, ...] = ()
    max_body_bytes: int = 64 * 1024
    idempotency_ttl_s: float = 300.0
    cool_off_ttl_s: float = 1.0
    cool_off_max: int = 10
    require_live: bool = False

    def __post_init__(self) -> None:
        object.__setattr__(self, "token_hashes", _validate_token_hashes(self.token_hashes))
        object.__setattr__(
            self, "ip_allowlist", _require_str_tuple(self.ip_allowlist, "ip_allowlist")
        )

    @classmethod
    def from_dict(cls, table: Mapping[str, Any]) -> WebhookConfig:
        """Parse a ``[webhooks]`` table.

        Refuses unknown keys and malformed values with ``ValueError``, like
        :meth:`honba.risk.RiskLimits.from_dict`.
        """
        if not isinstance(table, Mapping):
            raise TypeError(f"webhook config must be a mapping, got {type(table).__name__}")
        unknown = sorted(set(table) - _CONFIG_KEYS)
        if unknown:
            raise ValueError(f"unknown webhook config key(s): {unknown}")
        kwargs: dict[str, Any] = {}
        try:
            for key, value in table.items():
                if key in ("enabled", "require_live"):
                    kwargs[key] = _require_bool(value, key)
                elif key in ("max_body_bytes", "cool_off_max"):
                    kwargs[key] = _require_int(value, key)
                elif key in ("idempotency_ttl_s", "cool_off_ttl_s"):
                    kwargs[key] = _require_number(value, key)
                elif key == "token_hashes":
                    kwargs[key] = _validate_token_hashes(value)
                elif key == "ip_allowlist":
                    kwargs[key] = _require_str_tuple(value, key)
        except TypeError as exc:
            raise ValueError(str(exc)) from exc
        return cls(**kwargs)


@dataclass(frozen=True, slots=True)
class WebhookRequest:
    """A raw inbound webhook delivery."""

    method: str
    path: str
    headers: Mapping[str, str]
    body: bytes
    client_ip: str | None = None


@dataclass(frozen=True, slots=True)
class WebhookResponse:
    """The HTTP status and JSON body the ingestor answers with."""

    status_code: int
    body: dict[str, Any]

    def as_tuple(self) -> tuple[int, dict[str, Any]]:
        """Return ``(status_code, body)`` for a WSGI/ASGI-style transport."""
        return (self.status_code, self.body)


@dataclass(frozen=True, slots=True)
class WebhookRefusal:
    """A typed refusal returned by an injected risk gate (never reaches the engine)."""

    stage: WebhookStage
    code: str
    message: str
    context: dict[str, Any] = field(default_factory=dict)


class TtlCache:
    """A bounded ``key -> (value, expiry)`` map driven by an injected clock.

    Every method takes ``now`` explicitly, so behaviour is deterministic and no
    wall clock enters the decision path.
    """

    def __init__(self, *, max_entries: int = 1024) -> None:
        self._max_entries = max_entries
        self._data: dict[str, tuple[Any, float]] = {}

    def _purge(self, now: float) -> None:
        expired = [key for key, (_, expiry) in self._data.items() if now >= expiry]
        for key in expired:
            del self._data[key]

    def check_and_set(self, key: str, value: Any, *, now: float, ttl_s: float) -> Any | None:
        """Return the live cached value (if any); otherwise store ``value`` and return ``None``."""
        self._purge(now)
        entry = self._data.get(key)
        if entry is not None and now < entry[1]:
            return entry[0]
        self._data[key] = (value, now + ttl_s)
        while len(self._data) > self._max_entries:
            del self._data[next(iter(self._data))]
        return None

    def get(self, key: str, *, now: float) -> Any | None:
        """Return the live value for ``key``, or ``None`` if absent or expired."""
        entry = self._data.get(key)
        if entry is None:
            return None
        if now >= entry[1]:
            del self._data[key]
            return None
        return entry[0]

    def __len__(self) -> int:
        return len(self._data)


class _WindowCounter:
    """A count-per-key limiter whose window resets once the injected clock passes it."""

    def __init__(self) -> None:
        self._data: dict[str, tuple[int, float]] = {}

    def increment(self, key: str, *, now: float, ttl_s: float) -> int:
        count, window_end = self._data.get(key, (0, now + ttl_s))
        if now >= window_end:
            count, window_end = 0, now + ttl_s
        count += 1
        self._data[key] = (count, window_end)
        return count


#: The one opaque refusal stages 1, 2 and 4 share, so the endpoint is not an oracle.
_OPAQUE_REFUSAL = WebhookResponse(401, {"status": "rejected"})

_ACCEPTED = WebhookResponse(202, {"status": "accepted"})


def _header(headers: Mapping[str, str], name: str) -> str | None:
    target = name.lower()
    for key, value in headers.items():
        if key.lower() == target:
            return value
    return None


def _extract_token(headers: Mapping[str, str]) -> str | None:
    auth = _header(headers, "authorization")
    if auth is not None:
        parts = auth.split(" ", 1)
        if len(parts) == 2 and parts[0].lower() == "bearer":
            token = parts[1].strip()
            if token:
                return token
    token = _header(headers, "x-webhook-token")
    if token is not None:
        token = token.strip()
        if token:
            return token
    return None


def _match_token(token: str | None, hashes: tuple[str, ...]) -> str | None:
    """Return the matched configured hash, or ``None``; constant-time, no early exit."""
    if not token or not token.strip():
        return None
    computed = "sha256:" + hashlib.sha256(token.encode("utf-8")).hexdigest()
    matched: str | None = None
    for expected in hashes:
        if hmac.compare_digest(computed, expected):
            matched = expected
    return matched


def _parse_instrument_id(value: Any) -> InstrumentId:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("instrument_id must be a non-empty string")
    text = value.strip()
    if "." in text:
        symbol, exchange = text.rsplit(".", 1)
        if not symbol or not exchange:
            raise ValueError(f"malformed instrument_id {value!r}")
        return InstrumentId(symbol, exchange)
    return InstrumentId(text)


def _parse_intent(body: bytes) -> OrderIntent:
    payload = json.loads(body)
    if not isinstance(payload, dict):
        raise TypeError("payload must be a JSON object")
    if "instrument_id" not in payload or "side" not in payload or "quantity" not in payload:
        raise ValueError("payload missing instrument_id/side/quantity")
    instrument_id = _parse_instrument_id(payload["instrument_id"])
    side = OrderSide(str(payload["side"]))
    quantity = payload["quantity"]
    if isinstance(quantity, bool) or not isinstance(quantity, (int, float)):
        raise TypeError("quantity must be a number")
    order_type = OrderType(str(payload["order_type"])) if payload.get("order_type") else None
    time_in_force = (
        TimeInForce(str(payload["time_in_force"])) if payload.get("time_in_force") else None
    )
    price = _optional_price(payload.get("price"))
    trigger_price = _optional_price(payload.get("trigger_price"))
    kwargs: dict[str, Any] = {
        "instrument_id": instrument_id,
        "side": side,
        "quantity": float(quantity),
    }
    if order_type is not None:
        kwargs["order_type"] = order_type
    if time_in_force is not None:
        kwargs["time_in_force"] = time_in_force
    if price is not None:
        kwargs["price"] = price
    if trigger_price is not None:
        kwargs["trigger_price"] = trigger_price
    return OrderIntent(**kwargs)


def _optional_price(value: Any) -> float | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise TypeError("price must be a number")
    return float(value)


class WebhookIngestor:
    """Runs the ADR 0026 ordered pipeline over a :class:`WebhookRequest`.

    ``risk_gate`` is a pure callable ``(OrderIntent) -> WebhookRefusal | None`` (the
    production binding is the ``honba.risk`` stage; tests inject a fake so the unit
    layer stays native-free). ``journal`` receives ``{"intent", "token_id"}`` for an
    accepted order. ``clock`` defaults to :func:`time.monotonic`; nothing else reads
    the wall clock.
    """

    def __init__(
        self,
        config: WebhookConfig,
        *,
        risk_gate: Callable[[OrderIntent], WebhookRefusal | None] | None = None,
        journal: Callable[[dict[str, Any]], None] | None = None,
        trading_state: str = "active",
        live_session: bool = False,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.config = config
        self.risk_gate = risk_gate
        self.journal = journal
        self.trading_state = trading_state
        self.live_session = live_session
        self._clock = clock
        self._idempotency = TtlCache()
        self._cool_off = _WindowCounter()

    def ingest(self, request: WebhookRequest) -> WebhookResponse:
        """Run the pipeline and return the terminal response."""
        # 1. kill switch (operator stop outranks everything)
        if not self.config.enabled or str(self.trading_state).lower() == "halted":
            return self._opaque(WebhookStage.KILL_SWITCH, "kill_switch", request)
        # 2. IP allowlist
        if self.config.ip_allowlist and request.client_ip not in self.config.ip_allowlist:
            return self._opaque(WebhookStage.IP_ALLOWLIST, "ip_not_allowed", request)
        # 3. size cap, before any parse
        if len(request.body) > self.config.max_body_bytes:
            return self._refuse(
                WebhookStage.SIZE,
                WebhookResponse(413, {"status": "rejected", "code": "body_too_large"}),
                "body_too_large",
                request,
                token_id=None,
            )
        # 4. token
        token = _extract_token(request.headers)
        matched = _match_token(token, self.config.token_hashes)
        if matched is None:
            return self._opaque(WebhookStage.TOKEN, "invalid_token", request)
        # 5. parse
        try:
            intent = _parse_intent(request.body)
        except (ValueError, TypeError, KeyError):
            return self._refuse(
                WebhookStage.PARSE,
                WebhookResponse(400, {"status": "rejected", "code": "malformed_payload"}),
                "malformed_payload",
                request,
                token_id=matched,
            )
        # 6. live gate
        if self.config.require_live and not self.live_session:
            return self._refuse(
                WebhookStage.LIVE_GATE,
                WebhookResponse(409, {"status": "rejected", "code": "not_live"}),
                "not_live",
                request,
                token_id=matched,
            )
        # 7. idempotency: a replayed key returns the terminal response of the first
        # delivery. It is never pre-cached before the risk stage, so a key that was
        # refused records the refusal, not an acceptance.
        idem_key = _header(request.headers, "idempotency-key")
        if idem_key:
            cached = self._idempotency.get(idem_key, now=self._clock())
            if isinstance(cached, WebhookResponse):
                self._audit(
                    stage=WebhookStage.IDEMPOTENCY,
                    decision="granted",
                    code="idempotent_replay",
                    token_id=matched,
                    client_ip=request.client_ip,
                )
                return cached
        # 8. cool-off
        cool_key = f"{matched}|{request.client_ip}"
        count = self._cool_off.increment(
            cool_key, now=self._clock(), ttl_s=self.config.cool_off_ttl_s
        )
        if count > self.config.cool_off_max:
            return self._refuse(
                WebhookStage.COOL_OFF,
                WebhookResponse(429, {"status": "rejected", "code": "cool_off"}),
                "cool_off",
                request,
                token_id=matched,
            )
        # 9. risk
        if self.risk_gate is not None:
            refusal = self.risk_gate(intent)
            if refusal is not None:
                response = WebhookResponse(
                    422,
                    {
                        "status": "rejected",
                        "code": refusal.code,
                        "stage": WebhookStage.RISK.value,
                        "message": refusal.message,
                    },
                )
                self._remember(idem_key, response)
                return self._refuse(
                    WebhookStage.RISK,
                    response,
                    refusal.code,
                    request,
                    token_id=matched,
                )
        # 10. journal
        if self.journal is not None:
            self.journal({"intent": intent, "token_id": matched})
        self._audit(
            stage=WebhookStage.JOURNAL,
            decision="granted",
            code="accepted",
            token_id=matched,
            client_ip=request.client_ip,
        )
        self._remember(idem_key, _ACCEPTED)
        return _ACCEPTED

    def _remember(self, idem_key: str | None, response: WebhookResponse) -> None:
        """Cache the terminal response for a replayed idempotency key."""
        if idem_key:
            self._idempotency.check_and_set(
                idem_key,
                response,
                now=self._clock(),
                ttl_s=self.config.idempotency_ttl_s,
            )

    def _opaque(self, stage: WebhookStage, code: str, request: WebhookRequest) -> WebhookResponse:
        self._audit(
            stage=stage,
            decision="denied",
            code=code,
            token_id=None,
            client_ip=request.client_ip,
        )
        return _OPAQUE_REFUSAL

    def _refuse(
        self,
        stage: WebhookStage,
        response: WebhookResponse,
        code: str,
        request: WebhookRequest,
        *,
        token_id: str | None,
    ) -> WebhookResponse:
        self._audit(
            stage=stage,
            decision="denied",
            code=code,
            token_id=token_id,
            client_ip=request.client_ip,
        )
        return response

    def _audit(
        self,
        *,
        stage: WebhookStage,
        decision: str,
        code: str,
        token_id: str | None,
        client_ip: str | None,
    ) -> None:
        log = audit_log()
        if not log.handlers:
            return
        entry = {
            "event": "webhook.decision",
            "stage": stage.value,
            "decision": decision,
            "code": code,
            "token_id": token_id,
            "client_ip": client_ip,
        }
        log.info(json.dumps(entry, separators=(",", ":")))
