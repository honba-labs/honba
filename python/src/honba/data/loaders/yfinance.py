"""yfinance MarketDataProvider implementation (Design.md Section 12.4).

Provides market data loading for Indian equities (NSE/BSE), indices, and global assets
across daily and intraday timeframes (1m, 5m, 15m, 30m, 1h, 1D).
"""

from __future__ import annotations

import datetime as dt
import logging
import math
import time
from collections.abc import Sequence
from typing import Any

import pandas as pd
import yfinance as yf

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.screener.coverage import DateInterval
from honba.screener.ports import MarketDataProvider, validate_bar

logger = logging.getLogger(__name__)

# Standard index mappings for Indian markets
DEFAULT_INDEX_MAP: dict[str, str] = {
    "NIFTY50": "^NSEI",
    "NIFTY 50": "^NSEI",
    "NIFTY": "^NSEI",
    "BANKNIFTY": "^NSEBANK",
    "NIFTY BANK": "^NSEBANK",
    "NIFTYIT": "^CNXIT",
    "NIFTY IT": "^CNXIT",
    "SENSEX": "^BSESN",
    "BSE SENSEX": "^BSESN",
}

# Inverse index mappings
REVERSE_INDEX_MAP: dict[str, InstrumentId] = {
    "^NSEI": InstrumentId("NIFTY50", "NSE"),
    "^NSEBANK": InstrumentId("BANKNIFTY", "NSE"),
    "^CNXIT": InstrumentId("NIFTYIT", "NSE"),
    "^BSESN": InstrumentId("SENSEX", "BSE"),
}

# Timeframe normalization to yfinance intervals
TIMEFRAME_MAP: dict[str, str] = {
    "1D": "1d",
    "1d": "1d",
    "D": "1d",
    "d": "1d",
    "DAILY": "1d",
    "daily": "1d",
    "1M": "1mo",
    "1MO": "1mo",
    "1mo": "1mo",
    "MONTHLY": "1mo",
    "monthly": "1mo",
    "1W": "1wk",
    "1w": "1wk",
    "1WK": "1wk",
    "WEEKLY": "1wk",
    "weekly": "1wk",
    "1MIN": "1m",
    "1min": "1m",
    "1M_INTRADAY": "1m",
    "1m": "1m",
    "2M": "2m",
    "2m": "2m",
    "2min": "2m",
    "5M": "5m",
    "5m": "5m",
    "5min": "5m",
    "15M": "15m",
    "15m": "15m",
    "15min": "15m",
    "30M": "30m",
    "30m": "30m",
    "30min": "30m",
    "60M": "60m",
    "60m": "60m",
    "60min": "60m",
    "1H": "60m",
    "1h": "60m",
    "90M": "90m",
    "90m": "90m",
    "90min": "90m",
}

# Maximum chunk size in days per request to prevent yfinance truncation or timeouts
MAX_CHUNK_DAYS: dict[str, int] = {
    "1m": 7,     # yfinance allows max 7 days per 1m call
    "2m": 50,
    "5m": 50,
    "15m": 50,
    "30m": 50,
    "60m": 50,
    "90m": 50,
    "1d": 3650,  # 10 years per chunk for daily
    "1wk": 7300,
    "1mo": 7300,
}

# Indian Standard Time offset
IST_TZ = dt.timezone(dt.timedelta(hours=5, minutes=30), name="IST")


def to_yfinance_symbol(
    instrument: InstrumentId | str,
    exchange: str = "NSE",
    custom_map: dict[str, str] | None = None,
) -> str:
    """Translate an InstrumentId or symbol string to a yfinance ticker.
    
    Examples:
        InstrumentId("RELIANCE", "NSE") -> "RELIANCE.NS"
        InstrumentId("TCS", "BSE")      -> "TCS.BO"
        InstrumentId("NIFTY50", "NSE")  -> "^NSEI"
        InstrumentId("AAPL", "NASDAQ")  -> "AAPL"
    """
    if isinstance(instrument, str):
        sym = instrument.strip()
        v = exchange.upper()
    else:
        sym = instrument.symbol.strip()
        v = instrument.exchange.upper()

    if custom_map and sym in custom_map:
        return custom_map[sym]

    # Predefined indices
    sym_upper = sym.upper()
    if sym_upper in DEFAULT_INDEX_MAP:
        return DEFAULT_INDEX_MAP[sym_upper]

    # Already formatted tickers
    if sym.startswith("^") or sym.endswith(".NS") or sym.endswith(".BO"):
        return sym

    if v == "NSE":
        return f"{sym}.NS"
    elif v == "BSE":
        return f"{sym}.BO"

    # Default for US / global markets
    return sym


def from_yfinance_symbol(
    yf_symbol: str,
    default_exchange: str = "NSE",
) -> InstrumentId:
    """Translate a yfinance ticker back to an InstrumentId."""
    ticker = yf_symbol.strip()
    if ticker in REVERSE_INDEX_MAP:
        return REVERSE_INDEX_MAP[ticker]

    if ticker.endswith(".NS"):
        return InstrumentId(ticker[:-3], "NSE")
    elif ticker.endswith(".BO"):
        return InstrumentId(ticker[:-3], "BSE")

    return InstrumentId(ticker, default_exchange)


def normalize_timeframe(timeframe: str) -> str:
    """Convert arbitrary timeframe string into yfinance interval string."""
    tf_clean = timeframe.strip()
    mapped = TIMEFRAME_MAP.get(tf_clean) or TIMEFRAME_MAP.get(tf_clean.upper())
    if not mapped:
        raise ValueError(
            f"Unsupported timeframe {timeframe!r}. Supported timeframes: {sorted(set(TIMEFRAME_MAP.keys()))}"
        )
    return mapped


def dataframe_to_bars(
    df: pd.DataFrame,
    instrument: InstrumentId,
    timeframe: str,
    interval: DateInterval | None = None,
) -> list[Bar]:
    """Convert a yfinance pandas DataFrame with OHLCV data into a list of Honba Bars."""
    if df.empty:
        return []

    yf_interval = normalize_timeframe(timeframe)
    is_daily_or_longer = yf_interval in ("1d", "1wk", "1mo")
    is_india = instrument.exchange.upper() in ("NSE", "BSE")

    # Required column check
    required_cols = {"Open", "High", "Low", "Close", "Volume"}
    if not required_cols.issubset(df.columns):
        # Check lowercase variants
        col_map = {c.lower(): c for c in df.columns}
        if not {"open", "high", "low", "close", "volume"}.issubset(col_map.keys()):
            logger.warning("Missing required OHLCV columns in DataFrame: %s", df.columns.tolist())
            return []
        df = df.rename(columns={
            col_map["open"]: "Open",
            col_map["high"]: "High",
            col_map["low"]: "Low",
            col_map["close"]: "Close",
            col_map["volume"]: "Volume",
        })

    # Nano timestamps calculation
    start_ns = 0
    end_ns = 0
    if interval is not None:
        start_ns = int(dt.datetime.combine(interval.start, dt.time.min).timestamp() * 1e9)
        end_ns = int(dt.datetime.combine(interval.end, dt.time.min).timestamp() * 1e9)

    bars_by_ts: dict[int, Bar] = {}

    for idx, row in df.iterrows():
        try:
            op = float(row["Open"])
            hi = float(row["High"])
            lo = float(row["Low"])
            cl = float(row["Close"])
            vo = float(row["Volume"])
        except (ValueError, TypeError):
            continue

        if not (math.isfinite(op) and math.isfinite(hi) and math.isfinite(lo) and math.isfinite(cl) and math.isfinite(vo)):
            continue

        # Clamp minor precision artifacts where hi < max(op, cl) or lo > min(op, cl)
        hi = max(hi, op, cl)
        lo = min(lo, op, cl)
        if vo < 0:
            vo = 0.0

        # Timestamp derivation
        if isinstance(idx, pd.Timestamp):
            if is_daily_or_longer and is_india:
                # Align daily Indian bars to 09:15:00 IST session open
                bar_date = idx.date()
                dt_bar = dt.datetime.combine(bar_date, dt.time(9, 15), tzinfo=IST_TZ)
                ts = int(dt_bar.timestamp() * 1e9)
            else:
                ts = int(idx.timestamp() * 1e9)
        elif isinstance(idx, (dt.datetime, dt.date)):
            if is_daily_or_longer and is_india:
                bar_date = idx if isinstance(idx, dt.date) and not isinstance(idx, dt.datetime) else idx.date()
                dt_bar = dt.datetime.combine(bar_date, dt.time(9, 15), tzinfo=IST_TZ)
                ts = int(dt_bar.timestamp() * 1e9)
            elif isinstance(idx, dt.datetime):
                ts = int(idx.timestamp() * 1e9)
            else:
                dt_bar = dt.datetime.combine(idx, dt.time.min)
                ts = int(dt_bar.timestamp() * 1e9)
        else:
            continue

        # Check date interval filter [start_ns, end_ns)
        if interval is not None and not (start_ns <= ts < end_ns):
            continue

        bar = Bar(
            instrument_id=instrument,
            ts=ts,
            open=op,
            high=hi,
            low=lo,
            close=cl,
            volume=vo,
        )

        try:
            validate_bar(bar)
            bars_by_ts[ts] = bar
        except ValueError as exc:
            logger.debug("Skipping invalid bar at ts=%s: %s", ts, exc)

    return [bars_by_ts[t] for t in sorted(bars_by_ts.keys())]


def _chunk_interval(
    start: dt.date,
    end: dt.date,
    chunk_days: int,
) -> list[tuple[dt.date, dt.date]]:
    """Divide [start, end) into slices of max length chunk_days."""
    if start >= end:
        return []
    chunks: list[tuple[dt.date, dt.date]] = []
    curr = start
    while curr < end:
        nxt = min(curr + dt.timedelta(days=chunk_days), end)
        chunks.append((curr, nxt))
        curr = nxt
    return chunks


class YFinanceProvider:
    """MarketDataProvider implementation for fetching historical market data via yfinance.
    
    Supports:
    - NSE and BSE equities with automatic '.NS' / '.BO' ticker resolution
    - Indian indices (NIFTY50, BANKNIFTY, SENSEX, etc.)
    - Global stocks and ETFs
    - Intraday intervals (1m, 5m, 15m, 30m, 1h) and EOD daily bars (1D)
    - Automatic request chunking for intraday queries
    - Rate limit throttling and retries with exponential backoff
    """

    def __init__(
        self,
        symbol_map: dict[str, str] | None = None,
        rate_limit_pause: float = 0.2,
        max_retries: int = 3,
        auto_adjust: bool = False,
    ) -> None:
        self.symbol_map = symbol_map or {}
        self.rate_limit_pause = rate_limit_pause
        self.max_retries = max_retries
        self.auto_adjust = auto_adjust

    @property
    def name(self) -> str:
        return "yfinance"

    def fetch(
        self,
        instrument: InstrumentId,
        timeframe: str,
        interval: DateInterval,
        progress_callback: Any = None,
    ) -> list[Bar]:
        """Fetch bars for instrument in [interval.start, interval.end) via yfinance."""
        if interval.start >= interval.end:
            return []

        yf_interval = normalize_timeframe(timeframe)
        chunk_days = MAX_CHUNK_DAYS.get(yf_interval, 50)
        chunks = _chunk_interval(interval.start, interval.end, chunk_days)
        if not chunks:
            return []

        ticker_sym = to_yfinance_symbol(instrument, custom_map=self.symbol_map)
        ticker = yf.Ticker(ticker_sym)

        all_bars: dict[int, Bar] = {}
        total_chunks = len(chunks)

        for chunk_idx, (chunk_start, chunk_end) in enumerate(chunks, 1):
            chunk_bars = self._fetch_chunk(
                ticker=ticker,
                instrument=instrument,
                timeframe=timeframe,
                yf_interval=yf_interval,
                chunk_start=chunk_start,
                chunk_end=chunk_end,
                interval=interval,
            )
            for b in chunk_bars:
                all_bars[b.ts] = b

            if progress_callback:
                progress_callback(chunk_end, chunk_idx, total_chunks)

            if chunk_idx < total_chunks and self.rate_limit_pause > 0:
                time.sleep(self.rate_limit_pause)

        return [all_bars[t] for t in sorted(all_bars.keys())]

    def _fetch_chunk(
        self,
        ticker: yf.Ticker,
        instrument: InstrumentId,
        timeframe: str,
        yf_interval: str,
        chunk_start: dt.date,
        chunk_end: dt.date,
        interval: DateInterval,
    ) -> list[Bar]:
        """Download one chunk with retry logic."""
        # yfinance end parameter is exclusive for dates
        start_str = chunk_start.strftime("%Y-%m-%d")
        end_str = chunk_end.strftime("%Y-%m-%d")

        last_error: Exception | None = None
        for attempt in range(1, self.max_retries + 1):
            try:
                df = ticker.history(
                    start=start_str,
                    end=end_str,
                    interval=yf_interval,
                    auto_adjust=self.auto_adjust,
                )
                if df is not None and not df.empty:
                    return dataframe_to_bars(
                        df=df,
                        instrument=instrument,
                        timeframe=timeframe,
                        interval=interval,
                    )
                return []
            except Exception as exc:
                last_error = exc
                logger.debug(
                    "Attempt %d/%d failed for %s [%s, %s]: %s",
                    attempt,
                    self.max_retries,
                    instrument,
                    start_str,
                    end_str,
                    exc,
                )
                if attempt < self.max_retries:
                    time.sleep(self.rate_limit_pause * (2 ** (attempt - 1)))

        if last_error:
            logger.warning("Failed fetching %s [%s, %s] after %d attempts: %s", instrument, start_str, end_str, self.max_retries, last_error)
        return []

    def fetch_df(
        self,
        symbol_or_instrument: str | InstrumentId,
        timeframe: str = "1D",
        start: dt.date | str | None = None,
        end: dt.date | str | None = None,
        exchange: str = "NSE",
    ) -> pd.DataFrame:
        """Convenience method returning a raw pandas DataFrame for research or exploration."""
        if isinstance(symbol_or_instrument, InstrumentId):
            inst = symbol_or_instrument
        else:
            inst = InstrumentId(symbol_or_instrument, exchange)

        start_date = dt.date.fromisoformat(start) if isinstance(start, str) else (start or dt.date(2020, 1, 1))
        end_date = dt.date.fromisoformat(end) if isinstance(end, str) else (end or dt.date.today() + dt.timedelta(days=1))

        ticker_sym = to_yfinance_symbol(inst, custom_map=self.symbol_map)
        yf_interval = normalize_timeframe(timeframe)
        ticker = yf.Ticker(ticker_sym)

        return ticker.history(
            start=start_date.strftime("%Y-%m-%d"),
            end=end_date.strftime("%Y-%m-%d"),
            interval=yf_interval,
            auto_adjust=self.auto_adjust,
        )

    def fetch_batch(
        self,
        instruments: Sequence[InstrumentId],
        timeframe: str,
        interval: DateInterval,
        progress_callback: Any = None,
    ) -> dict[InstrumentId, list[Bar]]:
        """Fetch bars for multiple instruments sequentially."""
        results: dict[InstrumentId, list[Bar]] = {}
        total = len(instruments)
        for i, inst in enumerate(instruments, 1):
            bars = self.fetch(inst, timeframe, interval)
            results[inst] = bars
            if progress_callback:
                progress_callback(inst, i, total)
        return results
