"""Type stubs for honba._honba native PyO3 extension module."""

from typing import Final, List, Optional, Tuple

SCHEMA_VERSION: Final[int]

def canonical_json(kind: str, payload: str) -> str: ...
def wire_enum_values() -> dict[str, list[str]]: ...
def run_strategy(
    strategy: str,
    params: str,
    events: str,
    instruments: str = "[]",
    initial_cash: float = 0.0,
) -> str:
    """Run a Rust reference strategy over JSON wire messages (ADR 008).

    ``strategy`` is ``"contract_probe"``, ``"buy_and_hold"`` or ``"sma_crossover"``;
    ``params``, ``events`` (a list of wire ``Message``) and ``instruments`` are JSON.
    Orders fill at the last bar close (``BarFillEngine``). Returns JSON with
    ``intents``, ``rejections`` (intents refused for breaking an invariant, each
    ``{"ts_init", "intent", "error": {"kind", "message"}}``), ``fills``,
    ``observations``, ``positions`` and ``cash``.
    Raises ``ValueError`` for an unknown strategy or invalid JSON.
    """

class InstrumentId:
    symbol: str
    venue: str
    def __init__(self, symbol: str, venue: str = "NSE") -> None: ...
    def __repr__(self) -> str: ...
    def __str__(self) -> str: ...

class QuoteTick:
    symbol: str
    venue: str
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
        venue: str = "NSE",
    ) -> None: ...
    @property
    def mid_price(self) -> float: ...
    def __repr__(self) -> str: ...

class Bar:
    symbol: str
    venue: str
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
        venue: str = "NSE",
    ) -> None: ...
    def __repr__(self) -> str: ...

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
    def __repr__(self) -> str: ...

class OrderIntent:
    symbol: str
    venue: str
    side: str
    quantity: float
    order_type: str
    price: Optional[float]
    trigger_price: Optional[float]
    time_in_force: str
    def __init__(
        self,
        symbol: str,
        side: str,
        quantity: float,
        order_type: str = "market",
        price: Optional[float] = None,
        time_in_force: str = "day",
        venue: str = "NSE",
        trigger_price: Optional[float] = None,
    ) -> None: ...
    @staticmethod
    def market_buy(symbol: str, quantity: float, venue: str = "NSE") -> OrderIntent: ...
    @staticmethod
    def market_sell(symbol: str, quantity: float, venue: str = "NSE") -> OrderIntent: ...
    @staticmethod
    def limit_buy(
        symbol: str, quantity: float, price: float, venue: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def limit_sell(
        symbol: str, quantity: float, price: float, venue: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def stop_buy(
        symbol: str, quantity: float, trigger_price: float, venue: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def stop_sell(
        symbol: str, quantity: float, trigger_price: float, venue: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def stop_limit_buy(
        symbol: str, quantity: float, trigger_price: float, limit_price: float, venue: str = "NSE"
    ) -> OrderIntent: ...
    @staticmethod
    def stop_limit_sell(
        symbol: str, quantity: float, trigger_price: float, limit_price: float, venue: str = "NSE"
    ) -> OrderIntent: ...
    def __repr__(self) -> str: ...

class RustSmaCrossover:
    def __init__(self, fast: int = 3, slow: int = 8, qty: float = 1.0) -> None: ...
    def on_bar(self, bar: Bar) -> Optional[Tuple[str, float]]: ...
    def on_close(self, close: float) -> Optional[Tuple[str, float]]: ...
    @property
    def position(self) -> float: ...
    @property
    def intent_count(self) -> int: ...
    def intents(self) -> List[Tuple[str, float]]: ...
