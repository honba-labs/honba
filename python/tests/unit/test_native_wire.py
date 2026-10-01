"""Unit tests for the wire-contract surface of ``honba._honba`` (ADR 006)."""
from __future__ import annotations

import json

import pytest

_honba = pytest.importorskip("honba._honba")


def test_schema_version_is_exported():
    assert _honba.SCHEMA_VERSION == 1


def test_canonical_json_parses_and_reserializes_with_rust():
    payload = {"symbol": "RELIANCE", "venue": "NSE"}
    out = _honba.canonical_json("InstrumentId", json.dumps(payload))
    assert json.loads(out) == payload


def test_canonical_json_rejects_unknown_kind():
    with pytest.raises(ValueError, match="unknown kind"):
        _honba.canonical_json("Nope", "{}")


def test_canonical_json_rejects_invalid_payload():
    bad = {"schema_version": 99, "event": {"type": "order_cancelled", "order_id": "O",
                                           "ts_event": 1}, "ts_init": 1}
    with pytest.raises(ValueError, match="schema_version"):
        _honba.canonical_json("Message", json.dumps(bad))


def test_order_intent_stop_constructors():
    i = _honba.OrderIntent.stop_buy("NIFTY50", 75.0, 22050.0)
    assert (i.order_type, i.price, i.trigger_price) == ("stop_market", None, 22050.0)
    i = _honba.OrderIntent.stop_sell("NIFTY50", 75.0, 21950.0)
    assert (i.side, i.trigger_price) == ("sell", 21950.0)
    i = _honba.OrderIntent.stop_limit_buy("NIFTY50", 75.0, 22000.0, 22010.0)
    assert (i.order_type, i.trigger_price, i.price) == ("stop_limit", 22000.0, 22010.0)
    i = _honba.OrderIntent.stop_limit_sell("NIFTY50", 75.0, 21950.0, 21940.0)
    assert (i.side, i.trigger_price, i.price) == ("sell", 21950.0, 21940.0)


def test_order_intent_legacy_stop_name_normalizes():
    i = _honba.OrderIntent("NIFTY50", "buy", 1.0, "stop", trigger_price=100.0)
    assert i.order_type == "stop_market"


@pytest.mark.parametrize(
    "kwargs",
    [
        {"order_type": "limit"},
        {"order_type": "stop_market"},
        {"order_type": "stop_limit", "price": 1.0},
        {"order_type": "stop_market", "price": 1.0, "trigger_price": 1.0},
        {"order_type": "market", "price": 1.0},
    ],
)
def test_order_intent_rejects_wrong_price_fields(kwargs):
    with pytest.raises(ValueError):
        _honba.OrderIntent("NIFTY50", "buy", 1.0, **kwargs)


def test_order_intent_accepts_fok_and_gtd():
    for tif in ("fok", "gtd"):
        assert _honba.OrderIntent("X", "buy", 1.0, time_in_force=tif).time_in_force == tif
