"""NSE Bhavcopy downloader and MarketDataProvider implementation (Design.md Section 12.4)."""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import zipfile
from collections.abc import Iterable
from pathlib import Path
from typing import BinaryIO

import httpx

from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.india.calendar import NseCalendar
from honba.screener.coverage import DateInterval
from honba.screener.ports import MarketDataProvider, validate_bar

logger = logging.getLogger(__name__)

# Primary User-Agent headers to access NSE public archives
DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.5",
}


def parse_bhavcopy_csv(csv_content: str, venue: str = "NSE") -> dict[str, Bar]:
    """Parse NSE Bhavcopy CSV text and return map of symbol -> Bar."""
    f = io.StringIO(csv_content)
    reader = csv.DictReader(f)
    if not reader.fieldnames:
        return {}

    # Normalise header keys (strip whitespace, uppercase)
    field_map = {fn.strip().upper(): fn for fn in reader.fieldnames if fn}

    # Identify format:
    # 1. UDiFF format: TradDt, TckrSymb, SctySrs, OpnPric, HghPric, LwPric, ClsPric, TtlTradQty
    # 2. Sec Bhavdata: DATE1, SYMBOL, SERIES, OPEN_PRICE, HIGH_PRICE, LOW_PRICE, CLOSE_PRICE, TTL_TRD_QNTY
    # 3. Old Bhavcopy: TIMESTAMP, SYMBOL, SERIES, OPEN, HIGH, LOW, CLOSE, TOTTRDQTY

    is_udiff = "TCKRSYMB" in field_map
    is_sec = "SYMBOL" in field_map and "OPEN_PRICE" in field_map

    sym_col = field_map.get("TCKRSYMB") or field_map.get("SYMBOL")
    series_col = field_map.get("SCTYSRS") or field_map.get("SERIES")
    open_col = field_map.get("OPNPRIC") or field_map.get("OPEN_PRICE") or field_map.get("OPEN")
    high_col = field_map.get("HGHPRIC") or field_map.get("HIGH_PRICE") or field_map.get("HIGH")
    low_col = field_map.get("LWPRIC") or field_map.get("LOW_PRICE") or field_map.get("LOW")
    close_col = field_map.get("CLSPRIC") or field_map.get("CLOSE_PRICE") or field_map.get("CLOSE")
    qty_col = (
        field_map.get("TTLTRADGVOL")
        or field_map.get("TTLTRADQTY")
        or field_map.get("TTL_TRD_QNTY")
        or field_map.get("TOTTRDQTY")
    )
    date_col = field_map.get("TRADDT") or field_map.get("DATE1") or field_map.get("TIMESTAMP")

    if not all([sym_col, open_col, high_col, low_col, close_col, qty_col]):
        # Suppress logging if the content was an XML or binary payload
        first_hdr = str(reader.fieldnames[0]) if reader.fieldnames else ""
        if not first_hdr.startswith(("PK", "<", "<?", "{")):
            logger.debug("Unrecognized bhavcopy header format: %s", reader.fieldnames)
        return {}

    bars: dict[str, Bar] = {}

    for row in reader:
        # Keep primary equity series (EQ / BE)
        series = (row.get(series_col) or "").strip().upper() if series_col else "EQ"
        if series not in ("EQ", "BE", "BZ"):
            continue

        raw_sym = (row.get(sym_col) or "").strip()
        if not raw_sym:
            continue

        try:
            op = float(row[open_col])
            hi = float(row[high_col])
            lo = float(row[low_col])
            cl = float(row[close_col])
            vol = float(row[qty_col])
        except (ValueError, TypeError, KeyError):
            continue

        # In case of data anomalies (hi < lo or cl outside hi/lo), clamp safely
        hi = max(hi, op, cl)
        lo = min(lo, op, cl)
        if vol < 0:
            vol = 0.0

        raw_date_str = (row.get(date_col) or "").strip() if date_col else ""
        bar_date = _parse_date(raw_date_str) or dt.date.today()

        ts = int(dt.datetime.combine(bar_date, dt.time(9, 15)).timestamp() * 1e9)
        inst_id = InstrumentId(symbol=raw_sym, venue=venue)

        b = Bar(
            instrument_id=inst_id,
            ts=ts,
            open=op,
            high=hi,
            low=lo,
            close=cl,
            volume=vol,
        )
        bars[raw_sym] = b

    return bars


def _parse_date(raw: str) -> dt.date | None:
    if not raw:
        return None
    for fmt in ("%Y-%m-%d", "%d-%b-%Y", "%d-%m-%Y", "%Y%m%d"):
        try:
            return dt.datetime.strptime(raw, fmt).date()
        except ValueError:
            pass
    return None


class NseBhavcopyProvider:
    """Fetches official daily EOD Bhavcopies from NSE India public archives."""

    def __init__(
        self,
        cache_dir: str | Path | None = None,
        timeout: float = 15.0,
        calendar: NseCalendar | None = None,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.calendar = calendar or NseCalendar()

    @property
    def name(self) -> str:
        return "nse_bhavcopy"

    def _get_urls_for_date(self, d: dt.date) -> list[str]:
        """Generate candidate download URLs for a given trading session.

        Supports:
        1. Modern UDiFF CM Bhavcopy zip (2024+)
        2. Classic historical CM Bhavcopy zip from archives.nseindia.com (< 2024)
        3. Sec Bhavdata full CSV from archives / nsearchives
        """
        ymd = d.strftime("%Y%m%d")
        dmy = d.strftime("%d%m%Y")
        year = d.strftime("%Y")
        mon = d.strftime("%b").upper()
        cm_dmy = d.strftime("%d%b%Y").upper()

        return [
            # 1. Modern UDiFF CM Bhavcopy zip
            f"https://nsearchives.nseindia.com/content/cm/BhavCopy_NSE_CM_0_0_0_{ymd}_F_0000.csv.zip",
            # 2. Historical CM Bhavcopy zip (compressed, fast, works across historical archives)
            f"https://archives.nseindia.com/content/historical/EQUITIES/{year}/{mon}/cm{cm_dmy}bhav.csv.zip",
            f"https://nsearchives.nseindia.com/content/historical/EQUITIES/{year}/{mon}/cm{cm_dmy}bhav.csv.zip",
            # 3. Direct Sec Bhavdata full CSV
            f"https://nsearchives.nseindia.com/products/content/sec_bhavdata_full_{dmy}.csv",
            f"https://archives.nseindia.com/products/content/sec_bhavdata_full_{dmy}.csv",
        ]

    def download_session_bhavcopy(self, session_date: dt.date) -> dict[str, Bar]:
        """Download and parse Bhavcopy for one session date (with local file caching)."""
        cache_file = self.cache_dir / f"bhavcopy_{session_date.strftime('%Y%m%d')}.csv" if self.cache_dir else None
        if cache_file and cache_file.exists():
            return parse_bhavcopy_csv(cache_file.read_text(encoding="utf-8"))

        urls = self._get_urls_for_date(session_date)
        for url in urls:
            try:
                with httpx.Client(headers=DEFAULT_HEADERS, timeout=self.timeout, follow_redirects=True) as client:
                    resp = client.get(url)
                    if resp.status_code != 200:
                        continue

                    content = resp.content
                    csv_text = ""

                    # Check for zip magic bytes 'PK\x03\x04' regardless of URL extension
                    is_zip = url.endswith(".zip") or content.startswith(b"PK\x03\x04")

                    if is_zip:
                        try:
                            with zipfile.ZipFile(io.BytesIO(content)) as z:
                                for name in z.namelist():
                                    if name.lower().endswith(".csv"):
                                        csv_text = z.read(name).decode("utf-8", errors="ignore")
                                        break
                        except zipfile.BadZipFile:
                            continue
                    else:
                        # Ensure it's not a binary or HTML/XML error page before parsing
                        if not content.startswith((b"<!DOCTYPE", b"<html", b"<?xml")):
                            csv_text = content.decode("utf-8", errors="ignore")

                    if csv_text:
                        parsed = parse_bhavcopy_csv(csv_text)
                        if parsed:
                            if cache_file:
                                cache_file.write_text(csv_text, encoding="utf-8")
                            return parsed
            except Exception as exc:
                logger.debug("Failed to fetch %s: %s", url, exc)

        return {}

    def fetch(
        self,
        instrument: InstrumentId,
        timeframe: str,
        interval: DateInterval,
        progress_callback: Any = None,
    ) -> list[Bar]:
        """Fetch daily bars for instrument in [interval.start, interval.end)."""
        if timeframe.upper() not in ("1D", "D", "DAILY"):
            # Bhavcopy is EOD daily only
            return []

        # Find trading sessions in the requested interval
        cur = interval.start
        bars: list[Bar] = []

        # Collect trading days first to support accurate progress reporting
        trading_days: list[dt.date] = []
        scan_date = interval.start
        while scan_date < interval.end:
            if self.calendar.is_trading_day(scan_date):
                trading_days.append(scan_date)
            scan_date += dt.timedelta(days=1)

        total_sessions = len(trading_days)
        for idx, session_date in enumerate(trading_days, start=1):
            if progress_callback:
                progress_callback(session_date, idx, total_sessions)

            session_bars = self.download_session_bhavcopy(session_date)
            if instrument.symbol in session_bars:
                b = session_bars[instrument.symbol]
                try:
                    validate_bar(b)
                    bars.append(b)
                except ValueError:
                    pass

        return sorted(bars, key=lambda x: x.ts)
