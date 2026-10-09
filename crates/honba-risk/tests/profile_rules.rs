//! The stage through its public API with the India profile adapter (no fakes of the rules).

use std::sync::Arc;

use honba_entities::{Currency, Instrument, InstrumentKind, Money};
use honba_market::IndiaMarketProfile;
use honba_messages::{Exchange, InstrumentId, OrderId, OrderSide, TradingState, UnixNanos};
use honba_risk::{
    OrderRateLimit, PriceField, ProfileRulesSource, RiskCheck, RiskDecision, RiskLimits,
    RiskRefusal, RiskRequest, RiskStage, RulesSource,
};

fn reliance() -> InstrumentId {
    InstrumentId::new("RELIANCE", Exchange::new("NSE"))
}

fn stage(limits: RiskLimits) -> RiskStage {
    let instrument = Instrument::new(reliance(), InstrumentKind::Equity, Currency::Inr, 1.0, 0.05);
    let source = ProfileRulesSource::new(Arc::new(IndiaMarketProfile::default()), [instrument]);
    RiskStage::new(limits, Currency::Inr, Arc::new(source)).unwrap()
}

fn req() -> RiskRequest {
    RiskRequest {
        order_id: OrderId::new("O1"),
        instrument_id: reliance(),
        side: OrderSide::Buy,
        quantity: 10.0,
        price: Some(2500.05),
        trigger_price: None,
        reference_price: Some(2500.0),
        adv: None,
        position: 0.0,
        trading_state: TradingState::Active,
        ts: UnixNanos::new(1),
        last_feed_ts: None,
    }
}

fn refused(d: RiskDecision) -> RiskRefusal {
    match d {
        RiskDecision::Refused(r) => r,
        RiskDecision::Approved => panic!("expected refusal"),
    }
}

#[test]
fn india_cash_equity_order_flows_through_all_rules() {
    let mut s = stage(RiskLimits::default());
    assert_eq!(s.check(&req()), RiskDecision::Approved);

    let off_tick = RiskRequest {
        price: Some(2500.03),
        ..req()
    };
    assert!(matches!(
        refused(s.check(&off_tick)),
        RiskRefusal::TickSize {
            field: PriceField::Price,
            ..
        }
    ));

    let fractional = RiskRequest {
        quantity: 1.5,
        ..req()
    };
    assert!(matches!(
        refused(s.check(&fractional)),
        RiskRefusal::LotMultiple { .. }
    ));
}

#[test]
fn india_pack_supplies_no_band_so_wide_prices_pass() {
    let source = ProfileRulesSource::new(
        Arc::new(IndiaMarketProfile::default()),
        [Instrument::new(
            reliance(),
            InstrumentKind::Equity,
            Currency::Inr,
            1.0,
            0.05,
        )],
    );
    let (rules, band) = source.rules(&reliance()).expect("registered");
    assert_eq!((rules.lot_size, rules.tick_size), (1.0, 0.05));
    assert!(band.is_none());
    let mut s = stage(RiskLimits::default());
    let wide = RiskRequest {
        price: Some(99999.0),
        ..req()
    };
    assert_eq!(s.check(&wide), RiskDecision::Approved);
}

#[test]
fn unknown_instrument_and_state_rules() {
    let mut s = stage(RiskLimits::default());
    let other = InstrumentId::new("TCS", Exchange::new("NSE"));
    let r = RiskRequest {
        instrument_id: other.clone(),
        ..req()
    };
    assert_eq!(
        refused(s.check(&r)),
        RiskRefusal::InstrumentUnknown {
            instrument_id: other
        }
    );

    let halted = RiskRequest {
        trading_state: TradingState::Halted,
        ..req()
    };
    assert_eq!(refused(s.check(&halted)), RiskRefusal::TradingHalted);

    // Long 100, working sell 60 (position passed as 40), sell 50 more: refused.
    let in_flight = RiskRequest {
        trading_state: TradingState::Reducing,
        side: OrderSide::Sell,
        quantity: 50.0,
        position: 40.0,
        ..req()
    };
    assert!(matches!(
        refused(s.check(&in_flight)),
        RiskRefusal::ReduceOnly { .. }
    ));
}

#[test]
fn notional_limit_with_reference_price_fallback() {
    let mut s = stage(RiskLimits {
        max_notional: Some(20_000.0),
        order_rate: None,
        max_participation: None,
        stale_after_ms: None,
    });
    // Market order: priced from the reference (10 * 2500 = 25_000 > 20_000).
    let market = RiskRequest {
        price: None,
        ..req()
    };
    let got = refused(s.check(&market));
    assert_eq!(
        got,
        RiskRefusal::MaxNotional {
            notional: Money::from_major_f64(25_000.0, Currency::Inr).unwrap(),
            limit: Money::from_major_f64(20_000.0, Currency::Inr).unwrap(),
        }
    );
    assert_eq!(got.error_code().as_str(), "risk_max_notional_exceeded");

    let unpriceable = RiskRequest {
        price: None,
        reference_price: None,
        ..req()
    };
    assert_eq!(
        refused(s.check(&unpriceable)).context()["reason"],
        "unpriceable"
    );
}

#[test]
fn india_order_rate_limit_enforced_in_event_time() {
    const MS: u64 = 1_000_000;
    let mut s = stage(RiskLimits {
        max_notional: None,
        order_rate: Some(OrderRateLimit {
            max_orders: 2,
            window_ms: 1000,
        }),
        max_participation: None,
        stale_after_ms: None,
    });
    let at = |ts: u64| RiskRequest {
        ts: UnixNanos::new(ts),
        ..req()
    };
    assert_eq!(s.check(&at(1)), RiskDecision::Approved);
    // An off-tick order is refused by a shape rule and takes no slot.
    let off_tick = RiskRequest {
        price: Some(2500.03),
        ..at(2)
    };
    assert!(matches!(
        refused(s.check(&off_tick)),
        RiskRefusal::TickSize { .. }
    ));
    assert_eq!(s.check(&at(3)), RiskDecision::Approved);

    let got = refused(s.check(&at(4)));
    assert_eq!(
        got,
        RiskRefusal::OrderRate {
            count: 2,
            max_orders: 2,
            window_ms: 1000
        }
    );
    assert_eq!(got.error_code().as_str(), "risk_order_rate_exceeded");
    let c = got.context();
    assert_eq!(
        (
            c["rule"].as_str(),
            c["count"].as_u64(),
            c["window_ms"].as_u64()
        ),
        (Some("order_rate"), Some(2), Some(1000))
    );

    // Half-open edge: the order at ts=1 leaves the window exactly 1000 ms later.
    assert_eq!(s.check(&at(1 + 1000 * MS)), RiskDecision::Approved);
    assert_eq!(s.limits().order_rate.unwrap().max_orders, 2);
}
