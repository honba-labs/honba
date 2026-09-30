"""Regenerates jesse_golden_momentum.json from Jesse's compiled kernel (jesse-rust==1.2.0, MIT).

Not run in CI. Usage (needs Python 3.12 + numpy + the jesse_rust wheel on the path):
    PYTHONPATH=<dir containing jesse_rust> python gen_jesse_golden_momentum.py > jesse_golden_momentum.json
Only kernels verified to equal the TradingView/textbook definition are included:
stoch (SMA smoothing), willr, cci (mean absolute deviation, 0.015), cmo (simple sums).
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
candles = np.column_stack([np.arange(n), open_, close, high, low, np.full(n, 1e5)]).astype(float)


def lst(a):
    return [None if np.isnan(x) else float(x) for x in np.asarray(a, float)]


cases = []
for k, ks, ds in ((14, 1, 3), (14, 3, 3), (5, 3, 2)):
    kk, dd = j.stoch(candles, k, ks, 0, ds, 0)
    cases.append({"kind": "stochastic", "params": {"k_length": k, "k_smooth": ks, "d_smooth": ds},
                  "out": {"k": lst(kk), "d": lst(dd)}})
for p in (5, 14, 30):
    cases.append({"kind": "williams_r", "params": {"length": p}, "out": {"value": lst(j.willr(candles, p))}})
for p in (10, 20):
    cases.append({"kind": "cci", "params": {"length": p}, "out": {"value": lst(j.cci(candles, p))}})
for p in (5, 9, 20):
    cases.append({"kind": "cmo", "params": {"length": p}, "out": {"value": lst(j.cmo(close, p))}})

print(json.dumps({"source": "jesse-rust 1.2.0", "high": lst(high), "low": lst(low), "close": lst(close), "cases": cases}))
