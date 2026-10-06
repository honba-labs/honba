"""Request builders: pure functions from ergonomic arguments to an :class:`ApiRequest`.

Everything the server would reject with ``validation_invalid_request`` for a malformed
argument (timeframe grammar, time format, empty range, depth range, instrument id shape) is
rejected here first with :class:`RequestValidationError`, using the server's own field names
and reasons, so a bad call costs no round trip and an in-process and an HTTP call fail alike.

Paths are percent-encoded as they appear on the wire; times are normalised to RFC 3339 (a
string you pass is validated and sent verbatim).
"""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
from typing import Any
from urllib.parse import quote

from honba.client.errors import RequestValidationError
from honba.wire.wire import InstrumentId, UnixNanos

__all__ = [
    "MAX_DEPTH_LEVELS",
    "ApiRequest",
    "TimeLike",
    "bars",
    "depth",
    "health",
    "instrument",
    "instruments",
    "quotes",
    "verify_strategy",
]

MAX_DEPTH_LEVELS = 50
"""The most levels per side ``GET /depth/{id}`` accepts."""

TimeLike = str | date | datetime | UnixNanos
"""A point in time: RFC 3339 text, ``YYYY-MM-DD``, an aware ``datetime``, a ``date`` or a
:class:`~honba.wire.wire.UnixNanos`."""


@dataclass(frozen=True)
class ApiRequest:
    """One request: method, percent-encoded path, flat query and optional JSON body."""

    method: str
    path: str
    query: dict[str, Any] | None = None
    body: Any = None


_TIMEFRAME = re.compile(r"[1-9][0-9]*(s|m|h|d|w|mo)")
_DATE = re.compile(r"(\d{4})-(\d{2})-(\d{2})")
_DATETIME = re.compile(
    r"(\d{4})-(\d{2})-(\d{2})[Tt](\d{2}):(\d{2}):(\d{2})(?:\.(\d{1,9}))?"
    r"(?:[Zz]|([+-])(\d{2}):(\d{2}))"
)
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _non_empty(field: str, value: object) -> str:
    if not isinstance(value, str) or not value.strip():
        raise RequestValidationError(field, "invalid_value", f"{field} must be a non-empty string")
    return value


def health() -> ApiRequest:
    return ApiRequest("GET", "/health")


def instruments(*, exchange: str | None = None, symbol: str | None = None) -> ApiRequest:
    query: dict[str, Any] = {}
    if exchange is not None:
        query["exchange"] = _non_empty("exchange", exchange)
    if symbol is not None:
        query["symbol"] = _non_empty("symbol", symbol)
    return ApiRequest("GET", "/instruments", query or None)


def _id_path_segment(value: object) -> str:
    """``SYMBOL.EXCHANGE`` (split on the last dot), percent-encoded for a path segment."""
    if isinstance(value, str):
        text = value
    elif hasattr(value, "symbol") and hasattr(value, "exchange"):
        text = f"{value.symbol}.{value.exchange}"
    else:
        text = ""
    symbol, dot, exchange = text.rpartition(".")
    if not dot or not symbol or not exchange or text != text.strip():
        raise RequestValidationError(
            "id", "invalid_instrument_id", f"instrument id {value!r} is not SYMBOL.EXCHANGE"
        )
    return quote(text, safe=".")


def instrument(instrument_id: str | InstrumentId) -> ApiRequest:
    return ApiRequest("GET", f"/instruments/{_id_path_segment(instrument_id)}")


_MAX_NS = 2**63 - 1
"""Latest instant the server accepts: chrono's ``timestamp_nanos_opt`` is an ``i64``
(2262-04-11T23:47:16.854775807Z); anything later is ``invalid_time``."""


def _to_ns(field: str, value: object) -> tuple[str, int]:
    """Validate a time and return ``(text to send, nanoseconds since the epoch)``."""
    text, ns = _to_ns_unbounded(field, value)
    if ns > _MAX_NS:
        raise RequestValidationError(
            field,
            "invalid_time",
            f"{field} {value!r} is beyond the representable range (before 2262-04-12)",
        )
    return text, ns


def _to_ns_unbounded(field: str, value: object) -> tuple[str, int]:
    def bad(reason: str, message: str) -> RequestValidationError:
        return RequestValidationError(field, reason, message)

    if isinstance(value, UnixNanos):
        return value.iso, value.to_ns()
    if isinstance(value, datetime):
        if value.tzinfo is None or value.utcoffset() is None:
            raise bad("naive_datetime", f"{field} must be timezone-aware")
        try:
            delta = value.astimezone(timezone.utc) - _EPOCH
        except OverflowError:
            raise bad("invalid_time", f"{field} {value!r} is out of range") from None
        ns = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + delta.microseconds * 1000
        if ns < 0:
            raise bad("before_epoch", f"{field} {value!r} is before 1970-01-01")
        return UnixNanos.from_ns(ns).iso, ns
    if isinstance(value, date):
        return _to_ns(field, value.isoformat())
    if not isinstance(value, str):
        raise bad("invalid_time", f"{field} {value!r} is not a time")
    try:
        if (m := _DATE.fullmatch(value)) is not None:
            moment = datetime(int(m[1]), int(m[2]), int(m[3]), tzinfo=timezone.utc)
            nanos = 0
        elif (m := _DATETIME.fullmatch(value)) is not None:
            offset = timedelta(0)
            if m[8] is not None:
                offset = timedelta(hours=int(m[9]), minutes=int(m[10]))
                if int(m[9]) > 23 or int(m[10]) > 59:
                    raise ValueError("offset")
                if m[8] == "-":
                    offset = -offset
            moment = datetime(
                *(int(m[i]) for i in range(1, 7)),
                tzinfo=timezone(offset),
            )
            nanos = int((m[7] or "").ljust(9, "0"))
        else:
            raise ValueError("format")
    except ValueError:
        raise bad(
            "invalid_time",
            f"{field} {value!r} is not an RFC3339 time or a YYYY-MM-DD date",
        ) from None
    try:
        delta = moment.astimezone(timezone.utc) - _EPOCH
    except OverflowError:
        raise bad("invalid_time", f"{field} {value!r} is out of range") from None
    ns = (delta.days * 86_400 + delta.seconds) * 1_000_000_000 + nanos
    if ns < 0:
        raise bad("before_epoch", f"{field} {value!r} is before 1970-01-01")
    return value, ns


def bars(
    instrument_id: str | InstrumentId,
    *,
    tf: str | None = None,
    from_: TimeLike | None = None,
    to: TimeLike | None = None,
) -> ApiRequest:
    path = f"/bars/{_id_path_segment(instrument_id)}"
    query: dict[str, Any] = {}
    if tf is not None:
        if not isinstance(tf, str) or _TIMEFRAME.fullmatch(tf) is None:
            raise RequestValidationError(
                "tf",
                "invalid_timeframe",
                f"timeframe {tf!r} is not <n><s|m|h|d|w|mo>, e.g. 1m or 1d",
            )
        query["tf"] = tf
    start = _to_ns("from", from_) if from_ is not None else None
    end = _to_ns("to", to) if to is not None else None
    if start is not None:
        query["from"] = start[0]
    if end is not None:
        query["to"] = end[0]
    if start is not None and end is not None and start[1] >= end[1]:
        raise RequestValidationError("to", "empty_range", "`from` must be strictly before `to`")
    return ApiRequest("GET", path, query or None)


def quotes(
    symbols: str | Sequence[str],
    *,
    venue: str | None = None,
    as_of: TimeLike | None = None,
) -> ApiRequest:
    items = [symbols] if isinstance(symbols, str) else list(symbols)
    if not items:
        raise RequestValidationError("symbols", "missing_symbols", "at least one symbol is needed")
    for item in items:
        if not isinstance(item, str) or not item.strip() or "," in item:
            raise RequestValidationError(
                "symbols",
                "invalid_symbol",
                f"symbol {item!r} must be a non-empty string without a comma",
            )
    query: dict[str, Any] = {"symbols": ",".join(items)}
    if venue is not None:
        query["venue"] = _non_empty("venue", venue)
    if as_of is not None:
        query["as_of"] = _to_ns("as_of", as_of)[0]
    return ApiRequest("GET", "/quotes", query)


def depth(instrument_id: str | InstrumentId, *, levels: int | None = None) -> ApiRequest:
    path = f"/depth/{_id_path_segment(instrument_id)}"
    if levels is None:
        return ApiRequest("GET", path)
    if isinstance(levels, bool) or not isinstance(levels, int) or not 1 <= levels <= 50:
        raise RequestValidationError(
            "depth", "out_of_range", f"levels must be an integer in 1..={MAX_DEPTH_LEVELS}"
        )
    return ApiRequest("GET", path, {"depth": levels})


def verify_strategy(manifest: Any) -> ApiRequest:
    """``manifest`` is a ``StrategyManifest`` or a JSON-shaped, non-empty mapping."""
    if hasattr(manifest, "to_json_dict"):
        body = manifest.to_json_dict()
    elif isinstance(manifest, Mapping) and manifest:
        body = dict(manifest)
    else:
        raise RequestValidationError(
            "manifest",
            "invalid_manifest",
            "manifest must be a StrategyManifest or a non-empty dict",
        )
    return ApiRequest("POST", "/strategies/verify", body=body)
