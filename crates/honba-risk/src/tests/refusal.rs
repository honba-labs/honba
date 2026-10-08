//! Wire mapping of refusals: rule name, error code and context.

use honba_entities::{Currency, Money};
use honba_messages::{ErrorCode, Exchange, InstrumentId, OrderSide};

use crate::{PriceField, RiskRefusal};

fn inr(major: f64) -> Money {
    Money::from_major_f64(major, Currency::Inr).unwrap()
}

fn all() -> Vec<(RiskRefusal, &'static str, ErrorCode)> {
    vec![
        (
            RiskRefusal::TradingHalted,
            "trading_halted",
            ErrorCode::RiskTradingHalted,
        ),
        (
            RiskRefusal::ReduceOnly {
                position: 40.0,
                side: OrderSide::Sell,
                quantity: 50.0,
            },
            "reduce_only",
            ErrorCode::RiskReduceOnlyViolation,
        ),
        (
            RiskRefusal::InstrumentUnknown {
                instrument_id: InstrumentId::new("N", Exchange::new("NSE")),
            },
            "instrument_unknown",
            ErrorCode::RiskInstrumentUnknown,
        ),
        (
            RiskRefusal::QuantityBelowMin {
                quantity: 1.0,
                min: 2.0,
            },
            "quantity_below_min",
            ErrorCode::RiskQuantityBelowMin,
        ),
        (
            RiskRefusal::QuantityOverFreeze {
                quantity: 3.0,
                max: 2.0,
            },
            "quantity_over_freeze",
            ErrorCode::RiskQuantityOverFreeze,
        ),
        (
            RiskRefusal::LotMultiple {
                quantity: 3.0,
                lot: 2.0,
            },
            "lot_multiple",
            ErrorCode::RiskLotMultipleViolation,
        ),
        (
            RiskRefusal::TickSize {
                field: PriceField::Price,
                price: 1.01,
                tick: 0.05,
            },
            "tick_size",
            ErrorCode::RiskTickSizeViolation,
        ),
        (
            RiskRefusal::PriceBand {
                field: PriceField::TriggerPrice,
                price: 1.0,
                lower: 2.0,
                upper: 3.0,
            },
            "price_band",
            ErrorCode::RiskPriceBandExceeded,
        ),
        (
            RiskRefusal::MaxNotional {
                notional: inr(10.0),
                limit: inr(5.0),
            },
            "max_notional",
            ErrorCode::RiskMaxNotionalExceeded,
        ),
        (
            RiskRefusal::MaxNotionalUnpriceable { limit: inr(5.0) },
            "max_notional",
            ErrorCode::RiskMaxNotionalExceeded,
        ),
        (
            RiskRefusal::OrderRate {
                count: 2,
                max_orders: 2,
                window_ms: 1000,
            },
            "order_rate",
            ErrorCode::RiskOrderRateExceeded,
        ),
    ]
}

#[test]
fn every_refusal_maps_to_its_rule_and_error_code() {
    for (refusal, rule, code) in all() {
        assert_eq!(refusal.rule(), rule, "{refusal:?}");
        assert_eq!(refusal.error_code(), code, "{refusal:?}");
        assert_eq!(refusal.context()["rule"], rule, "{refusal:?}");
    }
}

#[test]
fn wire_spelling_of_the_codes() {
    let spellings: Vec<_> = all().iter().map(|(_, _, c)| c.as_str()).collect();
    assert_eq!(
        spellings,
        [
            "risk_trading_halted",
            "risk_reduce_only_violation",
            "risk_instrument_unknown",
            "risk_quantity_below_min",
            "risk_quantity_over_freeze",
            "risk_lot_multiple_violation",
            "risk_tick_size_violation",
            "risk_price_band_exceeded",
            "risk_max_notional_exceeded",
            "risk_max_notional_exceeded",
            "risk_order_rate_exceeded",
        ]
    );
}

#[test]
fn context_carries_the_numbers() {
    let c = RiskRefusal::ReduceOnly {
        position: 40.0,
        side: OrderSide::Sell,
        quantity: 50.0,
    }
    .context();
    assert_eq!(c["position"], 40.0);
    assert_eq!(c["side"], "sell");
    assert_eq!(c["quantity"], 50.0);

    let c = RiskRefusal::PriceBand {
        field: PriceField::TriggerPrice,
        price: 1.0,
        lower: 2.0,
        upper: 3.0,
    }
    .context();
    assert_eq!(c["field"], "trigger_price");
    assert_eq!(
        (
            c["price"].as_f64(),
            c["lower"].as_f64(),
            c["upper"].as_f64()
        ),
        (Some(1.0), Some(2.0), Some(3.0))
    );

    let c = RiskRefusal::MaxNotional {
        notional: inr(10.0),
        limit: inr(5.0),
    }
    .context();
    assert_eq!(c["notional"], 10.0);
    assert_eq!(c["limit"], 5.0);
    assert_eq!(c["currency"], "INR");
    assert!(c.get("reason").is_none());

    let c = RiskRefusal::OrderRate {
        count: 2,
        max_orders: 2,
        window_ms: 1000,
    }
    .context();
    assert_eq!(
        (
            c["count"].as_u64(),
            c["max_orders"].as_u64(),
            c["window_ms"].as_u64()
        ),
        (Some(2), Some(2), Some(1000))
    );
}
