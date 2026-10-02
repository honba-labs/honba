"""Regenerates jesse_golden_volatility.json from Jesse's compiled kernel (jesse-rust 1.2.0, MIT).

Not run in CI. Usage (Python 3.12 + numpy + jesse_rust wheel on the path):
    PYTHONPATH=<dir containing jesse_rust> python gen_jesse_golden_volatility.py > jesse_golden_volatility.json
Parity verified for: bollinger_bandwidth (kernel is a ratio; x100 = percent), standard_deviation
(population), choppiness_index (kernel scalar=100). Others differ from TradingView or have no kernel.
"""

import json

import jesse_rust as j
import numpy as np

rng = np.random.default_rng(11)
n = 120
close = 100 + np.cumsum(rng.normal(0, 1.2, n))
open_ = np.r_[close[0], close[:-1]]
high = np.maximum(open_, close) + rng.random(n) * 1.5
low = np.minimum(open_, close) - rng.random(n) * 1.5
candles = np.column_stack([np.arange(n), open_, close, high, low, np.full(n, 1e5)]).astype(float)


def lst(a):
    return [None if np.isnan(x) else float(x) for x in np.asarray(a, float)]


cases = []
for p, m in ((20, 2.0), (10, 1.5)):
    cases.append(
        {
            "kind": "bollinger_bandwidth",
            "params": {"period": p, "mult": m},
            "out": {"value": lst(np.asarray(j.bollinger_bands_width(close, p, m)) * 100)},
        }
    )
for p in (2, 5, 20):
    cases.append(
        {
            "kind": "standard_deviation",
            "params": {"length": p},
            "out": {"value": lst(j.moving_std(close, p))},
        }
    )
for p in (5, 14, 30):
    cases.append(
        {
            "kind": "choppiness_index",
            "params": {"length": p},
            "out": {"value": lst(j.chop(candles, p, 100.0, 1))},
        }
    )
print(
    json.dumps(
        {"high": high.tolist(), "low": low.tolist(), "close": close.tolist(), "cases": cases}
    )
)
