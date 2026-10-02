"""Portfolio and Account domain models (mirrors honba_entities::Portfolio)."""

from __future__ import annotations

from dataclasses import dataclass, field
from honba.domain.instrument import InstrumentId
from honba.domain.position import Position


@dataclass(slots=True)
class Account:
    """A named account holding cash and positions."""

    name: str
    cash: float = 0.0
    currency: str = "INR"
    positions: dict[InstrumentId, Position] = field(default_factory=dict)

    def credit(self, amount: float) -> None:
        if amount < 0:
            raise ValueError(f"credit amount must be >= 0, got {amount}")
        self.cash += amount

    def debit(self, amount: float) -> None:
        if amount < 0:
            raise ValueError(f"debit amount must be >= 0, got {amount}")
        if self.cash < amount:
            raise ValueError(f"insufficient funds: cash={self.cash}, debit={amount}")
        self.cash -= amount

    def get_position(self, instrument_id: InstrumentId) -> Position | None:
        return self.positions.get(instrument_id)

    def upsert_position(self, position: Position) -> None:
        self.positions[position.instrument_id] = position


@dataclass(slots=True)
class Portfolio:
    """A collection of accounts."""

    accounts: dict[str, Account] = field(default_factory=dict)

    def add_account(self, account: Account) -> None:
        self.accounts[account.name] = account

    def account(self, name: str) -> Account | None:
        return self.accounts.get(name)
