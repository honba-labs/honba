"""Type stubs for honba._honba native PyO3 extension module."""

from collections.abc import Callable
from typing import Any, Final, final

from typing_extensions import Self

__all__ = [
    "API_VERSION",
    "SCHEMA_VERSION",
    "STRATEGY_API_VERSION",
    "Bar",
    "Fill",
    "InstrumentId",
    "NextOpenSimulator",
    "OrderIntent",
    "QuoteTick",
    "RustSmaCrossover",
    "api_request",
    "canonical_json",
    "codegen_artifacts",
    "codegen_render",
    "currency_minor_units",
    "get_runtime_handle",
    "initialize_runtime",
    "next_open_fill_cost",
    "nse_equity_settlement_days",
    "run_strategy",
    "runtime_info",
    "runtime_start",
    "runtime_stop",
    "verify_manifest",
    "wire_enum_values",
]

SCHEMA_VERSION: Final[int]
API_VERSION: Final[str]
STRATEGY_API_VERSION: Final[str]

def api_request(
    data_dir: str,
    method: str,
    path: str,
    query_json: str | None = None,
    body_json: str | None = None,
) -> tuple[int, str]:
    """Send one request through the REST read API's router, in process (no socket).

    Builds the router ``honba serve`` serves over the ``SYMBOL.EXCHANGE.parquet`` files in
    ``data_dir`` (loaded once per process and cached) and returns ``(status, body_json)``.
    ``query_json`` is a flat JSON object of scalars. A request the API rejects (404, 422,
    501) is a normal return carrying the error envelope. Raises ``OSError`` when ``data_dir``
    cannot be loaded and ``ValueError`` for a malformed request (bad method, relative path,
    non-flat query). Wrapped by ``honba.client.InprocTransport``.
    """

def canonical_json(kind: str, payload: str) -> str: ...
def wire_enum_values() -> dict[str, list[str]]: ...
def currency_minor_units() -> dict[str, tuple[int, str, str]]:
    """Minor-unit table per currency code: ``(exponent, singular name, plural name)``.

    One major unit is ``10**exponent`` minor units. Rust owns the table (ADR 0011).
    """

def verify_manifest(manifest_json: str) -> str:
    """Compile a ``StrategyManifest`` (JSON) into its ``StrategyIr`` (JSON).

    Raises ``ValueError("<code>: <message>")`` where ``code`` is ``deserialize`` or the
    Rust ``IrError::code()``; wrapped by ``honba.strategies.verify.verify_manifest``.
    """

def codegen_artifacts() -> list[str]:
    """Names accepted by ``codegen_render``: json_schema, openapi, typescript, pyi, mcp."""

def codegen_render(kind: str) -> tuple[str, str]:
    """Render one honba-codegen artifact as ``(file_name, content)``.

    Byte-for-byte what the Rust ``honba schema export`` writes; raises ``ValueError``
    for an unknown ``kind``.
    """

def nse_equity_settlement_days(as_of: str | None = None) -> int:
    """India (NSE/BSE) equity delivery settlement cycle in days.

    ``as_of`` is an ISO date (``"YYYY-MM-DD"``): T+2 before 2023-01-27, T+1 from then.
    ``None`` is the cycle in force today (T+1). Raises ``ValueError`` for a malformed date.
    Mirrors ``IndiaMarketProfile::equity_settlement_days_as_of`` in
    ``crates/honba-market/src/india/profile.rs``; wrap it with
    ``honba.markets.india.settlement_days_for``.
    """

def run_strategy(
    strategy: str,
    params: str,
    events: str,
    instruments: str = "[]",
    initial_cash: float = 0.0,
    flat_cost: float = 0.0,
    cost_bps: float = 0.0,
) -> str:
    """Run a Rust reference strategy over JSON wire messages (ADR 008).

    ``strategy`` is ``"contract_probe"``, ``"buy_and_hold"`` or ``"sma_crossover"``;
    ``params``, ``events`` (a list of wire ``Message``) and ``instruments`` are JSON.
    Orders fill at the last bar close (``BarFillEngine``). Returns JSON with
    ``intents``, ``rejections`` (intents refused for breaking an invariant, each
    ``{"ts_init", "intent", "error": {"kind", "message"}}``), ``fills``,
    ``observations``, ``positions`` and ``cash``.
    ``flat_cost`` (``0..=1e9``) and ``cost_bps`` (``0..=10_000``) set the per-fill costs:
    ``Trade.costs = flat_cost + quantity * price * cost_bps / 10_000`` (default none).
    Raises ``ValueError`` for an unknown strategy, invalid JSON or invalid costs.
    """

def next_open_fill_cost(pack: str, side: str, quantity: float, price: float) -> int:
    """Cost in minor units of one fill under a named cost pack (INR).

    ``pack`` is ``"india.equity"`` / ``"india.equity.delivery"``, ``"india.equity.intraday"``
    or ``"none"`` / ``"zero"``; ``side`` is ``"buy"`` or ``"sell"``. Each charge leg is rounded
    to paise once and summed, exactly like ``nse_equity_delivery_fill_cost`` /
    ``nse_equity_intraday_fill_cost``. Raises ``ValueError`` for an unknown pack.
    """

@final
class NextOpenSimulator:
    """Rust ``NextOpenSim`` (ADR 0016): market orders fill at the next session's open.

    Money is integer minor units (ADR 0011); bars and orders are plain dicts. A bar has
    ``symbol``, ``ts``, ``open``, ``high``, ``low``, ``close`` and optional ``volume`` and
    ``exchange`` (default ``"NSE"``). An order has ``id``, ``symbol``, ``side`` (``"buy"`` /
    ``"sell"``), ``qty``, ``ts`` and optional ``type`` (default ``"market"``), ``price``,
    ``trigger``, ``exchange``. ``costs`` is ``None``, a pack name (see ``next_open_fill_cost``)
    or a callable ``(side, quantity, price) -> int`` of minor units whose exception propagates
    unchanged. ``ValueError`` for rule violations (non-monotonic or duplicate bars, a
    non-advancing session, a duplicate working id, a negative cost); ``RuntimeError`` when
    ``set_settlement_days`` runs after the first session.
    """

    cash: int
    fees: int
    traded_notional: int
    unsettled: int
    available_cash: int
    settlement_days: int
    session_ts: int | None
    receivables: list[tuple[int, int]]
    positions: list[tuple[str, str, float]]
    working_orders: list[str]
    def __new__(
        cls,
        cash: int,
        currency: str = "INR",
        *,
        settlement_days: int = 0,
        long_only: bool = True,
        lot_sizes: dict[str, float] | dict[tuple[str, str], float] | None = None,
        costs: str | Callable[[str, float, float], int] | None = None,
    ) -> Self: ...
    def open_session(self, ts: int, bars: list[dict[str, Any]]) -> None: ...
    def on_session_open(self, ts: int, bars: list[dict[str, Any]]) -> None: ...
    def on_bar(self, bar: dict[str, Any]) -> None: ...
    def submit(self, order: dict[str, Any]) -> None: ...
    def cancel(self, order_id: str, now: int) -> None: ...
    def set_settlement_days(self, days: int) -> None: ...
    def set_lot_size(self, symbol: str, lot_size: float, exchange: str = "NSE") -> None: ...
    def set_position(self, symbol: str, quantity: float, exchange: str = "NSE") -> None: ...
    def drain_events(self) -> list[dict[str, Any]]: ...
    def drain_fills(self) -> list[dict[str, Any]]: ...
    def drain_rejections(self) -> list[dict[str, Any]]: ...

@final
class InstrumentId:
    symbol: str
    exchange: str
    def __new__(cls, symbol: str, exchange: str = "NSE") -> Self: ...

@final
class QuoteTick:
    symbol: str
    exchange: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    ts: int
    def __new__(
        cls,
        symbol: str,
        bid_price: float,
        ask_price: float,
        bid_size: float = 1.0,
        ask_size: float = 1.0,
        ts: int = 0,
        exchange: str = "NSE",
    ) -> Self: ...
    @property
    def mid_price(self) -> float: ...

@final
class Bar:
    symbol: str
    exchange: str
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    def __new__(
        cls,
        symbol: str,
        ts: int,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
        exchange: str = "NSE",
    ) -> Self: ...

@final
class Fill:
    symbol: str
    ts: int
    price: float
    qty: float
    side: str
    def __new__(
        cls,
        symbol: str,
        ts: int,
        price: float,
        qty: float,
        side: str,
    ) -> Self: ...

@final
class OrderIntent:
    symbol: str
    exchange: str
    side: str
    quantity: float
    order_type: str
    price: float | None
    trigger_price: float | None
    time_in_force: str
    def __new__(
        cls,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        price: float | None = None,
        time_in_force: str = "day",
        exchange: str = "NSE",
        trigger_price: float | None = None,
    ) -> Self: ...
    @staticmethod
    def market_buy(symbol: str, quantity: float, exchange: str = "NSE") -> OrderIntent: ...
    @staticmethod
    def market_sell(symbol: str, quantity: float, exchange: str = "NSE") -> OrderIntent: ...
    @staticmethod
    def limit_buy(
        symbol: str, quantity: float, price: float, exchange: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def limit_sell(
        symbol: str, quantity: float, price: float, exchange: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def stop_buy(
        symbol: str, quantity: float, trigger_price: float, exchange: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def stop_sell(
        symbol: str, quantity: float, trigger_price: float, exchange: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def stop_limit_buy(
        symbol: str,
        quantity: float,
        trigger_price: float,
        limit_price: float,
        exchange: str = "NSE",
    ) -> OrderIntent: ...
    @staticmethod
    def stop_limit_sell(
        symbol: str,
        quantity: float,
        trigger_price: float,
        limit_price: float,
        exchange: str = "NSE",
    ) -> OrderIntent: ...

@final
class RustSmaCrossover:
    def __new__(cls, fast: int = 3, slow: int = 8, qty: float = 1.0) -> Self: ...
    def on_bar(self, bar: Bar) -> tuple[str, float] | None: ...
    def on_close(self, close: float) -> tuple[str, float] | None: ...
    @property
    def position(self) -> float: ...
    @property
    def intent_count(self) -> int: ...
    def intents(self) -> list[tuple[str, float]]: ...

def initialize_runtime() -> None:
    """Deprecated: use ``honba.event_loop.start()``.

    Starts the async runtime if none is running (idempotent).
    """

def get_runtime_handle() -> str:
    """Deprecated: use ``honba.event_loop.info()``.

    The running runtime's flavour (``"tokio-multi-thread"``); ``RuntimeError`` if none is running.
    """

def runtime_start(worker_threads: int | None = None) -> tuple[str, int, int]:
    """Start the interpreter's one async runtime: ``(flavor, worker_threads, generation)``.

    ``RuntimeError`` if one is already running, ``ValueError`` for ``worker_threads=0``. Use
    ``honba.event_loop`` instead of calling this directly.
    """

def runtime_stop() -> bool:
    """Stop the runtime and join its threads; ``True`` if one was running. Idempotent."""

def runtime_info() -> tuple[str, int, int] | None:
    """``(flavor, worker_threads, generation)`` of the running runtime, or ``None``."""
