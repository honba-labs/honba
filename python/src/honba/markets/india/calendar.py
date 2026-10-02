"""Trading calendar and session models for the Indian market (NSE/BSE)."""

from __future__ import annotations

import datetime as dt
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class SessionWindow:
    """A daily trading session time window [open, close)."""

    open_time: dt.time = dt.time(9, 15)
    close_time: dt.time = dt.time(15, 30)

    def contains(self, t: dt.time) -> bool:
        return self.open_time <= t < self.close_time


class MarketCalendar(Protocol):
    """Trading calendar protocol."""

    def is_holiday(self, date: dt.date) -> bool:
        ...

    def is_trading_day(self, date: dt.date) -> bool:
        ...

    def next_trading_day(self, date: dt.date) -> dt.date:
        ...

    def prev_trading_day(self, date: dt.date) -> dt.date:
        ...

    def trading_days_between(self, start: dt.date, end: dt.date) -> list[dt.date]:
        ...

    def sessions_before(self, asof: dt.date, count: int) -> list[dt.date]:
        ...


class NseCalendar:
    """NSE trading calendar with weekend and holiday exclusion."""

    def __init__(
        self,
        holidays: Iterable[dt.date] | None = None,
        session: SessionWindow | None = None,
    ) -> None:
        self._holidays: frozenset[dt.date] = frozenset(holidays) if holidays else frozenset()
        self.session = session or SessionWindow()

    def is_holiday(self, date: dt.date) -> bool:
        return date in self._holidays

    def is_trading_day(self, date: dt.date) -> bool:
        # Weekend: Saturday=5, Sunday=6
        if date.weekday() >= 5:
            return False
        return not self.is_holiday(date)

    def next_trading_day(self, date: dt.date) -> dt.date:
        d = date + dt.timedelta(days=1)
        while not self.is_trading_day(d):
            d += dt.timedelta(days=1)
        return d

    def prev_trading_day(self, date: dt.date) -> dt.date:
        d = date - dt.timedelta(days=1)
        while not self.is_trading_day(d):
            d -= dt.timedelta(days=1)
        return d

    def trading_days_between(self, start: dt.date, end: dt.date) -> list[dt.date]:
        """Return list of trading days in [start, end] inclusive."""
        if start > end:
            return []
        days: list[dt.date] = []
        cur = start
        while cur <= end:
            if self.is_trading_day(cur):
                days.append(cur)
            cur += dt.timedelta(days=1)
        return days

    def sessions_before(self, asof: dt.date, count: int) -> list[dt.date]:
        """Return `count` trading sessions ending at or before `asof` in chronological order."""
        if count <= 0:
            return []
        sessions: list[dt.date] = []
        cur = asof
        while len(sessions) < count:
            if self.is_trading_day(cur):
                sessions.append(cur)
            cur -= dt.timedelta(days=1)
        sessions.reverse()
        return sessions
