"""Portfolio and Account domain models (mirrors honba_entities::Portfolio)."""

from __future__ import annotations

from dataclasses import dataclass, field

from honba.domain.instrument import InstrumentId
from honba.domain.money import Currency, Money
from honba.domain.position import Position


@dataclass(slots=True)
class Account:
    """A named account holding integer ``Money`` cash and positions (ADR 0011).

    Mirrors ``honba_entities::Account``: credits and debits are ``Money`` in the
    account's currency, exact in minor units; a currency mismatch raises
    ``ValueError`` and leaves the balance unchanged. As before, amounts must be
    ``>= 0`` and a debit may not overdraw the account.
    """

    name: str
    cash: Money = field(default_factory=lambda: Money.zero(Currency.INR))
    positions: dict[InstrumentId, Position] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not isinstance(self.cash, Money):
            raise TypeError(f"cash must be Money, got {type(self.cash).__name__}")

    @property
    def currency(self) -> Currency:
        return self.cash.currency

    def credit(self, amount: Money) -> None:
        cash = self.cash + amount
        if amount.amount < 0:
            raise ValueError(f"credit amount must be >= 0, got {amount}")
        self.cash = cash

    def debit(self, amount: Money) -> None:
        cash = self.cash - amount
        if amount.amount < 0:
            raise ValueError(f"debit amount must be >= 0, got {amount}")
        if cash.amount < 0:
            raise ValueError(f"insufficient funds: cash={self.cash}, debit={amount}")
        self.cash = cash

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
