//! Rule 10: the order-rate window (ADR 0018 decision 4), in event time.

use honba_messages::{OrderSide, UnixNanos};

use super::{refusal, req, stage_with};
use crate::{OrderRateLimit, RiskCheck, RiskDecision, RiskLimits, RiskRefusal, RiskRequest};

const MS: u64 = 1_000_000;

fn limited(max_orders: u32, window_ms: u64) -> crate::RiskStage {
    stage_with(RiskLimits {
        max_notional: None,
        order_rate: Some(OrderRateLimit {
            max_orders,
            window_ms,
        }),
        max_participation: None,
    })
}

fn at(ts: u64) -> RiskRequest {
    RiskRequest {
        ts: UnixNanos::new(ts),
        ..req()
    }
}

fn rate_refusal(count: u32, max_orders: u32, window_ms: u64) -> RiskRefusal {
    RiskRefusal::OrderRate {
        count,
        max_orders,
        window_ms,
    }
}

#[test]
fn order_rate_refused_in_event_time() {
    let mut s = limited(2, 1000);
    assert_eq!(s.check(&at(1)), RiskDecision::Approved);
    assert_eq!(s.check(&at(2)), RiskDecision::Approved);
    let got = refusal(s.check(&at(3)));
    assert_eq!(got, rate_refusal(2, 2, 1000));
    assert_eq!(got.context()["rule"], "order_rate");
    assert_eq!(got.error_code().as_str(), "risk_order_rate_exceeded");
    // The event-time clock moves past the first order's window edge: slot freed.
    assert_eq!(s.check(&at(1 + 1000 * MS)), RiskDecision::Approved);
}

#[test]
fn refused_orders_do_not_count() {
    // Refused by a shape rule (lot multiple): never consumes a slot.
    let mut s = limited(1, 1000);
    for ts in 1..=5 {
        let bad = RiskRequest {
            quantity: 30.0,
            ..at(ts)
        };
        assert!(matches!(
            refusal(s.check(&bad)),
            RiskRefusal::LotMultiple { .. }
        ));
    }
    assert_eq!(s.check(&at(6)), RiskDecision::Approved);

    // Refused by the rate rule itself: does not extend the window either.
    let mut s = limited(1, 1);
    let t0 = 10 * MS;
    assert_eq!(s.check(&at(t0)), RiskDecision::Approved);
    assert_eq!(
        s.check(&at(t0 + MS / 2)),
        RiskDecision::Refused(rate_refusal(1, 1, 1))
    );
    assert_eq!(s.check(&at(t0 + MS)), RiskDecision::Approved);

    // Refused by state rules (halted) before any later rule: no slot either.
    let mut s = limited(1, 1000);
    let halted = RiskRequest {
        trading_state: honba_messages::TradingState::Halted,
        ..at(1)
    };
    assert_eq!(refusal(s.check(&halted)), RiskRefusal::TradingHalted);
    assert_eq!(s.check(&at(2)), RiskDecision::Approved);
}

#[test]
fn rate_window_boundary_half_open() {
    let w = 1000 * MS;
    let t = 5 * w;
    let mut s = limited(1, 1000);
    assert_eq!(s.check(&at(t)), RiskDecision::Approved);
    // One nanosecond short of the window: the earlier order is still inside (ts-W, ts].
    assert_eq!(
        s.check(&at(t + w - 1)),
        RiskDecision::Refused(rate_refusal(1, 1, 1000))
    );
    // Exactly window_ns later the earlier order is no longer seen.
    assert_eq!(s.check(&at(t + w)), RiskDecision::Approved);
}

#[test]
fn same_timestamp_orders_share_the_window() {
    let mut s = limited(2, 1000);
    assert_eq!(s.check(&at(7)), RiskDecision::Approved);
    assert_eq!(s.check(&at(7)), RiskDecision::Approved);
    assert_eq!(refusal(s.check(&at(7))), rate_refusal(2, 2, 1000));
}

#[test]
fn earlier_timestamp_is_treated_as_the_last_recorded() {
    let w = 1000 * MS;
    let mut s = limited(1, 1000);
    assert_eq!(s.check(&at(10 * w)), RiskDecision::Approved);
    // Going back in time cannot shrink the window or free the slot.
    assert_eq!(refusal(s.check(&at(1))), rate_refusal(1, 1, 1000));
    // The effective clock stays at 10*w, so the window edge is still 11*w.
    assert_eq!(refusal(s.check(&at(11 * w - 1))), rate_refusal(1, 1, 1000));
    assert_eq!(s.check(&at(11 * w)), RiskDecision::Approved);
}

#[test]
fn window_arithmetic_does_not_underflow_near_zero() {
    let mut s = limited(1, u64::MAX / MS);
    assert_eq!(s.check(&at(0)), RiskDecision::Approved);
    assert!(matches!(
        refusal(s.check(&at(1))),
        RiskRefusal::OrderRate { .. }
    ));
}

#[test]
fn without_a_rate_limit_nothing_is_counted() {
    let mut s = stage_with(RiskLimits::default());
    for ts in 0..50 {
        assert_eq!(s.check(&at(ts)), RiskDecision::Approved);
    }
}

#[test]
fn with_a_rate_limit_check_is_not_idempotent_by_design() {
    let mut s = limited(1, 1000);
    let r = RiskRequest {
        side: OrderSide::Buy,
        ..at(1)
    };
    assert_eq!(s.check(&r), RiskDecision::Approved);
    // The approval consumed the only slot, so the identical request now refuses.
    assert_eq!(refusal(s.check(&r)), rate_refusal(1, 1, 1000));
}
