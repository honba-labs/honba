"""Type stubs for honba._honba native PyO3 extension module."""

from typing import Final, final

from typing_extensions import Self

__all__ = [
    "API_VERSION",
    "SCHEMA_VERSION",
    "STRATEGY_API_VERSION",
    "Bar",
    "Fill",
    "InstrumentId",
    "OrderIntent",
    "QuoteTick",
    "RustSmaCrossover",
    "canonical_json",
    "codegen_artifacts",
    "codegen_render",
    "currency_minor_units",
    "get_runtime_handle",
    "initialize_runtime",
    "nse_equity_settlement_days",
    "run_strategy",
    "verify_manifest",
    "wire_enum_values",
]

SCHEMA_VERSION: Final[int]
API_VERSION: Final[str]
STRATEGY_API_VERSION: Final[str]

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
    """Initialize the Tokio runtime for the Python async bridge.

    Raises ``RuntimeError`` if the runtime cannot be installed.
    """

def get_runtime_handle() -> str:
    """Name of the runtime flavour (``"tokio-multi-thread"``)."""
