"""Type stubs for honba._honba native PyO3 extension module."""

from typing import Final

SCHEMA_VERSION: Final[int]

def canonical_json(kind: str, payload: str) -> str: ...
def wire_enum_values() -> dict[str, list[str]]: ...
def nse_equity_settlement_days() -> int:
    """India (NSE/BSE) equity delivery settlement cycle in days (T+2).

    Mirrors ``IndiaMarketProfile::equity_settlement_days`` in
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

class InstrumentId:
    symbol: str
    exchange: str
    def __init__(self, symbol: str, exchange: str = "NSE") -> None: ...

class QuoteTick:
    symbol: str
    exchange: str
    bid_price: float
    ask_price: float
    bid_size: float
    ask_size: float
    ts: int
    def __init__(
        self,
        symbol: str,
        bid_price: float,
        ask_price: float,
        bid_size: float = 1.0,
        ask_size: float = 1.0,
        ts: int = 0,
        exchange: str = "NSE",
    ) -> None: ...
    @property
    def mid_price(self) -> float: ...

class Bar:
    symbol: str
    exchange: str
    ts: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    def __init__(
        self,
        symbol: str,
        ts: int,
        open: float,
        high: float,
        low: float,
        close: float,
        volume: float = 0.0,
        exchange: str = "NSE",
    ) -> None: ...

class Fill:
    symbol: str
    ts: int
    price: float
    qty: float
    side: str
    def __init__(
        self,
        symbol: str,
        ts: int,
        price: float,
        qty: float,
        side: str,
    ) -> None: ...

class OrderIntent:
    symbol: str
    exchange: str
    side: str
    quantity: float
    order_type: str
    price: float | None
    trigger_price: float | None
    time_in_force: str
    def __init__(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        price: float | None = None,
        time_in_force: str = "day",
        exchange: str = "NSE",
        trigger_price: float | None = None,
    ) -> None: ...
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
        symbol: str, quantity: float, trigger_price: float, limit_price: float, exchange: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def stop_limit_sell(
        symbol: str, quantity: float, trigger_price: float, limit_price: float, exchange: str = "NSE"
    ) -> OrderIntent: ...

class RustSmaCrossover:
    def __init__(self, fast: int = 3, slow: int = 8, qty: float = 1.0) -> None: ...
    def on_bar(self, bar: Bar) -> tuple[str, float] | None: ...
    def on_close(self, close: float) -> tuple[str, float] | None: ...
    @property
    def position(self) -> float: ...
    @property
    def intent_count(self) -> int: ...
    def intents(self) -> list[tuple[str, float]]: ...
