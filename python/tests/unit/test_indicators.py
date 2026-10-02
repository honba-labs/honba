"""Streaming indicators: hand-checked values and warm-up behaviour."""

import math

import pytest

from honba.strategies.indicators import Atr, Bollinger, Donchian, Ema, Ichimoku, Kdj, Macd, Rsi, Sma


def feed(ind, values):
    return [ind.update(v) for v in values]


def test_sma_warmup_and_value():
    out = feed(Sma(3), [1, 2, 3, 4])
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)
    assert out[3] == pytest.approx(3.0)


def test_ema_seeds_with_sma_then_smooths():
    out = feed(Ema(3), [1, 2, 3, 4])
    assert out[:2] == [None, None]
    assert out[2] == pytest.approx(2.0)  # SMA seed
    assert out[3] == pytest.approx(3.0)  # 4*0.5 + 2*0.5, alpha = 2/(3+1)


def test_rsi_flat_series_is_100_like_jesse():
    assert feed(Rsi(3), [100.0] * 6)[-1] == pytest.approx(100.0)


def test_rsi_all_gains_is_100_and_all_losses_is_0():
    assert feed(Rsi(3), [1, 2, 3, 4, 5])[-1] == pytest.approx(100.0)
    assert feed(Rsi(3), [5, 4, 3, 2, 1])[-1] == pytest.approx(0.0)


def test_rsi_needs_period_plus_one_values():
    out = feed(Rsi(3), [1, 2, 1, 2])
    assert out[:3] == [None, None, None]
    assert out[3] is not None


def test_macd_line_is_fast_minus_slow_ema():
    m = Macd(3, 6, 3)
    vals = [float(x) for x in range(1, 20)]
    last = feed(m, vals)[-1]
    fast, slow = Ema(3), Ema(6)
    f = feed(fast, vals)[-1]
    s = feed(slow, vals)[-1]
    assert last.macd == pytest.approx(f - s)
    assert last.histogram == pytest.approx(last.macd - last.signal)


def test_bollinger_bands_symmetric_about_mean():
    out = feed(Bollinger(3, 2.0), [1, 2, 3])[-1]
    assert out.middle == pytest.approx(2.0)
    sd = math.sqrt(2 / 3)
    assert out.upper == pytest.approx(2 + 2 * sd)
    assert out.lower == pytest.approx(2 - 2 * sd)


def test_donchian_window_includes_current_bar():
    d = Donchian(3)
    # feed (high, low)
    outs = [d.update(h, l) for h, l in [(5, 1), (6, 2), (7, 3), (9, 4)]]
    assert outs[2].upper == 7 and outs[2].lower == 1  # window includes current
    assert outs[3].upper == 9 and outs[3].lower == 2


def test_invalid_period_rejected():
    for cls in (Sma, Ema, Rsi):
        with pytest.raises(ValueError):
            cls(0)


def test_atr_wilder_smoothing():
    a = Atr(2)
    assert a.update(10, 8, 9) is None  # no previous close, no true range
    assert a.update(11, 9, 10) is None  # first TR (2), still warming
    assert a.update(12, 9, 11) == pytest.approx(2.5)  # mean of TRs 2 and 3
    assert a.update(12, 10, 11) == pytest.approx(2.25)  # (2.5 * 1 + 2) / 2


def test_kdj_with_unit_smoothing_equals_rsv():
    k = Kdj(2, 1, 1)
    assert k.update(5, 1, 3) is None
    assert k.update(6, 2, 5) == pytest.approx((80.0, 80.0, 80.0))  # (5-1)/(6-1)


def test_kdj_flat_window_gives_zero_rsv():
    k = Kdj(2, 1, 1)
    k.update(5, 5, 5)
    assert k.update(5, 5, 5) == pytest.approx((0.0, 0.0, 0.0))


def test_ichimoku_cloud_is_displaced():
    c = Ichimoku(1, 1, 1, displacement=2)  # cloud plotted displacement-1 bars back
    assert c.update(4, 2) is None
    assert c.update(8, 6) == pytest.approx((3.0, 3.0))  # spans from one bar ago
    assert c.update(10, 8) == pytest.approx((7.0, 7.0))
