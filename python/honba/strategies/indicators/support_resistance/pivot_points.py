"""Session pivot points (support_resistance family)."""

from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.indicators._base import Indicator, indicator
from honba.strategies.indicators._india import ist_session_day

MODES = ("standard", "fibonacci", "camarilla", "woodie")


@dataclass(frozen=True, slots=True)
class PivotPointsValue:
    pp: float
    r1: float
    r2: float
    r3: float
    s1: float
    s2: float
    s3: float


@indicator(
    "pivot_points",
    "support_resistance",
    inputs=("high", "low", "close", "ts"),
    outputs=("pp", "r1", "r2", "r3", "s1", "s2", "s3"),
    warmup=None,
)
class PivotPoints(Indicator):
    """Pivot levels for the current IST session from the previous IST session's H/L/C.

    Sessions are detected with ``ist_session_day(ts)`` (daily bars: each bar is a session).
    First value is on the first bar of the second session; levels stay fixed all session.
    With P = previous (H+L+C)/3 and R = H-L:
    standard: R1=2P-L, S1=2P-H, R2/S2=P+/-R, R3=H+2(P-L), S3=L-2(H-P).
    fibonacci: R1..R3 = P + 0.382/0.618/1.0*R, S1..S3 = P - same.
    camarilla: R1..R3 = C + 1.1*R/12, /6, /4; S1..S3 = C - same (pp = P).
    woodie: P=(H+L+2C)/4 (no next-open available), other levels as standard.
    """

    def __init__(self, mode: str = "standard") -> None:
        if mode not in MODES:
            raise ValueError(f"mode must be one of {MODES}, got {mode!r}")
        self.mode = mode
        self._day: int | None = None
        self._h = self._l = self._c = 0.0
        self._levels: PivotPointsValue | None = None

    @property
    def warmup(self) -> None:
        return None  # session-dependent: first value on the first bar of the second session

    def _compute(self, h: float, l: float, c: float) -> PivotPointsValue:
        r = h - l
        if self.mode == "woodie":
            p = (h + l + 2 * c) / 4
        else:
            p = (h + l + c) / 3
        if self.mode in ("standard", "woodie"):
            return PivotPointsValue(
                p, 2 * p - l, p + r, h + 2 * (p - l), 2 * p - h, p - r, l - 2 * (h - p)
            )
        if self.mode == "fibonacci":
            return PivotPointsValue(
                p, p + 0.382 * r, p + 0.618 * r, p + r, p - 0.382 * r, p - 0.618 * r, p - r
            )
        k = 1.1 * r
        return PivotPointsValue(
            p, c + k / 12, c + k / 6, c + k / 4, c - k / 12, c - k / 6, c - k / 4
        )

    def update(self, high: float, low: float, close: float, ts: float) -> PivotPointsValue | None:
        day = ist_session_day(int(ts))
        if self._day is None:
            self._day, self._h, self._l = day, high, low
        elif day != self._day:
            self._levels = self._compute(self._h, self._l, self._c)
            self._day, self._h, self._l = day, high, low
        else:
            self._h, self._l = max(self._h, high), min(self._l, low)
        self._c = close
        return self._levels
