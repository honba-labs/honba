"""Every registered indicator must honour the Honba Indicator contract."""

import json
import math

import pytest

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies.indicators import (
    FAMILIES,
    Indicator,
    build_indicator,
    indicator_family,
    indicator_spec,
    list_indicators,
)

KINDS = list_indicators()
IID = InstrumentId("NIFTY50", "NSE")
IST = 19_800_000_000_000  # +05:30 in ns
DAY = 86_400_000_000_000


def synth(n=400):
    """Deterministic OHLCV bars, one per hour from 09:15 IST, plus extra named inputs."""
    bars, closes = [], []
    price = 100.0
    for i in range(n):
        price *= 1 + 0.01 * math.sin(i / 7) + 0.003 * math.cos(i / 3)
        o = price * (1 + 0.002 * math.sin(i))
        hi, lo = max(o, price) * 1.004, min(o, price) * 0.996
        ts = (
            (i // 6) * DAY
            + (i % 6) * 3_600_000_000_000
            + 3_600_000_000_000 * 3
            + 900_000_000_000
            - IST
        )
        bars.append(Bar(IID, ts, o, hi, lo, price, 1000.0 + 50 * (i % 11)))
        closes.append(price)
    extra = {
        "advances": [30 + (i * 7) % 20 for i in range(n)],
        "declines": [20 + (i * 5) % 25 for i in range(n)],
        "adv_volume": [1e6 + 1e4 * ((i * 3) % 17) for i in range(n)],
        "dec_volume": [9e5 + 1e4 * ((i * 5) % 13) for i in range(n)],
        "benchmark": [c * (1 + 0.02 * math.sin(i / 5)) for i, c in enumerate(closes)],
        "put_volume": [800 + (i * 11) % 90 for i in range(n)],
        "call_volume": [900 + (i * 13) % 70 for i in range(n)],
    }
    return bars, extra


def values(bars, extra, inputs, i):
    return [getattr(bars[i], f) if hasattr(bars[i], f) else extra[f][i] for f in inputs]


def flat(out):
    if out is None:
        return None
    if isinstance(out, (int, float)):
        return (float(out),)
    if hasattr(out, "__dataclass_fields__"):
        return tuple(float(getattr(out, f)) for f in out.__dataclass_fields__)
    return tuple(float(x) for x in out)


def run(kind, bars, extra):
    ind = build_indicator(kind)
    return ind, [flat(ind.update(*values(bars, extra, ind.inputs, i))) for i in range(len(bars))]


def test_families_are_declared_and_registered_kinds_belong_to_one():
    assert len(FAMILIES) == 8
    assert KINDS and all(indicator_family(k) in FAMILIES for k in KINDS)


@pytest.mark.parametrize("kind", KINDS)
def test_contract_metadata_and_spec(kind):
    ind = build_indicator(kind)
    assert isinstance(ind, Indicator)
    assert ind.kind == kind and ind.family == indicator_family(kind)
    assert ind.inputs and ind.outputs
    spec = indicator_spec(kind)
    json.dumps(spec)  # must be JSON-serialisable for configs / MCP discovery
    assert spec["kind"] == kind and spec["family"] == ind.family
    assert spec["inputs"] == list(ind.inputs) and spec["outputs"] == list(ind.outputs)
    defaults = {p["name"]: p["default"] for p in spec["params"] if "default" in p}
    build_indicator(kind, **defaults)  # defaults round-trip


@pytest.mark.parametrize("kind", KINDS)
def test_warmup_matches_first_value(kind):
    bars, extra = synth()
    ind, outs = run(kind, bars, extra)
    first = next((i for i, o in enumerate(outs) if o is not None), None)
    assert first is not None, f"{kind} never produced a value on 400 bars"
    assert all(o is not None for o in outs[first:]), f"{kind} returned None after warming up"
    assert all(len(o) == len(ind.outputs) for o in outs[first:] if o), (
        "output arity != declared outputs"
    )
    if ind.warmup is not None:  # session/data-dependent indicators may declare None
        assert first == ind.warmup - 1, (
            f"{kind}: warmup={ind.warmup} but first value at bar {first + 1}"
        )


@pytest.mark.parametrize("kind", KINDS)
def test_reset_restores_fresh_state(kind):
    bars, extra = synth(250)
    ind, first = run(kind, bars, extra)
    ind.reset()
    again = [flat(ind.update(*values(bars, extra, ind.inputs, i))) for i in range(len(bars))]
    assert again == first


@pytest.mark.parametrize("kind", KINDS)
def test_update_bar_matches_update(kind):
    bars, _extra = synth(250)
    ind = build_indicator(kind)
    if not all(hasattr(bars[0], f) for f in ind.inputs):
        pytest.skip("indicator needs non-bar inputs")
    a, b = build_indicator(kind), build_indicator(kind)
    for bar in bars:
        assert flat(a.update_bar(bar)) == flat(b.update(*[getattr(bar, f) for f in b.inputs]))


@pytest.mark.parametrize("kind", KINDS)
def test_outputs_are_finite_numbers(kind):
    bars, extra = synth()
    _, outs = run(kind, bars, extra)
    assert all(math.isfinite(x) for o in outs if o for x in o)


def test_unknown_kind_and_bad_family():
    with pytest.raises(ValueError):
        build_indicator("nope")
    with pytest.raises(ValueError):
        list_indicators("astrology")


def test_explicit_none_warmup_is_data_dependent_but_omitting_is_an_error():
    from honba.strategies.indicators import _base

    @_base.indicator("tmp_session", "volume", warmup=None)
    class Tmp(Indicator):
        def update(self, x):
            return x

    @_base.indicator("tmp_missing", "volume")
    class Missing(Indicator):
        def update(self, x):
            return x

    try:
        assert Tmp().warmup is None
        with pytest.raises(NotImplementedError):
            Missing().warmup  # noqa: B018  (property access must raise)
    finally:
        _base._REGISTRY.pop("tmp_session", None)
        _base._REGISTRY.pop("tmp_missing", None)
