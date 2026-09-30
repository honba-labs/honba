from honba.strategies.indicators._india import (
    IST_OFFSET_NS, NSE_SESSION_OPEN_MIN, TRADING_DAYS_PER_YEAR, ist_session_day,
)

H = 3_600_000_000_000
DAY = 24 * H


def test_constants():
    assert TRADING_DAYS_PER_YEAR == 252
    assert NSE_SESSION_OPEN_MIN == 9 * 60 + 15
    assert IST_OFFSET_NS == 5 * H + 30 * 60_000_000_000


def test_session_day_rolls_over_at_ist_midnight_not_utc():
    d0 = 20_000 * DAY  # UTC midnight of some day
    # 03:45 UTC == 09:15 IST, same IST day as 09:15 IST
    assert ist_session_day(d0 + 3 * H + 45 * 60_000_000_000) == 20_000
    # 18:29 UTC is 23:59 IST (same IST day), 18:30 UTC is 00:00 IST next day
    assert ist_session_day(d0 + 18 * H + 29 * 60_000_000_000) == 20_000
    assert ist_session_day(d0 + 18 * H + 30 * 60_000_000_000) == 20_001
