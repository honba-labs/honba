"""O(1)-per-update rolling-window helpers shared by the indicator families."""

from __future__ import annotations

import math
from collections import deque
from typing import NamedTuple

from honba.strategies.indicators._util import check as _check

_RECOMPUTE_MIN = 1000
_DROP = 1e-4  # recompute when m2 falls below this fraction of its peak since the last rebuild
_HUGE = 1e150  # |x| above this can overflow squares/sums (1e150**2 * n stays < 1.8e308)
_SHIFT_K2 = 1e3  # re-shift when (x - shift)**2 exceeds this multiple of the window variance
_CANCEL = 1e-4  # rebuild when a running total falls below this fraction of its running peak
_NAN = float("nan")
_new = tuple.__new__  # builds a NamedTuple without its Python-level __new__ frame
_NEAR = 1e-6  # RollingLinReg: rebuild when sse falls below this fraction of m2 (near-perfect fit)
_INF = float("inf")
_BURST = 4  # guard-rebuild credit cap, in units of one rebuild (see RollingPairMoments)


class RollingSum:
    """Sum over the last ``period`` values (``None`` until the window is full).

    A non-finite value in the window makes the sum NaN until it has left the window; the running
    total is then rebuilt exactly, so a NaN/inf never poisons later results. Overflow follows the
    ``RollingMoments`` convention: a value with ``|x| > 1e150`` is treated like a NaN/inf (the
    sum is reported as NaN while it is in the window, even if the exact sum is representable) and
    the total is rebuilt exactly, immediately, once the last such value has left. A running total
    that is nevertheless non-finite while the window is finite is rebuilt at once.

    The total is also rebuilt every ``max(1000, 4 * period)`` updates to bound rounding drift, and
    whenever it falls below ``1e-4`` of the largest magnitude it has had since the last rebuild
    (a big spike leaves rounding residue ~``spike * eps``, which would swamp a small true sum;
    the peak is tracked during warmup too). These guard rebuilds are rate-limited to one per
    ``max(1, period // 2)`` updates, so adversarial input cannot force ``O(period)`` work on
    more than ~``2 / period`` of the updates. This keeps the relative error near ``1e-10``.
    """

    def __init__(self, period: int) -> None:
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)
        self._sum = 0.0
        self._mag = 0.0  # max |sum| since the last rebuild (warmup included)
        self._bad = 0  # unsafe (non-finite or huge) values currently in the window
        self._dirty = False  # running state not trustworthy; rebuild once the window is safe
        self._since = 0  # updates since the last rebuild
        self._every = max(_RECOMPUTE_MIN, 4 * period)
        self._gap = max(1, period // 2)  # minimum updates between cancellation-guard rebuilds

    def _rebuild(self) -> None:
        self._sum = math.fsum(self._w)
        self._mag = abs(self._sum)
        self._dirty = False
        self._since = 0

    def update(self, x: float) -> float | None:
        w = self._w
        full = len(w) == self.period
        if full and _unsafe(w[0]):
            self._bad -= 1
        if _unsafe(x):
            self._bad += 1
        old = w[0] if full else 0.0
        w.append(x)
        if self._bad:
            self._dirty = True
        elif self._dirty or self._since >= self._every:
            self._rebuild()
        else:
            self._sum += x - old if full else x
            self._since += 1
            a = abs(self._sum)
            if not math.isfinite(a) or (a < self._mag * _CANCEL and self._since >= self._gap):
                self._rebuild()
            elif a > self._mag:
                self._mag = a
        if len(w) < self.period:
            return None
        return _NAN if self._bad else self._sum


def _unsafe(v: float) -> bool:
    """True for NaN/inf and for magnitudes whose squares could overflow."""
    return not abs(v) <= _HUGE


class Moments(NamedTuple):
    mean: float
    variance: float
    std: float


_NAN_RAW3 = (_NAN, _NAN, _NAN)
_NAN_MOMENTS = Moments(_NAN, _NAN, _NAN)


class RollingMoments:
    """Mean / variance / std over the last ``period`` values, O(1) amortised per update.

    Uses Welford's algorithm with removal (replace-oldest form), which does not suffer the
    catastrophic cancellation of a raw sum / sum-of-squares at high price levels. Exact rebuilds
    (O(period)) happen in three cases, all amortised O(1) on ordinary data:

    * every ``max(1000, 4 * period)`` updates (bounds ordinary rounding drift);
    * when the last non-finite value leaves the window. While a NaN/inf is in the window all
      moments are NaN and the running state is left untouched, so it cannot be poisoned;
    * when the newest value has drifted more than ``sqrt(1e3)`` (~32) window standard deviations
      from the shift (smooth trends: the shift is fixed at each rebuild, so ``x - shift`` would
      otherwise grow while ``m2`` stays small and relative precision would erode); the state is
      rebuilt around the current window mean. Measured on a 20000-bar random walk (rebuilds per
      update, period-dependent): ~7.6% at p=2, 2.2% at p=3, 0.83% at p=5, 0.25% at p=10 and
      0.18% at p>=20 (so <= 0.2% only for period >= ~20; the O(period) cost stays small for
      small periods); a ramp rebuilds once per ``~32 * std / slope`` updates;
    * when ``m2`` falls below ``1e-4`` of its peak since the last rebuild. A large outlier leaves
      a rounding residue of ~``outlier**2 * eps`` in ``m2``; once the outlier has left this
      residue would dominate the true (small) ``m2``, so the state is rebuilt exactly. Keeping
      ``m2 >= 1e-4 * peak`` bounds the residue's relative error at ~``1e-12``.

    Overflow: a value with ``|x| > 1e150`` (its square, summed, could exceed the float range) is
    treated like a NaN/inf: every moment (mean, variance, std) is reported as NaN while such a
    value is in the window, even when the exact result would have been representable, and the
    state is rebuilt exactly (from the window, with ``fsum``) once the last such value has left.
    A windowful of constant huge values is likewise NaN. Recovery is therefore immediate, not
    left to the periodic rebuild, and costs no ``O(period)`` work while the value is inside.

    A window of identical values reports variance exactly 0. ``ddof`` follows numpy
    (0 = population).

    ``update_raw`` is the hot path (returns a plain ``(mean, variance, std)`` tuple; a full,
    finite window takes one inlined branch of a few arithmetic operations); ``update`` wraps it
    in a :class:`Moments`.
    """

    def __init__(self, period: int, ddof: int = 0) -> None:
        self.period = _check(period)
        if ddof < 0 or period - ddof < 1:
            raise ValueError(
                f"ddof must satisfy 0 <= ddof < period, got ddof={ddof}, period={period}"
            )
        self.ddof = ddof
        self._w: deque[float] = deque(maxlen=period)
        self._shift = 0.0
        self._mean = 0.0  # mean of (value - shift)
        self._m2 = 0.0
        self._peak = 0.0  # max m2 since the last rebuild
        self._run = 0  # consecutive identical trailing values
        self._bad = 0  # non-finite values currently in the window
        self._dirty = False  # state not trustworthy; rebuild once the window is finite
        self._ok = False  # window full, all values safe, state clean (fast path allowed)
        self._tick = 0  # sliding updates so far
        self._every = max(_RECOMPUTE_MIN, 4 * period)
        self._due = self._every  # tick of the next periodic rebuild
        self._d = period - ddof
        self._kn = _SHIFT_K2 / period
        if period == 2:  # closed form, stateless but for the previous value
            self._prev: float | None = None
            self.update_raw = self._raw2  # type: ignore[method-assign]

    def _raw2(self, x: float) -> tuple[float, float, float] | None:
        """Period 2: mean ``a + d/2``, ``m2 = d**2 / 2`` (exact-ish, no state, no rebuilds)."""
        a, self._prev = self._prev, x
        if a is None:
            return None
        if -_HUGE <= x <= _HUGE and -_HUGE <= a <= _HUGE:
            d = x - a
            var = d * d / 2.0 / self._d
            return (a + d / 2.0, var, math.sqrt(var))
        return _NAN_RAW3

    def _recompute(self) -> None:
        n = len(self._w)
        self._shift = math.fsum(self._w) / n
        sh = self._shift
        self._mean = math.fsum(v - sh for v in self._w) / n  # ~0, kept for exactness
        m = self._mean
        self._m2 = math.fsum((v - sh - m) ** 2 for v in self._w)
        self._peak = self._m2
        self._due = self._tick + self._every
        self._dirty = False

    def update(self, x: float) -> Moments | None:
        r = self.update_raw(x)
        return None if r is None else _new(Moments, r)

    def update_raw(self, x: float) -> tuple[float, float, float] | None:
        """``(mean, variance, std)`` as a plain tuple (``None`` during warm-up)."""
        w, n = self._w, self.period
        if self._ok and len(w) == n and -_HUGE <= x <= _HUGE:
            # steady state: full, finite, clean. Welford runs on shifted values so it never sees
            # the price level.
            sh = self._shift
            self._run = self._run + 1 if x == w[-1] else 1
            old = w[0] - sh
            w.append(x)  # evicts the oldest
            y = x - sh
            delta = y - old
            mean = self._mean
            nm = mean + delta / n
            m2 = self._m2 + delta * ((y - nm) + (old - mean))
            self._mean = nm
            self._m2 = m2
            t = self._tick + 1
            self._tick = t
            pk = self._peak
            if m2 > pk:
                self._peak = pk = m2
            if (
                t >= self._due
                or m2 < pk * _DROP
                or y * y > self._kn * m2
                or not (m2 < _INF and -_INF < nm < _INF)
            ):
                self._recompute()
                sh, nm, m2 = self._shift, self._mean, self._m2
            if self._run >= n:
                self._shift, self._mean, self._m2, self._peak = x, 0.0, 0.0, 0.0
                sh, nm, m2 = x, 0.0, 0.0
            var = m2 / self._d
            if var < 0.0:
                var = 0.0
            return (sh + nm, var, math.sqrt(var))
        return self._slow(x)

    def _slow(self, x: float) -> tuple[float, float, float] | None:
        w, n = self._w, self.period
        full = len(w) == n
        if full and _unsafe(w[0]):
            self._bad -= 1
        if _unsafe(x):
            self._bad += 1
        self._run = self._run + 1 if w and w[-1] == x else 1
        if self._bad:
            self._dirty = True  # unsafe value in window: leave state alone, rebuild when clean
            self._ok = False
            w.append(x)
            return _NAN_RAW3 if len(w) == n else None
        if self._dirty:
            w.append(x)
            self._recompute()
            if len(w) < n:
                return None
        else:  # warm-up (a full clean window is the fast path unless x is unsafe, handled above)
            if not w:
                self._shift = x
            w.append(x)
            k = len(w)
            y = x - self._shift
            d = y - self._mean
            self._mean += d / k
            self._m2 += d * (y - self._mean)
            if k < n:
                return None
            self._peak = self._m2
        self._ok = True
        if self._run >= n:
            self._shift, self._mean, self._m2, self._peak = x, 0.0, 0.0, 0.0
        var = self._m2 / self._d
        var = max(var, 0.0)
        return (self._shift + self._mean, var, math.sqrt(var))


def _center(vals: list[float]) -> tuple[float, float, float, list[float]]:
    """Exact (fsum) shift, mean-of-shifted, m2 and shifted values; constant input gives m2 == 0."""
    n = len(vals)
    lo, hi = min(vals), max(vals)
    if lo == hi:
        return lo, 0.0, 0.0, [0.0] * n
    shift = math.fsum(vals) / n
    dev = [v - shift for v in vals]
    mean = math.fsum(dev) / n  # ~0, kept for exactness
    return shift, mean, math.fsum((d - mean) ** 2 for d in dev), dev


class PairMoments(NamedTuple):
    mean_x: float
    mean_y: float
    var_x: float
    var_y: float
    cov: float


_NAN_RAW5 = (_NAN, _NAN, _NAN, _NAN, _NAN)
_NAN_PAIR = PairMoments(_NAN, _NAN, _NAN, _NAN, _NAN)


class RollingPairMoments:
    """Means, variances and covariance of (x, y) over the last ``period`` pairs, O(1) amortised.

    Shifted Welford with removal (replace-oldest form) for both series plus the co-moment
    ``C = sum((x - mx) * (y - my))``; divisors are ``period - ddof`` (numpy convention). The
    non-finite / overflow convention is that of ``RollingMoments`` applied to the pair: while
    *either* component of any pair in the window is NaN/inf or has ``|v| > 1e150`` every field is
    NaN and the running state is left untouched; once the last such pair has left, the state is
    rebuilt exactly (``fsum``) at once. A window constant in x (or y) has exactly zero variance
    and zero covariance (detected in O(1) per update, and on every rebuild).

    Exact ``O(period)`` rebuilds: every ``max(1000, 4 * period)`` updates, and on recovery from a
    non-finite window (both unconditional); and *guard* rebuilds when ``m2x`` or ``m2y`` fall
    below ``1e-4`` of their peak since the last rebuild (outlier residue ~``spike**2 * eps``),
    when ``|C|`` falls below ``1e-4`` of its peak (cancellation; the peak is tracked during
    warmup too), or when the newest x (y) is more than ``sqrt(1e3)`` window standard deviations
    from its shift (smooth trends). Guard rebuilds are rate-limited by a token bucket: one per
    ``max(1, period // 2)`` updates on average, with a burst of four (so a spike that leaves just
    after another rebuild is still repaired at once). Under adversarial input the rate is hence
    at most ``~2 / period`` and a guard that finds no credit is deferred, so accuracy may degrade
    (error grows with the updates since the last rebuild) until credit is available. Rebuild
    rates on 20000-bar random walks are listed in the tests (< 1% at periods 20, 100, 300).

    ``update_raw`` is the hot path (plain ``(mean_x, mean_y, var_x, var_y, cov)`` tuple); ``update``
    wraps it in a :class:`PairMoments`. The bucket is kept as (credit, tick of that credit) and
    topped up lazily, only when a guard fires.
    """

    def __init__(self, period: int, ddof: int = 0) -> None:
        self.period = _check(period)
        if ddof < 0 or period - ddof < 1:
            raise ValueError(
                f"ddof must satisfy 0 <= ddof < period, got ddof={ddof}, period={period}"
            )
        self.ddof = ddof
        self._wx: deque[float] = deque(maxlen=period)
        self._wy: deque[float] = deque(maxlen=period)
        self._sx = self._sy = 0.0  # shifts
        self._mx = self._my = 0.0  # means of the shifted values
        self._m2x = self._m2y = self._c = 0.0
        self._px = self._py = self._pc = 0.0  # peaks of m2x, m2y, |C| since the last rebuild
        self._rx = self._ry = 0  # trailing runs of identical x / y
        self._bad = 0  # pairs with an unsafe component currently in the window
        self._dirty = False
        self._ok = False  # window full, all values safe, state clean (fast path allowed)
        self._tick = 0  # sliding updates so far
        self._every = max(_RECOMPUTE_MIN, 4 * period)
        self._due = self._every
        self._gap = max(1, period // 2)
        self._cap = _BURST * self._gap
        self._cr = self._cap  # guard-rebuild credit as of tick ``_cr_t``
        self._cr_t = 0
        self._d = period - ddof
        self._kn = _SHIFT_K2 / period
        if period == 2:  # closed form, stateless but for the previous pair
            self._prev: tuple[float, float] | None = None
            self.update_raw = self._raw2  # type: ignore[method-assign]

    def _raw2(self, x: float, y: float) -> tuple[float, float, float, float, float] | None:
        """Period 2: ``m2 = d**2 / 2``, ``C = dx * dy / 2`` (no state, no rebuilds)."""
        p, self._prev = self._prev, (x, y)
        if p is None:
            return None
        a, b = p
        if -_HUGE <= x <= _HUGE and -_HUGE <= y <= _HUGE and -_HUGE <= a <= _HUGE \
                and -_HUGE <= b <= _HUGE:
            dx, dy = x - a, y - b
            d = 2.0 * self._d
            return (a + dx / 2.0, b + dy / 2.0, dx * dx / d, dy * dy / d, dx * dy / d)
        return _NAN_RAW5

    def _recompute(self) -> None:
        self._sx, self._mx, self._m2x, dx = _center(list(self._wx))
        self._sy, self._my, self._m2y, dy = _center(list(self._wy))
        mx, my = self._mx, self._my
        self._c = math.fsum((a - mx) * (b - my) for a, b in zip(dx, dy))
        self._px, self._py, self._pc = self._m2x, self._m2y, abs(self._c)
        self._due = self._tick + self._every
        self._dirty = False

    def update(self, x: float, y: float) -> PairMoments | None:
        r = self.update_raw(x, y)
        return None if r is None else _new(PairMoments, r)

    def update_raw(self, x: float, y: float) -> tuple[float, float, float, float, float] | None:
        """``(mean_x, mean_y, var_x, var_y, cov)`` as a plain tuple (``None`` during warm-up)."""
        wx, n = self._wx, self.period
        if self._ok and len(wx) == n and -_HUGE <= x <= _HUGE and -_HUGE <= y <= _HUGE:
            wy = self._wy
            sx, sy, mx, my = self._sx, self._sy, self._mx, self._my
            self._rx = self._rx + 1 if x == wx[-1] else 1
            self._ry = self._ry + 1 if y == wy[-1] else 1
            oxs, oys = wx[0] - sx, wy[0] - sy
            wx.append(x)  # evicts the oldest pair
            wy.append(y)
            xs, ys = x - sx, y - sy  # Welford runs on shifted values: it never sees the price level
            dx, dy = xs - oxs, ys - oys
            nmx, nmy = mx + dx / n, my + dy / n
            m2x = self._m2x + dx * ((xs - nmx) + (oxs - mx))
            m2y = self._m2y + dy * ((ys - nmy) + (oys - my))
            c = self._c + ((xs - mx) * dy + (oys - my) * dx - dx * dy / n)
            self._m2x, self._m2y, self._c, self._mx, self._my = m2x, m2y, c, nmx, nmy
            t = self._tick + 1
            self._tick = t
            px, py, pc = self._px, self._py, self._pc
            if m2x > px:
                self._px = px = m2x
            if m2y > py:
                self._py = py = m2y
            ac = c if c >= 0.0 else -c
            if ac > pc:
                self._pc = pc = ac
            tot = m2x + m2y + c + nmx + nmy  # finite iff every term is
            rebuild = False
            if t >= self._due or not (-_INF < tot < _INF):
                rebuild = True
            else:
                kn = self._kn
                if (
                    m2x < px * _DROP
                    or m2y < py * _DROP
                    or ac < pc * _CANCEL
                    or xs * xs > kn * m2x
                    or ys * ys > kn * m2y
                ):
                    cr = self._cr + t - self._cr_t  # token bucket, topped up lazily
                    cap = self._cap
                    if cr > cap:
                        cr = cap
                    if cr >= self._gap:
                        cr -= self._gap
                        rebuild = True
                    self._cr, self._cr_t = cr, t
            if rebuild:
                self._recompute()
                sx, sy, nmx, nmy = self._sx, self._sy, self._mx, self._my
                m2x, m2y, c = self._m2x, self._m2y, self._c
            if self._rx >= n:  # constant x window: exact zero variance and covariance
                self._sx, self._mx, self._m2x = x, 0.0, 0.0
                self._px = self._c = self._pc = 0.0
                sx, nmx, m2x, c = x, 0.0, 0.0, 0.0
            if self._ry >= n:
                self._sy, self._my, self._m2y = y, 0.0, 0.0
                self._py = self._c = self._pc = 0.0
                sy, nmy, m2y, c = y, 0.0, 0.0, 0.0
            d = self._d
            vx = m2x / d
            vy = m2y / d
            return (sx + nmx, sy + nmy, vx if vx > 0.0 else 0.0, vy if vy > 0.0 else 0.0, c / d)
        return self._slow(x, y)

    def _slow(self, x: float, y: float) -> tuple[float, float, float, float, float] | None:
        wx, wy, n = self._wx, self._wy, self.period
        full = len(wx) == n
        if full and (_unsafe(wx[0]) or _unsafe(wy[0])):
            self._bad -= 1
        if _unsafe(x) or _unsafe(y):
            self._bad += 1
        self._rx = self._rx + 1 if wx and wx[-1] == x else 1
        self._ry = self._ry + 1 if wy and wy[-1] == y else 1
        if self._bad:
            self._dirty = True  # unsafe value in window: leave state alone, rebuild when clean
            self._ok = False
            wx.append(x)
            wy.append(y)
            return _NAN_RAW5 if len(wx) == n else None
        if self._dirty:
            wx.append(x)
            wy.append(y)
            self._recompute()
            if len(wx) < n:
                return None
        else:  # warm-up (a full clean window is the fast path unless a value is unsafe)
            if not wx:
                self._sx, self._sy = x, y
            wx.append(x)
            wy.append(y)
            k = len(wx)
            xs, ys = x - self._sx, y - self._sy
            dx, dy = xs - self._mx, ys - self._my
            self._mx += dx / k
            self._my += dy / k
            self._m2x += dx * (xs - self._mx)
            self._m2y += dy * (ys - self._my)
            self._c += dx * (ys - self._my)
            self._pc = max(self._pc, abs(self._c))
            if k < n:
                return None
            self._px, self._py = self._m2x, self._m2y
        self._ok = True
        if self._rx >= n:  # constant x window: exact zero variance and covariance
            self._sx, self._mx, self._m2x, self._px, self._c, self._pc = x, 0.0, 0.0, 0.0, 0.0, 0.0
        if self._ry >= n:
            self._sy, self._my, self._m2y, self._py, self._c, self._pc = y, 0.0, 0.0, 0.0, 0.0, 0.0
        d = self._d
        return (
            self._sx + self._mx,
            self._sy + self._my,
            max(self._m2x / d, 0.0),
            max(self._m2y / d, 0.0),
            self._c / d,
        )


class LinFit(NamedTuple):
    """Least-squares line of the window values against position 0..n-1."""

    intercept: float  # line value at position 0
    slope: float  # per bar
    mean: float  # mean of the values
    var: float  # population variance of the values
    sse: float  # sum of squared residuals


_NAN_FIT = LinFit(_NAN, _NAN, _NAN, _NAN, _NAN)


class RollingLinReg:
    """Least-squares line of the last ``period`` values against position, O(1) amortised.

    Tracks the shifted Welford mean / ``m2`` of the values and ``T = sum(c_i * y_i)`` with the
    centred positions ``c_i = i - (n-1)/2``; ``slope = T / Sxx`` (``Sxx = n(n^2-1)/12``),
    ``sse = m2 - T * slope``. The non-finite / overflow convention, rebuild schedule, exact-zero
    handling of constant windows and the token-bucket rate limit are those of
    ``RollingPairMoments`` (guards: ``m2`` and ``|T|`` falling below ``1e-4`` of their peaks,
    drift of the newest value beyond ``sqrt(1e3)`` standard deviations from the shift; and
    ``sse`` falling below ``1e-6 * m2``, see below).
    Slope and intercept keep a relative error ~1e-12 while ``|T|`` stays above ``1e-4`` of its
    peak. ``sse`` is a difference of two sums of squares, so its absolute error is ~``eps * m2``
    even right after a rebuild: the residual std of a near-perfect fit is good to ~``2.9e-8``
    window standard deviations (measured worst case over the test series; more between
    rebuilds, which the ``sse < 1e-6 * m2`` guard requests as often as the rate limit allows);
    ``sse`` is clamped at zero. The window is
    rebuilt once when it first fills.

    ``update_raw`` is the hot path (plain ``(intercept, slope, sse)`` tuple); ``update`` wraps it
    in a :class:`LinFit`.
    """

    def __init__(self, period: int) -> None:
        if period < 2:
            raise ValueError(f"period must be >= 2, got {period}")
        self.period = _check(period)
        self._w: deque[float] = deque(maxlen=period)
        self._sh = 0.0
        self._m = 0.0
        self._m2 = 0.0
        self._t = 0.0
        self._s = self._sc = 0.0  # compensated sum of the shifted values (+ its correction term)
        self._p2 = 0.0  # peak m2 since the last rebuild
        self._pt = 0.0  # peak |T|
        self._run = 0
        self._bad = 0
        self._dirty = False
        self._ok = False  # window full, all values safe, state clean (fast path allowed)
        self._tick = 0
        self._every = max(_RECOMPUTE_MIN, 4 * period)
        self._due = self._every
        self._gap = max(1, period // 2)
        self._cap = _BURST * self._gap
        self._cr = self._cap
        self._cr_t = 0
        self._sxx = period * (period * period - 1) / 12.0
        self._half = (period - 1) / 2.0
        self._hp = (period + 1) / 2.0
        self._kn = _SHIFT_K2 / period
        if period == 2:  # the line through two points: closed form, no rebuilds, sse == 0
            self._prev: float | None = None
            self.update_raw = self._raw2  # type: ignore[method-assign]
            self.update = self._update2  # type: ignore[method-assign]

    def _raw2(self, y: float) -> tuple[float, float, float] | None:
        a, self._prev = self._prev, y
        if a is None:
            return None
        if -_HUGE <= y <= _HUGE and -_HUGE <= a <= _HUGE:
            return (a, y - a, 0.0)
        return (_NAN, _NAN, _NAN)

    def _update2(self, y: float) -> LinFit | None:
        r = self._raw2(y)
        if r is None:
            return None
        a, d = r[0], r[1]
        if a != a:
            return _NAN_FIT
        return _new(LinFit, (a, d, a + d / 2.0, d * d / 4.0, 0.0))

    def _recompute(self) -> None:
        self._sh, self._m, self._m2, dev = _center(list(self._w))
        h = self._half
        self._t = math.fsum((i - h) * d for i, d in enumerate(dev))
        self._s, self._sc = math.fsum(dev), 0.0
        self._p2, self._pt = self._m2, abs(self._t)
        self._due = self._tick + self._every
        self._dirty = False

    def update(self, y: float) -> LinFit | None:
        r = self.update_raw(y)
        if r is None:
            return None
        if self._bad:
            return _NAN_FIT
        mean = self._sh + self._m
        return _new(LinFit, (r[0], r[1], mean, max(self._m2 / self.period, 0.0), r[2]))

    def update_raw(self, y: float) -> tuple[float, float, float] | None:
        """``(intercept, slope, sse)`` as a plain tuple (``None`` during warm-up)."""
        w, n = self._w, self.period
        if self._ok and len(w) == n and -_HUGE <= y <= _HUGE:
            sh, m = self._sh, self._m
            self._run = self._run + 1 if y == w[-1] else 1
            oy = w[0] - sh
            w.append(y)
            ys = y - sh
            dy = ys - oy
            nm = m + dy / n
            m2 = self._m2 + dy * ((ys - nm) + (oy - m))
            # positions shift by one: T' = T + (n+1)/2 * y_old - sum(y) + (n-1)/2 * y_new
            # (the sum is carried exactly, Neumaier-compensated: a biased sum would make T drift)
            s, sc = self._s, self._sc
            tv = self._t + (self._hp * oy - (s + sc) + self._half * ys)
            tt = s + ys
            sc += ((s - tt) + ys) if abs(s) >= abs(ys) else ((ys - tt) + s)
            s = tt
            v = -oy
            tt = s + v
            sc += ((s - tt) + v) if abs(s) >= abs(v) else ((v - tt) + s)
            self._s, self._sc = tt, sc
            self._t, self._m, self._m2 = tv, nm, m2
            t = self._tick + 1
            self._tick = t
            p2, pt = self._p2, self._pt
            if m2 > p2:
                self._p2 = p2 = m2
            at = tv if tv >= 0.0 else -tv
            if at > pt:
                self._pt = pt = at
            tot = m2 + tv + nm  # finite iff every term is
            rebuild = False
            if t >= self._due or not (-_INF < tot < _INF):
                rebuild = True
            elif (
                m2 < p2 * _DROP
                or at < pt * _CANCEL
                or ys * ys > self._kn * m2
                or m2 - tv * tv / self._sxx < m2 * _NEAR
            ):
                cr = self._cr + t - self._cr_t  # token bucket, topped up lazily
                cap = self._cap
                if cr > cap:
                    cr = cap
                if cr >= self._gap:
                    cr -= self._gap
                    rebuild = True
                self._cr, self._cr_t = cr, t
            if rebuild:
                self._recompute()
                sh, nm, m2, tv = self._sh, self._m, self._m2, self._t
            if self._run >= n:  # constant window: exactly flat, zero residual
                self._sh, self._m, self._m2 = y, 0.0, 0.0
                self._t = self._p2 = self._pt = 0.0
                self._s = self._sc = 0.0
                sh, nm, m2, tv = y, 0.0, 0.0, 0.0
            slope = tv / self._sxx
            sse = m2 - tv * slope
            return (
                sh + nm - slope * self._half,
                slope,
                sse if sse > 0.0 else 0.0,
            )
        return self._slow(y)

    def _slow(self, y: float) -> tuple[float, float, float] | None:
        w, n = self._w, self.period
        full = len(w) == n
        if full and _unsafe(w[0]):
            self._bad -= 1
        if _unsafe(y):
            self._bad += 1
        self._run = self._run + 1 if w and w[-1] == y else 1
        if self._bad:
            self._dirty = True
            self._ok = False
            w.append(y)
            return (_NAN, _NAN, _NAN) if len(w) == n else None
        w.append(y)
        if len(w) < n:
            return None
        self._recompute()  # first full window (or recovery from a non-finite window)
        self._ok = True
        if self._run >= n:  # constant window: exactly flat, zero residual
            self._sh, self._m, self._m2, self._t, self._p2, self._pt = y, 0.0, 0.0, 0.0, 0.0, 0.0
            self._s = self._sc = 0.0
        slope = self._t / self._sxx
        sse = self._m2 - self._t * slope
        return (
            self._sh + self._m - slope * self._half,
            slope,
            max(sse, 0.0),
        )
