//! Rules 1-2: trading state and reduce-only.

use honba_messages::{OrderSide, TradingState};

use super::{refusal, req, stage, x};
use crate::{check_state, RiskCheck, RiskDecision, RiskRefusal, RiskRequest};

fn reducing(position: f64, side: OrderSide, quantity: f64) -> RiskRequest {
    RiskRequest {
        trading_state: TradingState::Reducing,
        position,
        side,
        quantity,
        ..req()
    }
}

#[test]
fn trading_halted_refused() {
    let mut r = req();
    r.trading_state = TradingState::Halted;
    assert_eq!(refusal(stage().check(&r)), RiskRefusal::TradingHalted);
}

#[test]
fn halted_beats_every_later_rule() {
    let mut r = req();
    r.trading_state = TradingState::Halted;
    r.instrument_id =
        honba_messages::InstrumentId::new("NOPE", honba_messages::Exchange::new("NSE"));
    r.quantity = 1.0;
    r.price = Some(-1.0);
    assert_eq!(refusal(stage().check(&r)), RiskRefusal::TradingHalted);
}

#[test]
fn active_state_passes_the_state_rules() {
    assert_eq!(check_state(&req()), None);
    assert_eq!(stage().check(&req()), RiskDecision::Approved);
}

#[test]
fn reduce_only_refuses_position_increasing_order() {
    // Long 100: a buy adds exposure.
    assert_eq!(
        refusal(stage().check(&reducing(100.0, OrderSide::Buy, 25.0))),
        RiskRefusal::ReduceOnly {
            position: 100.0,
            side: OrderSide::Buy,
            quantity: 25.0
        }
    );
    // Short 100: a sell adds exposure.
    assert_eq!(
        refusal(stage().check(&reducing(-100.0, OrderSide::Sell, 25.0))),
        RiskRefusal::ReduceOnly {
            position: -100.0,
            side: OrderSide::Sell,
            quantity: 25.0
        }
    );
    // Crossing zero is refused too.
    assert!(matches!(
        refusal(stage().check(&reducing(25.0, OrderSide::Sell, 50.0))),
        RiskRefusal::ReduceOnly { .. }
    ));
}

#[test]
fn reduce_only_approves_reducing_and_closing_orders() {
    assert_eq!(
        stage().check(&reducing(100.0, OrderSide::Sell, 25.0)),
        RiskDecision::Approved
    );
    assert_eq!(
        stage().check(&reducing(100.0, OrderSide::Sell, 100.0)),
        RiskDecision::Approved
    );
    assert_eq!(
        stage().check(&reducing(-100.0, OrderSide::Buy, 100.0)),
        RiskDecision::Approved
    );
}

#[test]
fn reduce_only_flat_refuses_all() {
    for side in [OrderSide::Buy, OrderSide::Sell] {
        assert_eq!(
            refusal(stage().check(&reducing(0.0, side, 25.0))),
            RiskRefusal::ReduceOnly {
                position: 0.0,
                side,
                quantity: 25.0
            }
        );
    }
}

#[test]
fn reduce_only_two_orders_in_flight() {
    // Long 100 with a working sell of 60: the submitter passes p = 100 - 60 = 40.
    // A second sell of 50 would cross zero once both fill: refused.
    assert_eq!(
        refusal(stage().check(&reducing(40.0, OrderSide::Sell, 50.0))),
        RiskRefusal::ReduceOnly {
            position: 40.0,
            side: OrderSide::Sell,
            quantity: 50.0
        }
    );
    // A sell of 25 (<= 40) still fits.
    assert_eq!(
        stage().check(&reducing(40.0, OrderSide::Sell, 25.0)),
        RiskDecision::Approved
    );
}

#[test]
fn state_rules_precede_instrument_lookup() {
    let mut r = reducing(100.0, OrderSide::Buy, 25.0);
    r.instrument_id =
        honba_messages::InstrumentId::new("NOPE", honba_messages::Exchange::new("NSE"));
    assert!(matches!(
        refusal(stage().check(&r)),
        RiskRefusal::ReduceOnly { .. }
    ));
    assert_ne!(r.instrument_id, x());
}
