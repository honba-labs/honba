"""Regenerates jesse_golden_moving_average_trend.json from Jesse's compiled kernel (jesse-rust==1.2.0, MIT).

Not run in CI. Usage (Python 3.12 + numpy + the jesse_rust wheel on the path):
    PYTHONPATH=<dir containing jesse_rust> python gen_jesse_golden_moving_average_trend.py > jesse_golden_moving_average_trend.json
Only kinds whose definition matches Honba's (hma, vwma, kama, mcginley_dynamic, lsma, supertrend,
vortex) are included. dema/tema (Jesse seeds EMAs with the first value), adx, aroon, parabolic_sar
differ from TradingView/Wilder in seeding/window and are covered by hand-checked values instead.
Candles are Jesse-format columns [ts, open, close, high, low, volume].
"""
import json

import jesse_rust as j
import numpy as np

rng = np.random.default_rng(11)
n = 160
close = 100 + np.cumsum(rng.normal(0, 1.2, n))
open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.2, n)
high = np.maximum(open_, close) + rng.random(n) * 1.5
low = np.minimum(open_, close) - rng.random(n) * 1.5
vol = 1e3 + rng.random(n) * 500
candles = np.column_stack([np.arange(n), open_, close, high, low, vol]).astype(float)


def lst(a):
    return [None if np.isnan(x) else float(x) for x in np.asarray(a, float)]


cases = []
for p in (4, 9, 16):
    cases.append({"kind": "hma", "params": {"period": p}, "out": {"value": lst(j.hma(close, p))}})
for p in (5, 20):
    cases.append({"kind": "vwma", "params": {"period": p}, "out": {"value": lst(j.vwma(candles, p))}})
for p, f, s in ((10, 2, 30), (5, 3, 20)):
    cases.append({"kind": "kama", "params": {"period": p, "fast": f, "slow": s}, "out": {"value": lst(j.kama(close, p, f, s))}})
for p, k in ((14, 0.6), (5, 0.4)):
    cases.append({"kind": "mcginley_dynamic", "params": {"period": p, "k": k}, "out": {"value": lst(j.mcginley_dynamic(close, p, k))}})
for p in (5, 25):
    cases.append({"kind": "lsma", "params": {"period": p}, "out": {"value": lst(j.linearreg(close, p))}})
for p, f in ((10, 3.0), (7, 2.0)):
    st = j.supertrend(candles, p, f)[0]
    cases.append({"kind": "supertrend", "params": {"atr_period": p, "factor": f}, "out": {"value": lst(st)}})
for p in (14, 7):
    plus, minus = j.vi(candles, p, True)
    cases.append({"kind": "vortex", "params": {"period": p}, "out": {"plus": lst(plus), "minus": lst(minus)}})

print(json.dumps({"source": "jesse-rust 1.2.0", "high": lst(high), "low": lst(low), "close": lst(close),
                  "volume": lst(vol), "cases": cases}))
