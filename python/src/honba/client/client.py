"""The typed :class:`Client` over a :class:`~honba.client.transport.Transport`."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any, TypeVar

from pydantic import BaseModel, ValidationError
from typing_extensions import Self

from honba._native import native_attr
from honba.client import requests as rq
from honba.client.errors import InvalidResponseError, error_from_envelope
from honba.client.models import Depth, Health, InstrumentInfo
from honba.client.transport import HttpTransport, InprocTransport, Transport
from honba.wire.wire import Bar, InstrumentId, QuoteTick

__all__ = ["Client"]

_M = TypeVar("_M", bound=BaseModel)


class Client:
    """Typed access to the REST read API, independent of how requests travel.

    Methods mirror the served endpoints and return wire models (timestamps are
    :class:`~honba.wire.wire.UnixNanos`). A bad argument raises
    :class:`~honba.client.RequestValidationError` before anything is sent; a failed call raises
    an :class:`~honba.client.ApiError` subclass built from the error envelope.
    """

    def __init__(self, transport: Transport) -> None:
        self._transport = transport

    @classmethod
    def http(cls, base_url: str, **options: Any) -> Client:
        """A client for a running ``honba serve``; ``options`` go to ``HttpTransport``."""
        return cls(HttpTransport(base_url, **options))

    @classmethod
    def inproc(cls, data_dir: str | Path) -> Client:
        """A client that runs the Rust router in process over a Parquet directory."""
        return cls(InprocTransport(data_dir))

    @property
    def transport(self) -> Transport:
        return self._transport

    def close(self) -> None:
        """Release the transport's resources, if it has any."""
        close = getattr(self._transport, "close", None)
        if callable(close):
            close()

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- endpoints ---------------------------------------------------------------------------

    def health(self) -> Health:
        """``GET /health``."""
        return self._call(rq.health(), Health)

    def instruments(
        self, *, exchange: str | None = None, symbol: str | None = None
    ) -> list[InstrumentInfo]:
        """``GET /instruments``: every instrument passing the filters, in id order."""
        data = self._data(rq.instruments(exchange=exchange, symbol=symbol))
        return self._parse_list(data, "instruments", InstrumentInfo)

    def instrument(self, instrument_id: str | InstrumentId) -> InstrumentInfo:
        """``GET /instruments/{id}`` (``SYMBOL.EXCHANGE``); ``NotFoundApiError`` if unknown."""
        return self._call(rq.instrument(instrument_id), InstrumentInfo)

    def bars(
        self,
        instrument_id: str | InstrumentId,
        *,
        tf: str | None = None,
        from_: rq.TimeLike | None = None,
        to: rq.TimeLike | None = None,
    ) -> list[Bar]:
        """``GET /bars/{id}``: bars in ascending ``ts_event`` over ``[from_, to)``.

        ``tf`` is ``<n><s|m|h|d|w|mo>`` (server default ``1m``).
        """
        data = self._data(rq.bars(instrument_id, tf=tf, from_=from_, to=to))
        return self._parse_list(data, "bars", Bar)

    def quotes(
        self,
        symbols: str | Sequence[str],
        *,
        venue: str | None = None,
        as_of: rq.TimeLike | None = None,
    ) -> list[QuoteTick]:
        """``GET /quotes``: the latest (or ``as_of``, inclusive) quote of each symbol's
        instruments, in instrument-id order."""
        data = self._data(rq.quotes(symbols, venue=venue, as_of=as_of))
        return self._parse_list(data, "quotes", QuoteTick)

    def depth(self, instrument_id: str | InstrumentId, *, levels: int | None = None) -> Depth:
        """``GET /depth/{id}``: the order book (``levels`` per side, 1..=50, default 5).

        A bar-only data source has no book: ``MarketDataUnavailableApiError``.
        """
        return self._call(rq.depth(instrument_id, levels=levels), Depth)

    def verify_strategy(self, manifest: Any) -> dict[str, Any]:
        """``POST /strategies/verify``: the verified IR (a JSON-shaped record).

        ``ValidationApiError`` with ``context["reason"]`` set to the stable code if the
        manifest does not compile.
        """
        data = self._data(rq.verify_strategy(manifest))
        if not isinstance(data, dict):
            raise InvalidResponseError("verify payload is not an object", status=200)
        return data

    # -- plumbing ----------------------------------------------------------------------------

    def _data(self, request: rq.ApiRequest) -> Any:
        """Send ``request``; return the envelope's ``data`` or raise the mapped error."""
        response = self._transport.request(
            request.method, request.path, query=request.query, body=request.body
        )
        body = response.json
        if not 200 <= response.status < 300:
            raise error_from_envelope(response.status, body)
        if (
            not isinstance(body, Mapping)
            or body.get("error") is not None
            or body.get("data") is None
        ):
            raise InvalidResponseError(
                "success response without a data envelope", status=response.status
            )
        self._check_schema_version(body, response.status)
        return body["data"]

    @staticmethod
    def _check_schema_version(body: Mapping[str, Any], status: int) -> None:
        """Reject an envelope of a different wire version (ADR 0012) when it can be known.

        The expected version lives in the native extension; without it the check is skipped,
        so the HTTP client still works in a source-only checkout.
        """
        try:
            expected = native_attr("SCHEMA_VERSION")
        except (ImportError, RuntimeError):
            return
        if body.get("schema_version") != expected:
            raise InvalidResponseError(
                f"envelope schema_version {body.get('schema_version')!r} != {expected}",
                status=status,
            )

    def _call(self, request: rq.ApiRequest, model: type[_M]) -> _M:
        data = self._data(request)
        try:
            return model.model_validate(data)
        except ValidationError as exc:
            raise InvalidResponseError(
                f"{model.__name__} payload does not parse: {exc.error_count()} errors", status=200
            ) from exc

    @staticmethod
    def _parse_list(data: Any, key: str, model: type[_M]) -> list[_M]:
        rows = data.get(key) if isinstance(data, Mapping) else None
        if not isinstance(rows, list):
            raise InvalidResponseError(f"payload has no {key!r} list", status=200)
        try:
            return [model.model_validate(row) for row in rows]
        except ValidationError as exc:
            raise InvalidResponseError(
                f"{model.__name__} payload does not parse: {exc.error_count()} errors", status=200
            ) from exc
