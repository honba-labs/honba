"""Regression: NSE bhavcopy bar timestamps are 09:15 IST regardless of machine timezone."""

import time

import pytest

from honba.research.data_loader.nse import parse_bhavcopy_csv

# 2024-01-02 09:15 IST == 2024-01-02T03:45:00Z
EPOCH_NS_2024_01_02_0915_IST = 1_704_167_100 * 1_000_000_000

SEC = """SYMBOL, SERIES, DATE1, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, TTL_TRD_QNTY
RELIANCE, EQ, 02-Jan-2024, 100.0, 110.0, 90.0, 105.0, 1000
"""


@pytest.fixture(params=["America/New_York", "UTC", "Asia/Kolkata", "Pacific/Auckland"])
def machine_tz(request, monkeypatch):
    if not hasattr(time, "tzset"):
        pytest.skip("tzset unavailable")
    monkeypatch.setenv("TZ", request.param)
    time.tzset()
    yield request.param
    monkeypatch.undo()
    time.tzset()


def test_bar_ts_is_ist_open_independent_of_local_tz(machine_tz):
    bars = parse_bhavcopy_csv(SEC)
    assert bars["RELIANCE"].ts == EPOCH_NS_2024_01_02_0915_IST
