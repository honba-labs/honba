"""An ``ExecutionAdapter`` whose venue is a running ``honba serve`` reached over REST.

The mirror of :mod:`honba.adapters.gateway`: there a Python adapter sits *behind* a Rust-served
``/orders``; here a Python (or other-process) caller sits *in front of* one. Strategies, engines
and sessions that take an ``ExecutionAdapter`` can submit to the server's simulator or gateway
venue and read the outcome back (ADR 0030, E3-S7). It reuses :class:`honba.client.Client`, so no
wire type is hand-written here.

Routes and scopes (ADR 0023): ``POST /orders``, ``GET /orders`` and ``DELETE /orders/{id}`` all
need the ``write:orders`` scope (the ``read:account`` scope is reserved and no served route
takes it). ``GET /health`` is public. The adapter never calls the operator routes, so a token
without ``operator`` is enough; a halted server surfaces as a refusal (below).

What the server's order ledger can and cannot say (the adapter reports exactly this, it invents
nothing):

* A row carries identity, side, type, quantity, optional limit price and a *status*
  (``submitted``, ``partially_filled``, ``filled``, ``cancelled``, ``rejected``). It has no
  filled quantity, no average fill price, no venue order id, no timestamps (always 0) and no
  reject reason. There is no ``GET /trades`` or ``GET /positions`` route.
* So :meth:`RestExecutionAdapter.trades` is unsupported (``CapabilityError``), a fill is visible
  only as the row reaching ``filled`` (or ``partially_filled``), and an
  :class:`~honba.adapters.models.OrderReport` for a fill cannot be built honestly:
  ``filled_quantity`` of a partial and ``average_price`` of any fill are unknown. A ``filled``
  row reports ``filled_quantity == quantity`` (true by the ledger's semantics) and needs an
  average price: the row's limit price is *not* used (a limit can fill better). Pass
  ``fill_price=`` (a callable on the :class:`~honba.client.models.LedgerOrder`) to supply one
  from your own source; without it the report raises :class:`LedgerLimitationError`, and
  :meth:`RestExecutionAdapter.ledger` still returns the status-only rows.
* A venue rejection that happens after the POST (a limit order the simulator refuses, a broker
  refusal behind a gateway) is a ``rejected`` row without a reason; its report carries a
  placeholder reason saying so. A refusal at POST time (``risk_*``, ``order_rejected``,
  including ``risk_trading_halted``) carries the server's stable code and message.
* ``positions()`` is derived from the ledger: the net signed quantity of ``filled`` rows per
  instrument, ``avg_price`` 0.0 (unknown), currency INR (the account currency is not exposed).
  A ``partially_filled`` row makes it raise, because its filled quantity is unknown.
* ``time_in_force`` is always ``day`` on the row and ``product`` is not sent: the adapter
  supports ``DAY`` only and remembers nothing about products.

Retries (idempotency): a connection failure, a timeout or any ``retryable`` envelope is retried
with bounded delays only when it is safe: ``GET`` and ``DELETE`` always, ``POST /orders`` only
when the caller gave a ``client_order_id`` (the ledger answers a repeat with the first row, ADR
0019), and a ``rate_limited`` answer always (the request was refused unprocessed). A timeout on
a ``POST`` without a client id is raised with the outcome flagged unknown, never retried.
"""

from __future__ import annotations

import asyncio
import uuid
from collections.abc import Awaitable, Callable
from typing import ClassVar, TypeVar

from honba.adapters.base import Adapter
from honba.adapters.capabilities import AdapterCapabilities, Capability
from honba.adapters.errors import (
    AdapterError,
    AdapterFatalError,
    CapabilityError,
)
from honba.adapters.models import OrderReport, OrderStatus, Product, RunMode, SessionInfo
from honba.client import (
    ApiError,
    AuthApiError,
    Client,
    HttpTransport,
    InvalidResponseError,
    RateLimitedApiError,
    RiskApiError,
    UnsupportedApiError,
    ValidationApiError,
)
from honba.client.models import LedgerOrder
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderType, TimeInForce

__all__ = ["LedgerLimitationError", "RestApiError", "RestAuthError", "RestExecutionAdapter"]

_T = TypeVar("_T")

_NO_REASON = "rejected by the venue; the order ledger (GET /orders) does not carry the reason"

_DEFAULT_RETRY = (0.2, 0.5, 1.0)


class RestApiError(AdapterError):
    """A failed call to the server that is not a refusal: transport, timeout, rate limit,
    ``order_execution_unavailable``, ``internal_error``. Recoverable (``fatal`` is False).

    ``api_code`` is the envelope's stable code, ``retryable`` the server's own flag and
    ``status`` the HTTP status (``None`` when no response arrived).
    """

    code: ClassVar[str] = "adapter.rest_api"

    def __init__(
        self, api_code: str, message: str, *, retryable: bool = False, status: int | None = None
    ) -> None:
        super().__init__(f"{api_code}: {message}")
        self.api_code = api_code
        self.retryable = retryable
        self.status = status


class RestAuthError(AdapterFatalError):
    """``unauthorized`` or ``forbidden``: the token is missing, unknown, or lacks
    ``write:orders``. Retrying will not help."""

    code: ClassVar[str] = "adapter.rest_auth"

    def __init__(self, api_code: str, message: str) -> None:
        super().__init__(f"{api_code}: {message}")
        self.api_code = api_code


class LedgerLimitationError(AdapterError):
    """The order ledger cannot answer: a fill's average price or a partial fill's quantity is
    not on the row (see the module docstring). Permanent for that row; not fatal to the run."""

    code: ClassVar[str] = "adapter.rest_ledger_limitation"


class _Refused(Exception):
    """The server refused an order on its merits; carries the envelope error."""

    def __init__(self, error: ApiError) -> None:
        super().__init__(str(error))
        self.error = error


def _is_refusal(error: ApiError) -> bool:
    """``risk_*``, ``order_rejected`` and ``validation_invalid_request`` are the server saying
    no to this order; ``order_execution_unavailable`` is the venue being down (retriable)."""
    return isinstance(error, (RiskApiError, ValidationApiError)) or error.code == "order_rejected"


class RestExecutionAdapter(Adapter):
    """Submit orders to ``honba serve`` and read the ledger back, as an ``ExecutionAdapter``.

    ``base_url`` is ``http(s)://host:port``; ``token`` is sent as ``Authorization: Bearer``
    (never logged or put in an error). ``timeout`` is seconds per request; ``retry`` is the
    delay before each retry (empty: none). ``exchanges`` is what the capability descriptor
    declares (the server does not list its venue set on a route this adapter may call).
    ``client`` and ``sleep`` are injection points for tests; the caller owns an injected client.
    """

    name = "honba-rest"

    def __init__(
        self,
        base_url: str,
        *,
        token: str | None = None,
        timeout: float = 10.0,
        retry: tuple[float, ...] = _DEFAULT_RETRY,
        exchanges: frozenset[str] = frozenset({"NSE"}),
        user_id: str = "honba-rest",
        mode: RunMode = RunMode.PAPER,
        product: Product = Product.INTRADAY,
        fill_price: Callable[[LedgerOrder], float] | None = None,
        client: Client | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        headers = {"authorization": f"Bearer {token}"} if token else None
        self._client = client or Client(HttpTransport(base_url, timeout=timeout, headers=headers))
        self._owns_client = client is None
        self._retry = retry
        self._sleep = sleep
        self._user_id = user_id
        self._mode = mode
        self._product = product
        self._fill_price = fill_price
        self._connected = False
        self._capabilities = AdapterCapabilities(
            name=self.name,
            exchanges=exchanges,
            order_types=frozenset({OrderType.MARKET, OrderType.LIMIT}),
            time_in_force=frozenset({TimeInForce.DAY}),
            features=frozenset(
                {
                    Capability.PLACE_ORDER,
                    Capability.CANCEL_ORDER,
                    Capability.CANCEL_ALL,
                    Capability.ORDER_BOOK,
                    Capability.POSITIONS,
                }
            ),
        )

    # -- lifecycle -------------------------------------------------------------

    def capabilities(self) -> AdapterCapabilities:
        return self._capabilities

    async def connect(self) -> SessionInfo:
        self._connected = True
        return await self.session()

    async def disconnect(self) -> None:
        self._connected = False
        if self._owns_client:
            self._client.close()

    def is_connected(self) -> bool:
        return self._connected

    async def session(self) -> SessionInfo:
        self.require_connected()
        return SessionInfo(user_id=self._user_id, mode=self._mode)

    # -- calls -----------------------------------------------------------------

    async def _call(self, fn: Callable[[], _T], *, idempotent: bool, refusals: bool = False) -> _T:
        """Run the blocking client call off the loop, retrying per the module rules."""
        attempt = 0
        while True:
            try:
                return await asyncio.to_thread(fn)
            except ApiError as error:
                safe = idempotent or isinstance(error, RateLimitedApiError)
                if error.retryable and safe and attempt < len(self._retry):
                    await self._sleep(self._retry[attempt])
                    attempt += 1
                    continue
                if refusals and _is_refusal(error):
                    raise _Refused(error) from error
                raise self._map_error(error, idempotent=idempotent) from error

    @staticmethod
    def _map_error(error: ApiError, *, idempotent: bool) -> AdapterError:
        if isinstance(error, AuthApiError):
            return RestAuthError(error.code, error.message)
        if isinstance(error, InvalidResponseError):
            return AdapterFatalError(f"malformed response from the server: {error.message}")
        if isinstance(error, UnsupportedApiError):
            return CapabilityError(f"the server does not support this: {error.code}")
        message = error.message
        if not idempotent and error.category == "transport":
            message += " (the outcome is unknown: the order may exist; not retried without a client_order_id)"
        return RestApiError(error.code, message, retryable=error.retryable, status=error.status)

    # -- orders ----------------------------------------------------------------

    def _report(self, row: LedgerOrder) -> OrderReport:
        status = OrderStatus(row.status)
        filled = 0.0
        average = 0.0
        reason = None
        if status is OrderStatus.REJECTED:
            reason = _NO_REASON
        elif status is OrderStatus.PARTIALLY_FILLED:
            raise LedgerLimitationError(
                f"order {row.order_id} is partially filled; the ledger row has no filled quantity"
            )
        elif status is OrderStatus.FILLED:
            if self._fill_price is None:
                raise LedgerLimitationError(
                    f"order {row.order_id} is filled; the ledger row has no fill price "
                    "(pass fill_price= to RestExecutionAdapter, or read ledger())"
                )
            filled = row.quantity
            average = self._fill_price(row)
        return OrderReport(
            order_id=row.order_id,
            instrument_id=InstrumentId(row.instrument_id.symbol, row.instrument_id.exchange),
            side=OrderSide(row.side),
            quantity=row.quantity,
            status=status,
            product=self._product,
            order_type=OrderType(row.order_type),
            time_in_force=TimeInForce(row.time_in_force),
            filled_quantity=filled,
            average_price=average,
            price=row.price,
            reject_reason=reason,
        )

    async def place_order(
        self,
        intent: OrderIntent,
        *,
        product: Product,
        client_order_id: str | None = None,
    ) -> OrderReport:
        self.require_connected()
        caps = self._capabilities
        caps.require(Capability.PLACE_ORDER)
        caps.require_order_type(intent.order_type)
        caps.require_time_in_force(intent.time_in_force)
        caps.require_product(product)
        text = f"{intent.instrument_id.symbol}.{intent.instrument_id.exchange}"
        try:
            row = await self._call(
                lambda: self._client.place_order(
                    text,
                    intent.side.value,
                    intent.quantity,
                    order_type=intent.order_type.value,
                    price=intent.price,
                    client_order_id=client_order_id,
                ),
                idempotent=client_order_id is not None,
                refusals=True,
            )
        except _Refused as refused:
            return self._refusal(intent, product, client_order_id, refused.error)
        return self._report(row)

    def _refusal(
        self, intent: OrderIntent, product: Product, client_order_id: str | None, error: ApiError
    ) -> OrderReport:
        """A server refusal as data: ``REJECTED`` with the stable code and message. Without a
        client id there is no server id to report, so a local ``rejected-`` id stands in."""
        return OrderReport(
            order_id=client_order_id or f"rejected-{uuid.uuid4().hex[:12]}",
            instrument_id=intent.instrument_id,
            side=intent.side,
            quantity=intent.quantity,
            status=OrderStatus.REJECTED,
            product=product,
            order_type=intent.order_type,
            time_in_force=intent.time_in_force,
            price=intent.price,
            reject_reason=f"{error.code}: {error.message}",
        )
