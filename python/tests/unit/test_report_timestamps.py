"""Tests for report timestamp formatting.

A fill's ``ts`` is Unix nanoseconds. Every renderer used to print it with
``str(ts)``, so the trades tables showed values like ``1790307900000000000``
instead of a date. These tests pin the readable form.
"""

from __future__ import annotations

import datetime as dt

import pytest

from honba.report import _format_timestamp_ns


def _ns(year: int, month: int, day: int) -> int:
    return int(dt.datetime(year, month, day, tzinfo=dt.timezone.utc).timestamp()) * 10**9


def test_formats_nanoseconds_as_a_utc_date() -> None:
    assert _format_timestamp_ns(_ns(2026, 10, 3)) == "2026-10-03"


def test_formats_an_intraday_timestamp_as_its_date() -> None:
    # A mid-session fill must still report the trading day it happened on.
    intraday = _ns(2024, 1, 2) + 9 * 60 * 60 * 10**9 + 30 * 60 * 10**9
    assert _format_timestamp_ns(intraday) == "2024-01-02"


@pytest.mark.parametrize("value", [0, -1, -(10**18)])
def test_a_missing_timestamp_is_a_question_mark_not_an_epoch_date(value: int) -> None:
    assert _format_timestamp_ns(value) == "?"


def test_an_out_of_range_timestamp_is_a_question_mark_not_a_crash() -> None:
    assert _format_timestamp_ns(10**30) == "?"
