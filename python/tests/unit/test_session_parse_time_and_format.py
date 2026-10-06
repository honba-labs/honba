"""Characterization tests for naive-time handling and best-effort formatting."""

from __future__ import annotations

import datetime as dt

from honba.session import _parse_time
from honba.utils.format import (
    format_currency,
    format_date_indian,
    format_date_iso,
    format_date_us,
)


def test_parse_time_returns_naive_midnight_for_dates_and_iso_strings() -> None:
    expected = dt.datetime(2025, 1, 6)  # noqa: DTZ001 - asserting the naive contract
    assert _parse_time(dt.date(2025, 1, 6)) == expected
    assert _parse_time("2025-01-06") == expected
    for value in (dt.date(2025, 1, 6), "2025-01-06"):
        assert _parse_time(value).tzinfo is None


def test_parse_time_passes_datetimes_and_datetime_strings_through() -> None:
    aware = dt.datetime(2025, 1, 6, 9, 15, tzinfo=dt.timezone.utc)
    assert _parse_time(aware) is aware
    assert _parse_time("2025-01-06T09:15:00") == dt.datetime(2025, 1, 6, 9, 15)  # noqa: DTZ001 - asserting the naive contract


def test_format_date_strings_and_fallback() -> None:
    assert format_date_indian("2025-01-06") == "06-01-2025"
    assert format_date_indian("06/01/2025") == "06-01-2025"
    assert format_date_us("2025-01-06") == "01-06-2025"
    assert format_date_indian("not a date") == "not a date"
    assert format_date_us("not a date") == "not a date"
    assert format_date_indian(dt.date(2025, 1, 6)) == "06-01-2025"
    assert format_date_iso(dt.date(2025, 1, 6)) == "2025-01-06"


def test_format_currency_non_numeric_falls_back_to_str() -> None:
    assert format_currency("abc") == "abc"
    assert format_currency(None) == "None"
