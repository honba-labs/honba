import datetime as dt
import pytest
from honba.india.calendar import NseCalendar, SessionWindow


def test_nse_calendar_weekends_and_holidays():
    holidays = [dt.date(2025, 1, 26), dt.date(2025, 8, 15)]
    cal = NseCalendar(holidays=holidays)

    # 2025-01-24 is Friday (trading)
    assert cal.is_trading_day(dt.date(2025, 1, 24)) is True
    # 2025-01-25 is Saturday
    assert cal.is_trading_day(dt.date(2025, 1, 25)) is False
    # 2025-01-26 is Sunday and holiday
    assert cal.is_trading_day(dt.date(2025, 1, 26)) is False
    # 2025-01-27 is Monday (trading)
    assert cal.is_trading_day(dt.date(2025, 1, 27)) is True

    # 2025-08-15 is Friday (Independence day holiday)
    assert cal.is_holiday(dt.date(2025, 8, 15)) is True
    assert cal.is_trading_day(dt.date(2025, 8, 15)) is False


def test_nse_calendar_navigation():
    holidays = [dt.date(2025, 1, 27)]  # make Monday a holiday
    cal = NseCalendar(holidays=holidays)

    friday = dt.date(2025, 1, 24)
    # Next trading day after Friday should skip Sat, Sun, and Mon -> Tuesday Jan 28
    assert cal.next_trading_day(friday) == dt.date(2025, 1, 28)
    # Prev trading day before Tuesday Jan 28 should skip Mon, Sun, Sat -> Friday Jan 24
    assert cal.prev_trading_day(dt.date(2025, 1, 28)) == friday


def test_nse_calendar_sessions_before():
    cal = NseCalendar()
    # 2025-01-10 is Friday
    sessions = cal.sessions_before(dt.date(2025, 1, 10), count=5)
    assert len(sessions) == 5
    assert sessions[-1] == dt.date(2025, 1, 10)
    assert sessions[0] == dt.date(2025, 1, 6)  # Mon, Tue, Wed, Thu, Fri
