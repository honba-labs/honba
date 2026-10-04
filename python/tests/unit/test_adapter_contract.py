"""Unit tests for the shared adapter contract suite (E1-S1).

The suite is only worth having if it rejects adapters that break the contract, so most of
this file is mutations: a ``FakeAdapter`` with exactly one thing wrong. Each must be caught.
"""

from __future__ import annotations

import datetime as dt

import pytest

from honba.adapters.contract import ContractViolation, verify_adapter_contract
from honba.adapters.errors import AdapterError, CapabilityError
from honba.adapters.models import OrderReport, Product, StreamMode
from honba.adapters.testing import FakeAdapter
from honba.domain.instrument import InstrumentId
from honba.domain.order import OrderIntent, OrderSide
from honba.domain.tick import QuoteTick
from honba.domain.trade import Trade

UNKNOWN = InstrumentId("NOSUCH", "NSE")


async def test_fake_adapter_satisfies_the_contract() -> None:
    await verify_adapter_contract(FakeAdapter())


class TestTheSuiteRejectsBrokenAdapters:
    async def test_rejects_an_adapter_that_does_not_implement_the_roles(self) -> None:
        class NotAnAdapter:
            name = "broken"

            def capabilities(self) -> object:  # pragma: no cover - never reached
                raise AssertionError

        with pytest.raises(ContractViolation, match="Adapter"):
            await verify_adapter_contract(NotAnAdapter())  # type: ignore[arg-type]

    async def test_rejects_an_adapter_missing_a_role_method(self) -> None:
        class NoFunds(FakeAdapter):
            funds = None  # type: ignore[assignment]

        with pytest.raises(ContractViolation, match="funds"):
            await verify_adapter_contract(NoFunds())

    async def test_rejects_a_leaked_broker_wire_type(self) -> None:
        class RawFunds(FakeAdapter):
            async def funds(self) -> dict[str, float]:  # type: ignore[override]
                return {"available_cash": 1.0}

        with pytest.raises(ContractViolation, match="Funds"):
            await verify_adapter_contract(RawFunds())

    async def test_rejects_a_quote_that_is_not_a_quote(self) -> None:
        class RawQuote(FakeAdapter):
            async def quote(self, instrument_id: InstrumentId) -> object:  # type: ignore[override]
                return {"bid": 1.0, "ask": 2.0}

        with pytest.raises(ContractViolation, match="QuoteTick"):
            await verify_adapter_contract(RawQuote())

    async def test_rejects_an_unknown_instrument_answered_with_a_key_error(self) -> None:
        class LeakyLookups(FakeAdapter):
            async def quote(self, instrument_id: InstrumentId) -> QuoteTick:
                if instrument_id not in self._instruments:
                    raise KeyError(instrument_id)
                return await super().quote(instrument_id)

        with pytest.raises(ContractViolation, match="AdapterError"):
            await verify_adapter_contract(LeakyLookups())

    async def test_rejects_an_unknown_order_answered_with_a_key_error(self) -> None:
        class LeakyOrders(FakeAdapter):
            async def order_status(self, order_id: str) -> object:  # type: ignore[override]
                raise KeyError(order_id)

        with pytest.raises(ContractViolation, match="AdapterError"):
            await verify_adapter_contract(LeakyOrders())

    async def test_rejects_an_unsupported_capability_answered_with_attribute_error(self) -> None:
        class HalfBaked(FakeAdapter):
            async def cancel_all(self, **_: object) -> None:
                raise AttributeError("cancel_all")

        with pytest.raises(ContractViolation, match="CapabilityError"):
            await verify_adapter_contract(HalfBaked())

    async def test_rejects_a_refusal_raised_as_something_other_than_an_adapter_error(
        self,
    ) -> None:
        class RudeRefusals(FakeAdapter):
            async def place_order(
                self,
                intent: OrderIntent,
                *,
                product: Product,
                client_order_id: str | None = None,
            ) -> object:  # type: ignore[override]
                raise ValueError("insufficient funds")

        with pytest.raises(ContractViolation, match="AdapterError"):
            await verify_adapter_contract(RudeRefusals())

    async def test_an_unaffordable_order_is_reported_not_raised(self) -> None:
        # The rule is that a refusal is data: the adapter reports it and the suite passes.
        adapter = FakeAdapter(opening_balance=1.0)
        await verify_adapter_contract(adapter)
        await adapter.connect()
        try:
            report = await adapter.place_order(
                OrderIntent.market_buy(InstrumentId("RELIANCE", "NSE"), 100.0),
                product=Product.DELIVERY,
            )
            assert report.status.value == "rejected"
            assert report.reject_reason
        finally:
            await adapter.disconnect()

    async def test_rejects_a_report_missing_from_the_order_book(self) -> None:
        class Amnesia(FakeAdapter):
            async def orders(self) -> list[OrderReport]:
                self.require_connected()  # keeps every other rule intact
                return []

        with pytest.raises(ContractViolation, match="order book"):
            await verify_adapter_contract(Amnesia())

    async def test_rejects_order_status_disagreeing_with_the_placement_report(self) -> None:
        class Drifting(FakeAdapter):
            async def order_status(self, order_id: str) -> object:  # type: ignore[override]
                report = await super().order_status(order_id)
                return type(report)(
                    order_id=report.order_id,
                    instrument_id=report.instrument_id,
                    side=report.side,
                    quantity=report.quantity,
                    status=report.status,
                    product=report.product,
                    ts_event=report.ts_event + 1,
                )

        with pytest.raises(ContractViolation, match="order_status"):
            await verify_adapter_contract(Drifting())

    async def test_rejects_a_repeat_client_order_id_creating_a_second_order(self) -> None:
        class Duplicating(FakeAdapter):
            async def place_order(
                self,
                intent: OrderIntent,
                *,
                product: Product,
                client_order_id: str | None = None,
            ) -> object:  # type: ignore[override]
                return await super().place_order(intent, product=product, client_order_id=None)

        with pytest.raises(ContractViolation, match="idempotent"):
            await verify_adapter_contract(Duplicating())

    async def test_rejects_trades_that_do_not_match_the_fill(self) -> None:
        class PhantomFills(FakeAdapter):
            async def trades(self) -> list[Trade]:
                return []

        with pytest.raises(ContractViolation, match="trade book"):
            await verify_adapter_contract(PhantomFills())

    async def test_rejects_positions_that_ignore_the_fill(self) -> None:
        class BlindPositions(FakeAdapter):
            async def positions(self) -> list[object]:  # type: ignore[override]
                return []

        with pytest.raises(ContractViolation, match="position book"):
            await verify_adapter_contract(BlindPositions())

    async def test_rejects_bars_out_of_order(self) -> None:
        class TimeTraveller(FakeAdapter):
            async def historical_bars(
                self,
                instrument_id: InstrumentId,
                *,
                timeframe: str,
                start: dt.datetime,
                end: dt.datetime,
            ) -> list[object]:  # type: ignore[override]
                bars = await super().historical_bars(
                    instrument_id, timeframe=timeframe, start=start, end=end
                )
                return list(reversed(bars))

        with pytest.raises(ContractViolation, match="ascending"):
            await verify_adapter_contract(TimeTraveller())

    async def test_rejects_a_capability_name_that_disagrees_with_the_registry_key(self) -> None:
        class Misnamed(FakeAdapter):
            def capabilities(self) -> object:  # type: ignore[override]
                caps = super().capabilities()
                return type(caps)(**{**_as_kwargs(caps), "name": "other"})

        with pytest.raises(ContractViolation, match="name"):
            await verify_adapter_contract(Misnamed())

    async def test_rejects_working_before_connect(self) -> None:
        class Eager(FakeAdapter):
            def require_connected(self) -> None:
                return None  # no guard at all

        with pytest.raises(ContractViolation, match="not connected"):
            await verify_adapter_contract(Eager())

    async def test_rejects_operations_after_disconnect(self) -> None:
        class Leaky(FakeAdapter):
            async def disconnect(self) -> None:
                await super().disconnect()
                self._connected = True  # keeps claiming to be connected

        with pytest.raises(ContractViolation, match="disconnect"):
            await verify_adapter_contract(Leaky())

    async def test_reports_the_violation_with_the_call_that_broke_it(self) -> None:
        class RudeRefusals(FakeAdapter):
            async def funds(self) -> object:  # type: ignore[override]
                raise RuntimeError("connection reset by peer")

        with pytest.raises(ContractViolation, match="funds"):
            await verify_adapter_contract(RudeRefusals(opening_balance=1.0))


def _as_kwargs(caps: object) -> dict[str, object]:
    import dataclasses

    return {f.name: getattr(caps, f.name) for f in dataclasses.fields(caps)}  # type: ignore[arg-type]


class TestCapabilityHonestyIsChecked:
    async def test_an_adapter_may_declare_a_reduced_capability_set(self) -> None:
        from honba.adapters.capabilities import Capability

        adapter = FakeAdapter()
        assert Capability.DEPTH not in adapter.capabilities().features
        await verify_adapter_contract(adapter)

    async def test_declaring_a_capability_without_implementing_it_is_caught(self) -> None:
        from honba.adapters.capabilities import AdapterCapabilities, Capability

        class Overstating(FakeAdapter):
            def capabilities(self) -> AdapterCapabilities:
                caps = super().capabilities()
                return AdapterCapabilities(
                    name=caps.name,
                    exchanges=caps.exchanges,
                    products=caps.products,
                    order_types=caps.order_types,
                    features=caps.features | {Capability.DEPTH},
                )

            async def depth(self, instrument_id: InstrumentId, levels: int = 5) -> object:
                raise NotImplementedError

        with pytest.raises(ContractViolation, match="depth"):
            await verify_adapter_contract(Overstating())


class TestStreamContract:
    async def test_subscribe_returns_a_subscription_for_exactly_what_was_asked(
        self,
    ) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        try:
            instrument = (await adapter.instruments())[0].instrument_id
            subscription = await adapter.subscribe(
                (instrument,), mode=StreamMode.QUOTE, callback=lambda _: None
            )
            assert subscription.instruments == (instrument,)
            assert subscription.mode is StreamMode.QUOTE
        finally:
            await adapter.disconnect()

    async def test_subscribing_to_an_unsupported_mode_is_a_capability_error(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        try:
            with pytest.raises(CapabilityError):
                await adapter.subscribe(
                    (InstrumentId("RELIANCE", "NSE"),),
                    mode=StreamMode.DEPTH,
                    callback=lambda _: None,
                )
        finally:
            await adapter.disconnect()

    async def test_unsubscribing_an_unknown_id_is_a_typed_error(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        try:
            with pytest.raises(AdapterError, match="sub-"):
                await adapter.unsubscribe("sub-nope")
        finally:
            await adapter.disconnect()


class TestSuiteIsRepeatable:
    async def test_the_same_adapter_can_be_verified_twice(self) -> None:
        adapter = FakeAdapter()
        await verify_adapter_contract(adapter)
        await verify_adapter_contract(adapter)

    async def test_the_suite_leaves_the_adapter_disconnected(self) -> None:
        adapter = FakeAdapter()
        await verify_adapter_contract(adapter)
        assert adapter.is_connected() is False

    async def test_a_read_only_adapter_passes(self) -> None:
        from honba.adapters.capabilities import AdapterCapabilities, Capability

        caps = FakeAdapter().capabilities()
        read_only = AdapterCapabilities(
            name="reader",
            exchanges=caps.exchanges,
            products=caps.products,
            order_types=caps.order_types,
            features=frozenset(
                {Capability.QUOTES, Capability.INSTRUMENT_MASTER, Capability.HISTORICAL_BARS}
            ),
        )

        class ReadOnly(FakeAdapter):
            def capabilities(self) -> AdapterCapabilities:
                return read_only

        await verify_adapter_contract(ReadOnly(name="reader"))


class TestSellSideIsProbedToo:
    async def test_a_sell_reduces_the_position_it_finds(self) -> None:
        adapter = FakeAdapter()
        await verify_adapter_contract(adapter)
        instrument = InstrumentId("RELIANCE", "NSE")
        await adapter.connect()
        try:
            await adapter.place_order(
                OrderIntent.market_buy(instrument, 10.0), product=Product.DELIVERY
            )
            before = await adapter.funds()
            report = await adapter.place_order(
                OrderIntent.market_sell(instrument, 4.0), product=Product.DELIVERY
            )
            assert report.status.value in {"filled", "accepted", "submitted"}
            after = await adapter.funds()
            assert after.available_cash > before.available_cash
        finally:
            await adapter.disconnect()

    async def test_order_side_is_part_of_the_report(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        try:
            report = await adapter.place_order(
                OrderIntent(
                    instrument_id=InstrumentId("RELIANCE", "NSE"), side=OrderSide.SELL, quantity=1.0
                ),
                product=Product.INTRADAY,
            )
            assert report.side is OrderSide.SELL
        finally:
            await adapter.disconnect()

    async def test_limit_orders_rest_when_they_do_not_cross(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        try:
            report = await adapter.place_order(
                OrderIntent.limit_buy(InstrumentId("RELIANCE", "NSE"), 1.0, 1.0),
                product=Product.DELIVERY,
            )
            assert report.status.value == "accepted"
        finally:
            await adapter.disconnect()

    async def test_unsupported_order_type_is_refused_before_any_request(self) -> None:
        adapter = FakeAdapter()
        await adapter.connect()
        try:
            with pytest.raises(CapabilityError, match="stop_limit"):
                await adapter.place_order(
                    OrderIntent.stop_limit_buy(InstrumentId("RELIANCE", "NSE"), 1.0, 1.0, 2.0),
                    product=Product.DELIVERY,
                )
        finally:
            await adapter.disconnect()
