"""Regenerates jesse_golden.json from Jesse's compiled kernel (jesse-rust==1.2.0, MIT).

Not run in CI. Usage (needs Python 3.12 + numpy + the jesse_rust wheel on the path):
    PYTHONPATH=<dir containing jesse_rust> python gen_jesse_golden.py > jesse_golden.json
Candles are Jesse-format columns [ts, open, close, high, low, volume].
"""
import json

import jesse_rust as j
import numpy as np

rng = np.random.default_rng(7)
n = 160
close = 100 + np.cumsum(rng.normal(0, 1.2, n))
open_ = np.r_[close[0], close[:-1]] + rng.normal(0, 0.2, n)
high = np.maximum(open_, close) + rng.random(n) * 1.5
low = np.minimum(open_, close) - rng.random(n) * 1.5
candles = np.column_stack([np.arange(n), open_, close, high, low, np.full(n, 1e5)]).astype(float)


def lst(a):
    return [None if np.isnan(x) else float(x) for x in np.asarray(a, float)]


cases = []
for p in (2, 5, 14, 50):
    cases.append({"kind": "sma", "params": {"period": p}, "out": {"value": lst(j.sma(close, p))}})
    cases.append({"kind": "ema", "params": {"period": p, "seed": "first"}, "out": {"value": lst(j.ema(close, p))}})
    cases.append({"kind": "rsi", "params": {"period": p}, "out": {"value": lst(j.rsi(close, p))}})
    cases.append({"kind": "atr", "params": {"period": p, "include_first_bar": True}, "out": {"value": lst(j.atr(candles, p))}})
for f, s, g in ((12, 26, 9), (5, 13, 4), (8, 21, 5)):
    m, sg, h = j.macd(close, f, s, g)
    cases.append({"kind": "macd", "params": {"fast": f, "slow": s, "signal": g, "seed": "first"},
                  "out": {"macd": lst(m), "signal": lst(sg), "histogram": lst(h)}})
for p, d in ((20, 2.0), (10, 1.5)):
    u, m, l = j.bollinger_bands(close, p, d, d)
    cases.append({"kind": "bollinger", "params": {"period": p, "mult": d},
                  "out": {"upper": lst(u), "middle": lst(m), "lower": lst(l)}})
for p in (10, 20):
    r = j.donchian(candles, p)
    cases.append({"kind": "donchian", "params": {"period": p},
                  "out": {"upper": lst(r["upperband"]), "lower": lst(r["lowerband"])}})
# Ichimoku kernel takes the last 80 candles and returns only the final values.
for args in ((9, 26, 52, 26), (5, 10, 20, 10), (9, 26, 52, 10)):
    r = j.ichimoku_cloud(candles[-80:], *args)
    cases.append({"kind": "ichimoku",
                  "params": {"tenkan": args[0], "kijun": args[1], "senkou_b": args[2], "displacement": args[3]},
                  "window": 80, "out": {"span_a": float(r[2]), "span_b": float(r[3])}})

print(json.dumps({"source": "jesse-rust 1.2.0", "high": lst(high), "low": lst(low), "close": lst(close), "cases": cases}))
