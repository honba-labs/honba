"""Unit tests for the webhook ingestion pipeline (ADR 0026, E5-S6).

Native-free: this module never imports ``honba._honba``. Trading state is a plain
string and the risk gate / journal are injected fakes, so the whole stage order,
token handling, idempotency and cool-off behaviour is exercised deterministically
under a fake clock.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

import pytest

from honba.api.webhooks import (
    TtlCache,
    WebhookConfig,
    WebhookIngestor,
    WebhookRefusal,
    WebhookRequest,
    WebhookResponse,
    WebhookStage,
)

_TOKEN = b"secret"
_TOKEN_HASH = "sha256:" + hashlib.sha256(_TOKEN).hexdigest()
_VALID_BODY = json.dumps(
    {"instrument_id": "NIFTY50.NSE", "side": "buy", "quantity": 10, "order_type": "market"}
).encode()
_OPAQUE = WebhookResponse(401, {"status": "rejected"})


class FakeClock:
    """A deterministic monotonic clock; advance it explicitly."""

    def __init__(self, start: float = 0.0) -> None:
        self._now = start

    def __call__(self) -> float:
        return self._now

    def advance(self, delta: float) -> None:
        self._now += delta


def _config(**overrides: Any) -> WebhookConfig:
    kwargs: dict[str, Any] = {"token_hashes": (_TOKEN_HASH,)}
    kwargs.update(overrides)
    return WebhookConfig(**kwargs)


def _request(
    *,
    body: bytes | None = None,
    headers: dict[str, str] | None = None,
    auth: str | None = "Bearer secret",
    client_ip: str | None = "10.0.0.1",
    method: str = "POST",
    path: str = "/webhooks/broker",
) -> WebhookRequest:
    merged: dict[str, str] = {}
    if auth is not None:
        merged["Authorization"] = auth
    if headers:
        merged.update(headers)
    return WebhookRequest(
        method=method,
        path=path,
        headers=merged,
        body=_VALID_BODY if body is None else body,
        client_ip=client_ip,
    )


def test_stage_order_blocks_before_parse() -> None:
    ingestor = WebhookIngestor(_config(max_body_bytes=8), trading_state="halted", clock=FakeClock())
    response = ingestor.ingest(_request(body=b"{ this is not json and it is far too long }"))
    assert response.status_code == 401
    assert response.body == {"status": "rejected"}


def test_size_cap_rejects_oversize_body() -> None:
    ingestor = WebhookIngestor(_config(max_body_bytes=8), clock=FakeClock())
    response = ingestor.ingest(_request(body=b"x" * 9))
    assert response.status_code == 413
    assert response.body["code"] == "body_too_large"


def test_unknown_and_malformed_tokens_are_indistinguishable() -> None:
    ingestor = WebhookIngestor(_config(), clock=FakeClock())
    unknown = ingestor.ingest(_request(headers={"Authorization": "Bearer nope"}))
    malformed = ingestor.ingest(_request(headers={"Authorization": "Bearer not-a-hash"}))
    wrong = ingestor.ingest(_request(headers={"Authorization": "Bearer " + "a" * 64}))
    headerless = ingestor.ingest(
        _request(auth=None, headers={"X-Webhook-Token": "still-not-a-hash"})
    )
    assert unknown == malformed == wrong == headerless
    assert unknown == _OPAQUE


def test_ip_allowlist_refuses_unlisted_client() -> None:
    ingestor = WebhookIngestor(_config(ip_allowlist=("10.0.0.1",)), clock=FakeClock())
    response = ingestor.ingest(_request(client_ip="10.0.0.9"))
    assert response == _OPAQUE


def test_hashed_token_never_compared_in_plaintext() -> None:
    with pytest.raises(ValueError):
        WebhookConfig(token_hashes=("secret",))
    with pytest.raises(ValueError):
        WebhookConfig(token_hashes=("deadbeef",))
    with pytest.raises(ValueError):
        WebhookConfig(token_hashes=("sha256:" + "z" * 64,))
    ingestor = WebhookIngestor(_config(), clock=FakeClock())
    assert ingestor.ingest(_request()).status_code == 202
    wrong = ingestor.ingest(_request(headers={"Authorization": "Bearer secret2"}))
    assert wrong == _OPAQUE


def test_idempotent_replay_returns_cached_response() -> None:
    journaled: list[dict[str, Any]] = []
    ingestor = WebhookIngestor(_config(), journal=journaled.append, clock=FakeClock())
    headers = {"Idempotency-Key": "abc"}
    first = ingestor.ingest(_request(headers=headers))
    second = ingestor.ingest(_request(headers=headers))
    assert first == second
    assert first.status_code == 202
    assert len(journaled) == 1


def test_idempotency_caches_the_refusal_not_a_premature_accept() -> None:
    calls: list[Any] = []
    journaled: list[dict[str, Any]] = []

    def gate(intent: Any) -> WebhookRefusal:
        calls.append(intent)
        return WebhookRefusal(WebhookStage.RISK, "max_notional", "over limit")

    ingestor = WebhookIngestor(
        _config(), risk_gate=gate, journal=journaled.append, clock=FakeClock()
    )
    headers = {"Idempotency-Key": "k"}
    first = ingestor.ingest(_request(headers=headers))
    second = ingestor.ingest(_request(headers=headers))
    assert first.status_code == 422
    assert second == first
    assert len(calls) == 1
    assert journaled == []


def test_cool_off_limits_repeat_deliveries() -> None:
    ingestor = WebhookIngestor(_config(cool_off_max=2), journal=lambda _: None, clock=FakeClock())
    statuses = [ingestor.ingest(_request()).status_code for _ in range(3)]
    assert statuses[:2] == [202, 202]
    assert statuses[2] == 429
    assert ingestor.ingest(_request()).body["code"] == "cool_off"


def test_ttl_expiry_is_deterministic_under_a_fake_clock() -> None:
    cache = TtlCache()
    assert cache.check_and_set("k", "v", now=0.0, ttl_s=5.0) is None
    assert cache.check_and_set("k", "v2", now=4.9, ttl_s=5.0) == "v"
    assert cache.check_and_set("k", "v3", now=5.0, ttl_s=5.0) is None
    assert cache.get("k", now=5.0) == "v3"

    expiring = TtlCache()
    assert expiring.check_and_set("e", "v", now=0.0, ttl_s=1.0) is None
    assert len(expiring) == 1
    assert expiring.get("e", now=1.0) is None
    assert len(expiring) == 0

    clock = FakeClock()
    journaled: list[dict[str, Any]] = []
    ingestor = WebhookIngestor(
        _config(idempotency_ttl_s=5.0), journal=journaled.append, clock=clock
    )
    headers = {"Idempotency-Key": "replay"}
    assert ingestor.ingest(_request(headers=headers)).status_code == 202
    assert ingestor.ingest(_request(headers=headers)).status_code == 202
    assert len(journaled) == 1
    clock.advance(6.0)
    assert ingestor.ingest(_request(headers=headers)).status_code == 202
    assert len(journaled) == 2
