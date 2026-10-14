"""Integration tests for webhook order ingestion (ADR 0026, E5-S6).

These drive a real :class:`~honba.api.webhooks.WebhookIngestor` end to end — through
the token, idempotency, cool-off and risk stages — and read back the shared
``honba.api.auth`` NDJSON audit log. No native symbol is required; ``importorskip``
keeps them clean on an unbuilt checkout.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

webhooks = pytest.importorskip("honba.api.webhooks")
auth = pytest.importorskip("honba.api.auth")

_TOKEN = b"secret"
_TOKEN_HASH = "sha256:" + hashlib.sha256(_TOKEN).hexdigest()
_BODY = json.dumps({"instrument_id": "NIFTY50.NSE", "side": "buy", "quantity": 5}).encode()


def _request() -> webhooks.WebhookRequest:
    return webhooks.WebhookRequest(
        method="POST",
        path="/webhooks/broker",
        headers={"Authorization": "Bearer secret"},
        body=_BODY,
        client_ip="10.0.0.1",
    )


def _audit_entries(path: Path) -> list[dict]:
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line]


def test_webhook_order_passes_risk_and_journals(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.ndjson"
    auth.configure_audit_log(audit_path)
    journaled: list[dict] = []
    ingestor = webhooks.WebhookIngestor(
        webhooks.WebhookConfig(token_hashes=(_TOKEN_HASH,)),
        risk_gate=lambda intent: None,
        journal=journaled.append,
    )

    response = ingestor.ingest(_request())

    assert response.as_tuple() == (202, {"status": "accepted"})
    assert len(journaled) == 1
    assert journaled[0]["intent"].instrument_id.symbol == "NIFTY50"
    assert journaled[0]["token_id"] == _TOKEN_HASH

    granted = [
        entry
        for entry in _audit_entries(audit_path)
        if entry.get("event") == "webhook.decision" and entry.get("decision") == "granted"
    ]
    assert granted
    assert granted[-1]["stage"] == "journal"
    assert granted[-1]["token_id"] == _TOKEN_HASH


def test_webhook_risk_refusal_is_audited(tmp_path: Path) -> None:
    audit_path = tmp_path / "audit.ndjson"
    auth.configure_audit_log(audit_path)
    refusal = webhooks.WebhookRefusal(
        webhooks.WebhookStage.RISK, "max_notional", "order notional over limit"
    )
    ingestor = webhooks.WebhookIngestor(
        webhooks.WebhookConfig(token_hashes=(_TOKEN_HASH,)),
        risk_gate=lambda intent: refusal,
        journal=lambda _: pytest.fail("a refused order must not be journaled"),
    )

    response = ingestor.ingest(_request())

    assert response.status_code == 422
    assert response.body["code"] == "max_notional"
    assert response.body["stage"] == "risk"

    denied = [
        entry
        for entry in _audit_entries(audit_path)
        if entry.get("event") == "webhook.decision" and entry.get("decision") == "denied"
    ]
    assert any(entry.get("code") == "max_notional" for entry in denied)
