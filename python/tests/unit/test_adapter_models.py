"""Unit tests for the canonical adapter-boundary value types (E1-S1).

These are the types an adapter must return and the engine must consume; they hold
no broker wire data (that stops at the adapter boundary, see ``boundary.py``).
"""

from __future__ import annotations

import math

import pytest

from honba.adapters.models import (
    DepthLevel,
    Funds,
    Holding,
    MarginReport,
    MarketDepth,
    OrderReport,
    Product,
    RunMode,
    SessionInfo,
    StreamMode,
    Subscription,
)
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderSide, OrderStatus, OrderType, TimeInForce

IID = InstrumentId("RELIANCE", "NSE")


class TestRunMode:
    def test_mode_is_per_run_not_a_global_flag(self) -> None:
        assert {m.value for m in RunMode} == {"backtest", "paper", "live"}

    def test_session_states_its_mode(self) -> None:
        session = SessionInfo(user_id="U1", mode=RunMode.PAPER)
        assert session.mode is RunMode.PAPER


class TestSessionInfo:
    def test_requires_non_empty_user_id(self) -> None:
        with pytest.raises(ValueError, match="user_id"):
            SessionInfo(user_id="", mode=RunMode.LIVE)

    def test_expiry_is_optional_but_not_negative(self) -> None:
        assert SessionInfo(user_id="U1", mode=RunMode.LIVE).expires_at is None
        assert SessionInfo(user_id="U1", mode=RunMode.LIVE, expires_at=0).expires_at == 0
        with pytest.raises(ValueError, match="expires_at"):
            SessionInfo(user_id="U1", mode=RunMode.LIVE, expires_at=-1)

    def test_accounts_default_to_empty_and_are_a_tuple(self) -> None:
        assert SessionInfo(user_id="U1", mode=RunMode.LIVE).accounts == ()

    def test_is_frozen(self) -> None:
        session = SessionInfo(user_id="U1", mode=RunMode.LIVE)
        with pytest.raises(AttributeError):
            session.user_id = "U2"  # type: ignore[misc]


class TestDepthLevel:
    def test_price_must_be_positive_and_finite(self) -> None:
        assert DepthLevel(price=1.0, quantity=0.0).price == 1.0
        with pytest.raises(ValueError, match="price"):
            DepthLevel(price=0.0, quantity=1.0)
        with pytest.raises(ValueError, match="price"):
            DepthLevel(price=math.inf, quantity=1.0)
        with pytest.raises(ValueError, match="price"):
            DepthLevel(price=math.nan, quantity=1.0)

    def test_quantity_may_be_zero_but_not_negative_or_nan(self) -> None:
        assert DepthLevel(price=1.0, quantity=0.0).quantity == 0.0
        with pytest.raises(ValueError, match="quantity"):
            DepthLevel(price=1.0, quantity=-1.0)
        with pytest.raises(ValueError, match="quantity"):
            DepthLevel(price=1.0, quantity=math.nan)


class TestMarketDepth:
    @staticmethod
    def _depth(bids: list[DepthLevel], asks: list[DepthLevel]) -> MarketDepth:
        return MarketDepth(instrument_id=IID, ts=1, bids=tuple(bids), asks=tuple(asks))

    def test_best_prices_and_mid(self) -> None:
        depth = self._depth(
            [DepthLevel(99.0, 5.0), DepthLevel(98.0, 10.0)],
            [DepthLevel(101.0, 4.0), DepthLevel(102.0, 10.0)],
        )
        assert depth.best_bid == DepthLevel(99.0, 5.0)
        assert depth.best_ask == DepthLevel(101.0, 4.0)
        assert depth.mid_price == 100.0

    def test_bids_must_descend_and_asks_must_ascend(self) -> None:
        with pytest.raises(ValueError, match="bids"):
            self._depth([DepthLevel(98.0, 1.0), DepthLevel(99.0, 1.0)], [DepthLevel(101.0, 1.0)])
        with pytest.raises(ValueError, match="asks"):
            self._depth([DepthLevel(99.0, 1.0)], [DepthLevel(102.0, 1.0), DepthLevel(101.0, 1.0)])

    def test_one_side_may_be_empty(self) -> None:
        depth = self._depth([DepthLevel(99.0, 1.0)], [])
        assert depth.best_ask is None
        assert depth.mid_price is None

    def test_both_sides_empty_is_allowed(self) -> None:
        depth = self._depth([], [])
        assert depth.best_bid is None and depth.best_ask is None

    def test_crossed_book_is_rejected(self) -> None:
        with pytest.raises(ValueError, match="crossed"):
            self._depth([DepthLevel(101.0, 1.0)], [DepthLevel(99.0, 1.0)])

    def test_ts_must_not_be_negative(self) -> None:
        with pytest.raises(ValueError, match="ts"):
            MarketDepth(instrument_id=IID, ts=-1, bids=(), asks=())

    def test_accepts_plain_sequences_as_lists(self) -> None:
        # Adapters build these from parsed JSON; a list must not silently pass validation.
        with pytest.raises((TypeError, ValueError)):
            MarketDepth(instrument_id=IID, ts=1, bids=[DepthLevel(99.0, 1.0)], asks=[])


class TestFunds:
    def test_all_amounts_finite_and_non_negative(self) -> None:
        funds = Funds(available_cash=1000.0, opening_balance=5000.0)
        assert funds.margin_used == 0.0 and funds.collateral == 0.0
        for field in ("available_cash", "opening_balance", "margin_used", "collateral"):
            for bad in (-1.0, math.nan, math.inf):
                with pytest.raises(ValueError, match=field):
                    Funds(**{"available_cash": 1.0, "opening_balance": 1.0, field: bad})

    def test_payin_and_payout_are_reported_separately(self) -> None:
        funds = Funds(available_cash=1.0, opening_balance=2.0, payin=3.0, payout=4.0)
        assert (funds.payin, funds.payout) == (3.0, 4.0)

    def test_currency_defaults_to_the_settlement_currency(self) -> None:
        assert Funds(available_cash=0.0, opening_balance=0.0).currency == "INR"
        assert Funds(available_cash=0.0, opening_balance=0.0, currency="USD").currency == "USD"

    def test_currency_must_not_be_blank(self) -> None:
        with pytest.raises(ValueError, match="currency"):
            Funds(available_cash=0.0, opening_balance=0.0, currency="")


class TestHolding:
    def test_quantity_may_be_zero_but_amounts_may_not_be_negative(self) -> None:
        assert Holding(instrument_id=IID, quantity=0.0).quantity == 0.0
        with pytest.raises(ValueError, match="average_price"):
            Holding(instrument_id=IID, quantity=1.0, average_price=-1.0)
        with pytest.raises(ValueError, match="last_price"):
            Holding(instrument_id=IID, quantity=1.0, last_price=-1.0)

    def test_reports_whether_the_demat_balance_is_zero(self) -> None:
        assert Holding(instrument_id=IID, quantity=0.0).is_zero
        assert not Holding(instrument_id=IID, quantity=1.0).is_zero


class TestMarginReport:
    def test_account_level_report_has_no_instrument(self) -> None:
        report = MarginReport(initial=1000.0, maintenance=800.0)
        assert report.instrument_id is None
        assert report.currency == "INR"

    def test_instrument_level_report_names_the_instrument(self) -> None:
        assert MarginReport(initial=1.0, maintenance=1.0, instrument_id=IID).instrument_id == IID

    def test_maintenance_never_exceeds_initial(self) -> None:
        assert MarginReport(initial=1000.0, maintenance=1000.0).maintenance == 1000.0
        with pytest.raises(ValueError, match="maintenance"):
            MarginReport(initial=1000.0, maintenance=1000.1)

    def test_amounts_are_finite_and_non_negative(self) -> None:
        for field in ("initial", "maintenance"):
            for bad in (-1.0, math.nan, math.inf):
                with pytest.raises(ValueError, match=field):
                    MarginReport(**{"initial": 1.0, "maintenance": 1.0, field: bad})

    def test_excess_over_maintenance_is_the_headroom(self) -> None:
        assert MarginReport(initial=1000.0, maintenance=800.0).headroom == 200.0


class TestOrderReport:
    @staticmethod
    def _report(**kwargs: object) -> OrderReport:
        base: dict[str, object] = {
            "order_id": "c-1",
            "instrument_id": IID,
            "side": OrderSide.BUY,
            "quantity": 100.0,
            "status": OrderStatus.ACCEPTED,
            "product": Product.DELIVERY,
        }
        base.update(kwargs)
        return OrderReport(**base)  # type: ignore[arg-type]

    def test_order_id_must_not_be_blank(self) -> None:
        with pytest.raises(ValueError, match="order_id"):
            self._report(order_id="")

    def test_quantity_must_be_positive_and_filled_cannot_exceed_it(self) -> None:
        with pytest.raises(ValueError, match="quantity"):
            self._report(quantity=0.0)
        with pytest.raises(ValueError, match="filled_quantity"):
            self._report(filled_quantity=101.0)
        with pytest.raises(ValueError, match="filled_quantity"):
            self._report(filled_quantity=-1.0)

    def test_filled_status_requires_the_whole_quantity(self) -> None:
        report = self._report(
            status=OrderStatus.FILLED, filled_quantity=100.0, average_price=2450.5
        )
        assert report.average_price == 2450.5
        with pytest.raises(ValueError, match="filled_quantity"):
            self._report(status=OrderStatus.FILLED, filled_quantity=50.0, average_price=2450.5)

    def test_average_price_is_required_once_something_filled(self) -> None:
        with pytest.raises(ValueError, match="average_price"):
            self._report(status=OrderStatus.PARTIALLY_FILLED, filled_quantity=10.0)
        assert (
            self._report(
                status=OrderStatus.PARTIALLY_FILLED, filled_quantity=10.0, average_price=2450.5
            ).filled_quantity
            == 10.0
        )

    def test_reject_reason_iff_rejected(self) -> None:
        assert self._report(status=OrderStatus.REJECTED, reject_reason="insufficient funds")
        with pytest.raises(ValueError, match="reject_reason"):
            self._report(status=OrderStatus.REJECTED)
        with pytest.raises(ValueError, match="reject_reason"):
            self._report(status=OrderStatus.ACCEPTED, reject_reason="insufficient funds")
        with pytest.raises(ValueError, match="reject_reason"):
            self._report(status=OrderStatus.REJECTED, reject_reason="")

    def test_a_rejection_is_a_normal_result_not_an_exception(self) -> None:
        # The broker refusing an order is data; an unsupported capability is an error.
        report = self._report(status=OrderStatus.REJECTED, reject_reason="price band")
        assert isinstance(report, OrderReport)

    def test_optional_prices_must_be_finite_when_present(self) -> None:
        assert self._report(order_type=OrderType.LIMIT, price=2450.5).price == 2450.5
        with pytest.raises(ValueError, match="price"):
            self._report(price=math.nan)
        with pytest.raises(ValueError, match="trigger_price"):
            self._report(trigger_price=math.inf)

    def test_defaults_match_the_canonical_order_vocabulary(self) -> None:
        report = self._report()
        assert report.order_type is OrderType.MARKET
        assert report.time_in_force is TimeInForce.DAY
        assert report.filled_quantity == 0.0
        assert report.ts_event == 0


class TestSubscription:
    def test_requires_a_mode_at_least_one_instrument_and_unique_ids(self) -> None:
        sub = Subscription(id="s1", instruments=(IID,), mode=StreamMode.QUOTE)
        assert sub.mode is StreamMode.QUOTE
        with pytest.raises(ValueError, match="id"):
            Subscription(id="", instruments=(IID,), mode=StreamMode.QUOTE)
        with pytest.raises(ValueError, match="instruments"):
            Subscription(id="s1", instruments=(), mode=StreamMode.QUOTE)
        with pytest.raises(ValueError, match="instruments"):
            Subscription(id="s1", instruments=(IID, IID), mode=StreamMode.QUOTE)

    def test_stream_modes_are_the_normalised_feed_modes(self) -> None:
        assert {m.value for m in StreamMode} == {"ltp", "quote", "depth"}
