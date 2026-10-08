//! Seeded property tests (ADR 0018 decision 11). A tiny splitmix64 PRNG keeps them
//! deterministic without a new dependency.

use honba_entities::{Currency, Money};
use honba_market::{PriceBand, QuantityViolation};
use honba_messages::{Exchange, InstrumentId, OrderSide, TradingState, UnixNanos};

use super::{req, rules, stage_with, x};
use crate::{
    check_state, OrderRateLimit, RiskCheck, RiskDecision, RiskLimits, RiskRefusal, RiskRequest,
    RiskStage,
};

const SEED: u64 = 0x5EED_0018_C0FF_EE01;

struct Rng(u64);

impl Rng {
    fn next(&mut self) -> u64 {
        self.0 = self.0.wrapping_add(0x9E37_79B9_7F4A_7C15);
        let mut z = self.0;
        z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
        z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
        z ^ (z >> 31)
    }
    fn pick<T: Copy>(&mut self, xs: &[T]) -> T {
        xs[(self.next() % xs.len() as u64) as usize]
    }
    fn opt<T: Copy>(&mut self, xs: &[T]) -> Option<T> {
        let i = self.next() % (xs.len() as u64 + 1);
        xs.get(i as usize).copied()
    }
}

const QTYS: &[f64] = &[0.0, 10.0, 25.0, 30.0, 50.0, 75.0, 100.0, 125.0, 1000.0];
const PRICES: &[f64] = &[
    -5.0, 0.0, 89.95, 90.0, 100.0, 100.03, 100.05, 110.0, 110.05, 111.0,
];
const POSITIONS: &[f64] = &[0.0, 25.0, 100.0, -25.0, -100.0, 40.0];

fn random_request(rng: &mut Rng) -> RiskRequest {
    let instrument_id = if rng.next() % 10 == 0 {
        InstrumentId::new("NOPE", Exchange::new("NSE"))
    } else {
        x()
    };
    RiskRequest {
        instrument_id,
        side: rng.pick(&[OrderSide::Buy, OrderSide::Sell]),
        quantity: rng.pick(QTYS),
        price: rng.opt(PRICES),
        trigger_price: rng.opt(PRICES),
        reference_price: rng.opt(PRICES),
        position: rng.pick(POSITIONS),
        trading_state: rng.pick(&[
            TradingState::Active,
            TradingState::Active,
            TradingState::Reducing,
            TradingState::Halted,
        ]),
        ts: UnixNanos::new(rng.next() % 10_000),
        ..req()
    }
}

const NOTIONAL_LIMIT: f64 = 5000.0;

/// Rule number (1-9) names, in ADR order; quantity rules are 4-6.
const RULE_NAMES: [&str; 9] = [
    "trading_halted",
    "reduce_only",
    "instrument_unknown",
    "quantity_below_min",
    "quantity_over_freeze",
    "lot_multiple",
    "tick_size",
    "price_band",
    "max_notional",
];

/// Each rule evaluated alone, straight from the market rules; returns the refusing rule numbers.
fn refusing_rules(r: &RiskRequest, notional: bool) -> Vec<usize> {
    let mut out = Vec::new();
    match check_state(r) {
        Some(RiskRefusal::TradingHalted) => out.push(1),
        Some(_) => out.push(2),
        None => {}
    }
    let Some((irules, band)) = rules().rules(&r.instrument_id) else {
        out.push(3);
        return out;
    };
    match irules.validate_quantity(r.quantity) {
        Err(QuantityViolation::BelowMin { .. }) => out.push(4),
        Err(QuantityViolation::OverFreeze { .. }) => out.push(5),
        Err(QuantityViolation::NotLotMultiple { .. }) => out.push(6),
        Ok(()) => {}
    }
    let prices = [r.price, r.trigger_price];
    if prices
        .iter()
        .flatten()
        .any(|p| irules.validate_price(*p).is_err())
    {
        out.push(7);
    }
    let band: Option<PriceBand> = band;
    if let Some(b) = band {
        if prices.iter().flatten().any(|p| !b.contains(*p)) {
            out.push(8);
        }
    }
    if notional {
        let limit = Money::from_major_f64(NOTIONAL_LIMIT, Currency::Inr).unwrap();
        let refused = match r.price.or(r.trigger_price).or(r.reference_price) {
            None => true,
            Some(px) => match Money::mul_qty(r.quantity, px, Currency::Inr) {
                Ok(n) => n.minor() > limit.minor(),
                Err(_) => true,
            },
        };
        if refused {
            out.push(9);
        }
    }
    out
}

#[test]
fn rule_order() {
    let mut rng = Rng(SEED);
    let (mut refused, mut approved) = (0, 0);
    for i in 0..10_000 {
        let r = random_request(&mut rng);
        let notional = rng.next() % 2 == 0;
        let mut s = stage_with(RiskLimits {
            max_notional: notional.then_some(NOTIONAL_LIMIT),
            order_rate: None,
        });
        let expected = refusing_rules(&r, notional).into_iter().min();
        match (s.check(&r), expected) {
            (RiskDecision::Approved, None) => approved += 1,
            (RiskDecision::Refused(got), Some(n)) => {
                refused += 1;
                assert_eq!(got.rule(), RULE_NAMES[n - 1], "case {i}: {r:?}");
            }
            (got, want) => panic!("case {i}: {got:?} vs lowest refusing rule {want:?}: {r:?}"),
        }
    }
    // The generator must exercise both outcomes, or the property is vacuous.
    assert!(
        refused > 1000 && approved > 100,
        "{refused} refused, {approved} approved"
    );
}

fn rated() -> RiskStage {
    stage_with(RiskLimits {
        max_notional: None,
        order_rate: Some(OrderRateLimit {
            max_orders: 3,
            window_ms: 1,
        }),
    })
}

#[test]
fn deterministic() {
    for seq in 0..20u64 {
        let mut rng = Rng(SEED ^ seq);
        let (mut a, mut b) = (rated(), rated());
        let mut rate_refusals = 0;
        for i in 0..200 {
            let mut r = random_request(&mut rng);
            r.ts = UnixNanos::new(i * 100_000);
            r.trading_state = TradingState::Active;
            r.instrument_id = x();
            r.quantity = 25.0;
            r.price = Some(100.0);
            r.trigger_price = None;
            let (da, db) = (a.check(&r), b.check(&r));
            assert_eq!(da, db, "sequence {seq} step {i}");
            if matches!(da, RiskDecision::Refused(RiskRefusal::OrderRate { .. })) {
                rate_refusals += 1;
            }
        }
        assert!(rate_refusals > 0, "sequence {seq} never hit the rate rule");
    }
}

#[test]
fn idempotent_without_rate() {
    let mut rng = Rng(SEED);
    let mut s = stage_with(RiskLimits::default());
    for i in 0..2_000 {
        let r = random_request(&mut rng);
        let first = s.check(&r);
        assert_eq!(first, s.check(&r), "case {i}: {r:?}");
    }
}
