"""Unit tests for yfinance market data loader."""

import datetime as dt
from unittest.mock import MagicMock, patch

import pandas as pd
import pytest

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.data.loaders.yfinance import (
    YFinanceProvider,
    _chunk_interval,
    dataframe_to_bars,
    from_yfinance_symbol,
    normalize_timeframe,
    to_yfinance_symbol,
)
from honba.screener.coverage import DateInterval
from honba.screener.ports import InMemoryBarStore, validate_bar
from honba.screener.service import DataService


class TestSymbolTranslation:
    def test_to_yfinance_symbol_nse(self) -> None:
        inst = InstrumentId("RELIANCE", "NSE")
        assert to_yfinance_symbol(inst) == "RELIANCE.NS"

    def test_to_yfinance_symbol_bse(self) -> None:
        inst = InstrumentId("TCS", "BSE")
        assert to_yfinance_symbol(inst) == "TCS.BO"

    def test_to_yfinance_symbol_already_suffixed(self) -> None:
        assert to_yfinance_symbol(InstrumentId("INFY.NS", "NSE")) == "INFY.NS"
        assert to_yfinance_symbol(InstrumentId("INFY.BO", "BSE")) == "INFY.BO"

    def test_to_yfinance_symbol_indices(self) -> None:
        assert to_yfinance_symbol(InstrumentId("NIFTY50", "NSE")) == "^NSEI"
        assert to_yfinance_symbol(InstrumentId("BANKNIFTY", "NSE")) == "^NSEBANK"
        assert to_yfinance_symbol(InstrumentId("SENSEX", "BSE")) == "^BSESN"

    def test_to_yfinance_symbol_custom_map(self) -> None:
        custom = {"MYINDEX": "^CUSTOM"}
        assert to_yfinance_symbol(InstrumentId("MYINDEX", "NSE"), custom_map=custom) == "^CUSTOM"

    def test_to_yfinance_symbol_global(self) -> None:
        assert to_yfinance_symbol(InstrumentId("AAPL", "NASDAQ")) == "AAPL"

    def test_from_yfinance_symbol(self) -> None:
        assert from_yfinance_symbol("RELIANCE.NS") == InstrumentId("RELIANCE", "NSE")
        assert from_yfinance_symbol("TCS.BO") == InstrumentId("TCS", "BSE")
        assert from_yfinance_symbol("^NSEI") == InstrumentId("NIFTY50", "NSE")
        assert from_yfinance_symbol("^BSESN") == InstrumentId("SENSEX", "BSE")
        assert from_yfinance_symbol("AAPL", default_venue="NASDAQ") == InstrumentId("AAPL", "NASDAQ")


class TestTimeframeNormalization:
    def test_supported_timeframes(self) -> None:
        assert normalize_timeframe("1D") == "1d"
        assert normalize_timeframe("1d") == "1d"
        assert normalize_timeframe("daily") == "1d"
        assert normalize_timeframe("1m") == "1m"
        assert normalize_timeframe("5m") == "5m"
        assert normalize_timeframe("15m") == "15m"
        assert normalize_timeframe("30m") == "30m"
        assert normalize_timeframe("60m") == "60m"
        assert normalize_timeframe("1h") == "60m"
        assert normalize_timeframe("1W") == "1wk"
        assert normalize_timeframe("1M") == "1mo"

    def test_unsupported_timeframe(self) -> None:
        with pytest.raises(ValueError, match="Unsupported timeframe"):
            normalize_timeframe("45m")


class TestIntervalChunking:
    def test_chunking_within_bound(self) -> None:
        start = dt.date(2026, 1, 1)
        end = dt.date(2026, 1, 5)
        chunks = _chunk_interval(start, end, chunk_days=7)
        assert chunks == [(start, end)]

    def test_chunking_split(self) -> None:
        start = dt.date(2026, 1, 1)
        end = dt.date(2026, 1, 15)
        chunks = _chunk_interval(start, end, chunk_days=5)
        assert chunks == [
            (dt.date(2026, 1, 1), dt.date(2026, 1, 6)),
            (dt.date(2026, 1, 6), dt.date(2026, 1, 11)),
            (dt.date(2026, 1, 11), dt.date(2026, 1, 15)),
        ]

    def test_chunking_empty(self) -> None:
        start = dt.date(2026, 1, 5)
        end = dt.date(2026, 1, 5)
        assert _chunk_interval(start, end, chunk_days=5) == []


class TestDataframeToBars:
    def test_convert_daily_bars(self) -> None:
        dates = pd.date_range("2026-01-01", periods=3, freq="D", tz="Asia/Kolkata")
        df = pd.DataFrame(
            {
                "Open": [100.0, 105.0, 102.0],
                "High": [108.0, 110.0, 106.0],
                "Low": [98.0, 103.0, 100.0],
                "Close": [105.0, 104.0, 105.0],
                "Volume": [1000.0, 2000.0, 1500.0],
            },
            index=dates,
        )
        inst = InstrumentId("RELIANCE", "NSE")
        bars = dataframe_to_bars(df, inst, "1D")
        assert len(bars) == 3
        for b in bars:
            assert validate_bar(b)
            assert b.instrument_id == inst
        assert bars[0].open == 100.0
        assert bars[0].close == 105.0

    def test_convert_intraday_bars(self) -> None:
        times = pd.date_range("2026-01-01 09:15", periods=3, freq="5min", tz="Asia/Kolkata")
        df = pd.DataFrame(
            {
                "Open": [100.0, 101.0, 102.0],
                "High": [102.0, 103.0, 104.0],
                "Low": [99.0, 100.0, 101.0],
                "Close": [101.0, 102.0, 103.0],
                "Volume": [500.0, 600.0, 700.0],
            },
            index=times,
        )
        inst = InstrumentId("RELIANCE", "NSE")
        bars = dataframe_to_bars(df, inst, "5m")
        assert len(bars) == 3
        assert bars[0].open == 100.0
        assert bars[1].open == 101.0
        assert bars[2].open == 102.0

    def test_filter_by_interval(self) -> None:
        dates = pd.date_range("2026-01-01", periods=4, freq="D", tz="Asia/Kolkata")
        df = pd.DataFrame(
            {
                "Open": [100.0, 105.0, 102.0, 104.0],
                "High": [108.0, 110.0, 106.0, 108.0],
                "Low": [98.0, 103.0, 100.0, 102.0],
                "Close": [105.0, 104.0, 105.0, 107.0],
                "Volume": [1000.0, 2000.0, 1500.0, 1200.0],
            },
            index=dates,
        )
        inst = InstrumentId("RELIANCE", "NSE")
        interval = DateInterval(dt.date(2026, 1, 2), dt.date(2026, 1, 4))
        bars = dataframe_to_bars(df, inst, "1D", interval=interval)
        assert len(bars) == 2


class TestYFinanceProvider:
    @patch("yfinance.Ticker")
    def test_fetch_with_mock(self, mock_ticker_cls: MagicMock) -> None:
        dates = pd.date_range("2026-01-01", periods=2, freq="D", tz="Asia/Kolkata")
        mock_df = pd.DataFrame(
            {
                "Open": [2500.0, 2520.0],
                "High": [2550.0, 2560.0],
                "Low": [2490.0, 2510.0],
                "Close": [2530.0, 2540.0],
                "Volume": [50000.0, 60000.0],
            },
            index=dates,
        )
        mock_instance = MagicMock()
        mock_instance.history.return_value = mock_df
        mock_ticker_cls.return_value = mock_instance

        provider = YFinanceProvider(rate_limit_pause=0.0)
        assert provider.name == "yfinance"

        inst = InstrumentId("RELIANCE", "NSE")
        interval = DateInterval(dt.date(2026, 1, 1), dt.date(2026, 1, 3))
        bars = provider.fetch(inst, "1D", interval)

        assert len(bars) == 2
        assert bars[0].close == 2530.0
        assert bars[1].close == 2540.0
        mock_ticker_cls.assert_called_with("RELIANCE.NS")

    @patch("yfinance.Ticker")
    def test_dataservice_integration(self, mock_ticker_cls: MagicMock) -> None:
        dates = pd.date_range("2026-01-01", periods=3, freq="D", tz="Asia/Kolkata")
        mock_df = pd.DataFrame(
            {
                "Open": [100.0, 102.0, 104.0],
                "High": [105.0, 106.0, 108.0],
                "Low": [98.0, 100.0, 102.0],
                "Close": [102.0, 104.0, 106.0],
                "Volume": [1000.0, 1200.0, 1400.0],
            },
            index=dates,
        )
        mock_instance = MagicMock()
        mock_instance.history.return_value = mock_df
        mock_ticker_cls.return_value = mock_instance

        store = InMemoryBarStore()
        provider = YFinanceProvider(rate_limit_pause=0.0)
        svc = DataService(store=store, providers=[provider])

        inst = InstrumentId("TCS", "NSE")
        interval = DateInterval(dt.date(2026, 1, 1), dt.date(2026, 1, 4))
        plan = svc.plan([inst], "1D", interval)
        assert len(plan.gaps_by_instrument[inst]) == 1

        res = svc.ensure(plan)
        assert res.success is True
        assert len(res.bars[inst]) == 3

        # Second plan should show 0 gaps because data is now in store
        plan2 = svc.plan([inst], "1D", interval)
        assert len(plan2.gaps_by_instrument[inst]) == 0


def test_live_yfinance_fetch() -> None:
    """Live smoke test fetching real market data."""
    provider = YFinanceProvider()
    inst = InstrumentId("RELIANCE", "NSE")
    # Fetch last 3 days
    today = dt.date.today()
    interval = DateInterval(today - dt.timedelta(days=7), today)
    bars = provider.fetch(inst, "1D", interval)
    # If network/yfinance works, bars will be non-empty
    if bars:
        assert all(validate_bar(b) for b in bars)
        assert all(b.instrument_id == inst for b in bars)
