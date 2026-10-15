"""An ``ExecutionAdapter`` behind the JSON gateway protocol of the Rust ``PythonGateway``.

``honba._honba.GatewayServer`` runs the REST app with an execution venue that calls a Python
object (ADR 0030, E3-S7). That object speaks four methods over JSON strings, so no Rust type
crosses the boundary as a Python class:

    submit_order(order_json) -> venue order id     (raises on refusal)
    cancel_order(venue_id)
    modify_order(venue_id, quantity, price)
    next_fill() -> Trade JSON | None               (None: nothing new)

:class:`ExecutionAdapterGateway` implements that protocol over any
:class:`~honba.adapters.base.ExecutionAdapter`. Every method is a coroutine; the Rust side
awaits it on a dedicated event loop, so a slow broker never stalls the HTTP server.

Errors keep the adapter taxonomy and the Rust side maps them onto ``PortError``: a broker
refusal (``OrderReport`` with ``status=REJECTED``) becomes :class:`GatewayRejected`, an
``AdapterFatalError`` needs the operator, any other ``AdapterError`` is retriable.
"""

from __future__ import annotations

import datetime as dt
import json
from typing import Any, ClassVar

from honba.adapters.base import ExecutionAdapter
from honba.adapters.errors import AdapterError
from honba.adapters.models import OrderStatus, Product
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide, OrderType, TimeInForce
from honba.domain.trade import Trade

__all__ = ["ExecutionAdapterGateway", "GatewayRejected"]

_EPOCH = dt.datetime(1970, 1, 1, tzinfo=dt.UTC)


class GatewayRejected(AdapterError):
    """The broker refused an order on its merits; ``reject_code`` carries its reason.

    Not retryable: the Rust side maps it to ``PortError::Rejected`` and the order row is
    rejected with the stable ``order_rejected`` code.
    """

    code: ClassVar[str] = "adapter.rejected"

    def __init__(self, reject_code: str, message: str | None = None) -> None:
        super().__init__(message or reject_code)
        self.reject_code = reject_code


def _nanos(ts: int) -> dict[str, str]:
    seconds, nanos = divmod(max(ts, 0), 1_000_000_000)
    stamp = (_EPOCH + dt.timedelta(seconds=seconds)).strftime("%Y-%m-%dT%H:%M:%S")
    return {"iso": f"{stamp}.{nanos:09d}Z", "unix_nanos": str(ts)}


def _optional_float(value: Any) -> float | None:
    return None if value is None else float(value)


class ExecutionAdapterGateway:
    """Adapts an :class:`ExecutionAdapter` to the gateway protocol above.

    ``product`` is used for orders that carry none. Fills are read by polling
    ``adapter.trades()``; the list is append-only for the day, so only entries past the last
    one handed out are new.
    """

    def __init__(self, adapter: ExecutionAdapter, *, product: Product = Product.INTRADAY) -> None:
        self._adapter = adapter
        self._product = product
        self._seen = 0

    async def submit_order(self, order_json: str) -> str:
        order = json.loads(order_json)
        instrument = order["instrument_id"]
        intent = OrderIntent(
            InstrumentId(instrument["symbol"], instrument["exchange"]),
            OrderSide(order["side"]),
            float(order["quantity"]),
            OrderType(order["order_type"]),
            price=_optional_float(order.get("price")),
            time_in_force=TimeInForce(order["time_in_force"]),
            trigger_price=_optional_float(order.get("trigger_price")),
            trail_amount=_optional_float(order.get("trail_amount")),
            trail_percent=_optional_float(order.get("trail_percent")),
        )
        product = Product(order["product"]) if order.get("product") else self._product
        report = await self._adapter.place_order(
            intent, product=product, client_order_id=order["order_id"]
        )
        if report.status is OrderStatus.REJECTED:
            raise GatewayRejected(report.reject_reason or "rejected")
        return report.venue_order_id or report.order_id

    async def cancel_order(self, venue_id: str) -> None:
        await self._adapter.cancel_order(venue_id)

    async def modify_order(self, venue_id: str, quantity: float, price: float | None) -> None:
        await self._adapter.modify_order(venue_id, quantity=quantity, price=price)

    async def next_fill(self) -> str | None:
        trades = await self._adapter.trades()
        if self._seen >= len(trades):
            return None
        trade = trades[self._seen]
        self._seen += 1
        return json.dumps(_trade_json(trade))


def _trade_json(trade: Trade) -> dict[str, Any]:
    return {
        "order_id": trade.order_id or "",
        "instrument_id": {
            "symbol": trade.instrument_id.symbol,
            "exchange": trade.instrument_id.exchange,
        },
        "side": trade.side.value,
        "quantity": trade.quantity,
        "price": trade.price,
        "costs": {"amount": trade.costs.amount, "currency": trade.costs.currency.value},
        "ts_event": _nanos(trade.ts),
        "ts_init": _nanos(trade.ts),
    }
