"""NSE Bhavcopy downloader and MarketDataProvider implementation (Design.md Section 12.4)."""

from __future__ import annotations

import csv
import datetime as dt
import io
import logging
import lzma
import zipfile
from pathlib import Path

import httpx

from honba.domain.bar import Bar
from honba.domain.instrument import InstrumentId
from honba.markets.india.calendar import NseCalendar
from honba.screener.coverage import DateInterval
from honba.screener.ports import validate_bar

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


def parse_bhavcopy_csv(csv_content: str, exchange: str = "NSE") -> dict[str, Bar]:
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
        inst_id = InstrumentId(symbol=raw_sym, exchange=exchange)

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
        max_workers: int = 12,
    ) -> None:
        self.cache_dir = Path(cache_dir) if cache_dir else None
        if self.cache_dir:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        self.timeout = timeout
        self.calendar = calendar or NseCalendar()
        self.max_workers = max_workers
        self._client: httpx.Client | None = None

    def _get_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            # Persistent connection pool with keep-alive
            limits = httpx.Limits(
                max_keepalive_connections=self.max_workers * 2, max_connections=self.max_workers * 4
            )
            self._client = httpx.Client(
                headers=DEFAULT_HEADERS, timeout=self.timeout, limits=limits, follow_redirects=True
            )
        return self._client

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

    def download_session_bhavcopy(
        self, session_date: dt.date, client: httpx.Client | None = None
    ) -> dict[str, Bar]:
        """Download and parse Bhavcopy for one session date (with local xz-compressed file caching)."""
        date_str = session_date.strftime("%Y%m%d")
        xz_cache_file = self.cache_dir / f"bhavcopy_{date_str}.csv.xz" if self.cache_dir else None
        csv_cache_file = self.cache_dir / f"bhavcopy_{date_str}.csv" if self.cache_dir else None

        # 1. Check compressed cache (.csv.xz)
        if xz_cache_file and xz_cache_file.exists():
            try:
                with lzma.open(xz_cache_file, mode="rt", encoding="utf-8") as f:
                    return parse_bhavcopy_csv(f.read())
            except Exception as exc:
                logger.warning("Error reading xz cache %s: %s", xz_cache_file, exc)

        # 2. Fallback to uncompressed cache (.csv) and compress to .xz for future lookup
        if csv_cache_file and csv_cache_file.exists():
            try:
                content_str = csv_cache_file.read_text(encoding="utf-8")
                # Housekeeping on read: rotate/compress legacy .csv into .csv.xz
                if xz_cache_file:
                    try:
                        with lzma.open(xz_cache_file, mode="wt", encoding="utf-8") as f:
                            f.write(content_str)
                        csv_cache_file.unlink(missing_ok=True)
                    except Exception as e:
                        logger.debug("Failed auto-compression of %s: %s", csv_cache_file, e)
                return parse_bhavcopy_csv(content_str)
            except Exception as exc:
                logger.warning("Error reading csv cache %s: %s", csv_cache_file, exc)

        http_client = client or self._get_client()
        urls = self._get_urls_for_date(session_date)
        for url in urls:
            try:
                resp = http_client.get(url)
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
                        if xz_cache_file:
                            try:
                                with lzma.open(xz_cache_file, mode="wt", encoding="utf-8") as f:
                                    f.write(csv_text)
                            except Exception as e:
                                logger.warning("Could not write xz cache %s: %s", xz_cache_file, e)
                        return parsed
            except Exception as exc:
                logger.debug("Failed to fetch %s: %s", url, exc)

        return {}

    def compress_existing_cache(self) -> tuple[int, int, int]:
        """Housekeeping: compress existing legacy .csv files in cache to .csv.xz.

        Returns:
            (migrated_count, original_bytes, compressed_bytes)
        """
        if not self.cache_dir or not self.cache_dir.exists():
            return 0, 0, 0

        migrated = 0
        total_orig = 0
        total_comp = 0

        for csv_path in sorted(self.cache_dir.glob("bhavcopy_*.csv")):
            xz_path = csv_path.with_suffix(".csv.xz")
            orig_size = csv_path.stat().st_size
            total_orig += orig_size
            try:
                raw_bytes = csv_path.read_bytes()
                comp_bytes = lzma.compress(raw_bytes)
                xz_path.write_bytes(comp_bytes)
                total_comp += len(comp_bytes)
                csv_path.unlink()
                migrated += 1
            except Exception as exc:
                logger.warning("Failed to compress %s: %s", csv_path, exc)

        return migrated, total_orig, total_comp

    def fetch(
        self,
        instrument: InstrumentId,
        timeframe: str,
        interval: DateInterval,
        progress_callback: Any = None,
    ) -> list[Bar]:
        """Fetch daily bars for instrument in [interval.start, interval.end) concurrently."""
        if timeframe.upper() not in ("1D", "D", "DAILY"):
            # Bhavcopy is EOD daily only
            return []

        # Find trading sessions in the requested interval
        trading_days: list[dt.date] = []
        scan_date = interval.start
        while scan_date < interval.end:
            if self.calendar.is_trading_day(scan_date):
                trading_days.append(scan_date)
            scan_date += dt.timedelta(days=1)

        total_sessions = len(trading_days)
        if total_sessions == 0:
            return []

        bars: list[Bar] = []
        completed_count = 0
        client = self._get_client()

        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _fetch_one_session(d: dt.date) -> tuple[dt.date, Bar | None]:
            session_bars = self.download_session_bhavcopy(d, client=client)
            bar: Bar | None = None
            if instrument.symbol in session_bars:
                candidate = session_bars[instrument.symbol]
                try:
                    validate_bar(candidate)
                    bar = candidate
                except ValueError:
                    pass
            return d, bar

        # Use thread pool to fetch sessions concurrently
        workers = min(self.max_workers, total_sessions)
        with ThreadPoolExecutor(max_workers=workers) as executor:
            future_to_date = {executor.submit(_fetch_one_session, d): d for d in trading_days}
            for future in as_completed(future_to_date):
                completed_count += 1
                sess_date, bar = future.result()
                if bar is not None:
                    bars.append(bar)
                if progress_callback:
                    progress_callback(sess_date, completed_count, total_sessions)

        return sorted(bars, key=lambda x: x.ts)
