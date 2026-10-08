"""Transports: how a request reaches the REST read API.

A :class:`Transport` turns ``(method, path, query, body)`` into a :class:`Response`
(HTTP-style status plus parsed JSON, or ``None`` for an empty / non-JSON body). Two
implementations answer identically:

* :class:`HttpTransport` talks to a running ``honba serve`` over HTTP (httpx, already a
  dependency).
* :class:`InprocTransport` calls the same Rust router in process through
  ``honba._honba.api_request``; no socket, no server, no Python handler logic.

Transports never interpret the envelope except to honour ``error.retryable``.
"""

from __future__ import annotations

import json
import math
import time
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

import httpx

from honba._native import native_attr
from honba.client.errors import TransportApiError

__all__ = ["HttpTransport", "InprocTransport", "Response", "RetryPolicy", "Transport"]


@dataclass(frozen=True)
class Response:
    """An HTTP-style answer: ``status`` and the parsed JSON body (``None`` if empty/not JSON)."""

    status: int
    json: Any


@runtime_checkable
class Transport(Protocol):
    """The seam between :class:`~honba.client.Client` and the API.

    ``path`` is percent-encoded as it appears on the wire; ``query`` is a flat mapping of
    scalars; ``body`` is a JSON-serialisable value sent as ``application/json``. A transport
    returns every HTTP answer, error statuses included, as a :class:`Response`; it raises
    :class:`~honba.client.TransportApiError` only when no answer arrived.
    """

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> Response: ...


@dataclass(frozen=True)
class RetryPolicy:
    """A bounded, explicit retry policy: one entry of ``delays`` per retry (seconds to wait
    before it). Empty (the default) means no retries. Only a ``retryable`` envelope error or a
    connection failure is ever retried; a non-retryable error is returned at once."""

    delays: tuple[float, ...] = ()

    MAX_RETRIES = 10

    def __post_init__(self) -> None:
        if len(self.delays) > self.MAX_RETRIES:
            raise ValueError(f"at most {self.MAX_RETRIES} retries")
        if any(not math.isfinite(d) or d < 0 for d in self.delays):
            raise ValueError("retry delays must be finite and >= 0")

    @classmethod
    def none(cls) -> RetryPolicy:
        return cls()

    @classmethod
    def fixed(cls, retries: int, delay: float) -> RetryPolicy:
        """``retries`` retries, each after ``delay`` seconds."""
        if retries < 0:
            raise ValueError("retries must be >= 0")
        return cls(delays=(delay,) * retries)


def _dumps(value: Any) -> str:
    """Compact, key-order-preserving JSON: both transports send the same bytes, so a server
    message that quotes a column (a parse error) is identical across them."""
    return json.dumps(value, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def _parse_json(text: str) -> Any:
    try:
        return json.loads(text) if text.strip() else None
    except ValueError:
        return None


def _is_retryable(response: Response) -> bool:
    body = response.json
    error = body.get("error") if isinstance(body, Mapping) else None
    return isinstance(error, Mapping) and error.get("retryable") is True


class HttpTransport:
    """HTTP transport against a running ``honba serve``.

    ``base_url`` is ``http(s)://host:port``; ``timeout`` is seconds for each request. No
    redirects are followed and nothing is retried unless ``retry`` says so. ``transport`` (an
    ``httpx`` transport) and ``sleep`` are injection points for tests.
    """

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 10.0,
        retry: RetryPolicy | None = None,
        headers: Mapping[str, str] | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(f"base_url must start with http:// or https://, got {base_url!r}")
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("timeout must be a positive number of seconds")
        self._retry = retry or RetryPolicy.none()
        self._sleep = sleep
        self._http = httpx.Client(
            base_url=base_url.rstrip("/"),
            timeout=timeout,
            headers=dict(headers or {}),
            transport=transport,
            follow_redirects=False,
        )

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> Response:
        attempts = len(self._retry.delays) + 1
        for attempt in range(attempts):
            last = attempt == attempts - 1
            try:
                response = self._send(method, path, query, body)
            except TransportApiError:
                if last:
                    raise
            else:
                if last or not _is_retryable(response):
                    return response
            self._sleep(self._retry.delays[attempt])
        raise AssertionError("unreachable")  # pragma: no cover

    def _send(self, method: str, path: str, query: Mapping[str, Any] | None, body: Any) -> Response:
        try:
            http = self._http.request(
                method,
                path,
                params=dict(query) if query else None,
                content=_dumps(body).encode() if body is not None else None,
                headers={"content-type": "application/json"} if body is not None else None,
            )
        except httpx.TimeoutException as exc:
            raise TransportApiError(
                "timeout", str(exc) or "request timed out", retryable=True
            ) from exc
        except httpx.TransportError as exc:
            raise TransportApiError(
                "transport_error", str(exc) or type(exc).__name__, retryable=True
            ) from exc
        return Response(http.status_code, _parse_json(http.text))

    def close(self) -> None:
        self._http.close()


class InprocTransport:
    """Runs the REST router in this process over the ``SYMBOL.EXCHANGE.parquet`` files in
    ``data_dir`` (needs the native extension; see ``honba._native``).

    The directory is loaded once per process on first use and cached (``honba serve`` also
    loads once), so files added later are not seen. Answers are the served API's, status and
    envelope included.

    ``journals_dir`` turns on the run routes (``/backtests``, ``/journals``): runs are executed
    by the Rust run service in this process and journaled there (created if missing). Without
    it a valid submit answers 503 ``unsupported`` (``context.reason == "no_journals_dir"``).
    ``max_concurrent_runs`` and ``max_queued_runs`` bound the worker pool and the pending
    queue (a full queue answers 429); ``None`` keeps the server defaults. The service lives
    for the process, keyed by these four settings, so runs outlive any one transport object;
    they do not survive interpreter exit (ADR 0017 decision 5).
    """

    def __init__(
        self,
        data_dir: str | Path,
        *,
        journals_dir: str | Path | None = None,
        max_concurrent_runs: int | None = None,
        max_queued_runs: int | None = None,
    ) -> None:
        path = Path(data_dir)
        if not path.is_dir():
            raise NotADirectoryError(f"data directory {str(path)!r} is not a directory")
        for name, value in (
            ("max_concurrent_runs", max_concurrent_runs),
            ("max_queued_runs", max_queued_runs),
        ):
            if value is not None and value < 1:
                raise ValueError(f"{name} must be >= 1")
        self._data_dir = str(path)
        self._journals_dir: str | None = None
        if journals_dir is not None:
            journals = Path(journals_dir)
            journals.mkdir(parents=True, exist_ok=True)
            self._journals_dir = str(journals)
        self._max_concurrent_runs = max_concurrent_runs
        self._max_queued_runs = max_queued_runs

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> Response:
        extra: dict[str, Any] = {}
        if self._journals_dir is not None:
            extra["journals_dir"] = self._journals_dir
            if self._max_concurrent_runs is not None:
                extra["max_concurrent_runs"] = self._max_concurrent_runs
            if self._max_queued_runs is not None:
                extra["max_queued_runs"] = self._max_queued_runs
        try:
            status, text = native_attr("api_request")(
                self._data_dir,
                method,
                path,
                _dumps(dict(query)) if query else None,
                _dumps(body) if body is not None else None,
                **extra,
            )
        except (OSError, ValueError) as exc:
            # Same type and code HTTP uses when no answer arrived; not retryable: a data
            # directory that vanished or a malformed call will not fix itself.
            raise TransportApiError(
                "transport_error", str(exc) or type(exc).__name__, retryable=False
            ) from exc
        return Response(status, _parse_json(text))

    def close(self) -> None:
        """Nothing to release: the native state is process-wide."""
