//! Unit tests for `crate::TradingState` (moved from `honba-engine`, ADR 0018 decision 2).

use std::collections::HashSet;

use serde_json::json;

use crate::TradingState;

const ALL: [TradingState; 3] = [
    TradingState::Active,
    TradingState::Reducing,
    TradingState::Halted,
];

#[test]
fn default_is_active() {
    assert_eq!(TradingState::default(), TradingState::Active);
}

#[test]
fn active_and_reducing_accept_orders_but_halted_does_not() {
    assert!(TradingState::Active.accepts_orders());
    assert!(TradingState::Reducing.accepts_orders());
    assert!(!TradingState::Halted.accepts_orders());
}

#[test]
fn every_state_may_reach_every_other_state() {
    for from in ALL {
        for to in ALL {
            assert!(
                from.can_transition_to(to),
                "{from:?} may not transition to {to:?}"
            );
        }
    }
}

#[test]
fn states_are_copy_and_hashable() {
    let mut seen = HashSet::new();
    for state in ALL {
        let copy = state;
        assert_eq!(copy, state);
        assert!(seen.insert(copy));
    }
    assert_eq!(seen.len(), ALL.len());
}

#[test]
fn trading_state_serde_snake_case() {
    for (state, wire) in [
        (TradingState::Active, "active"),
        (TradingState::Reducing, "reducing"),
        (TradingState::Halted, "halted"),
    ] {
        assert_eq!(serde_json::to_value(state).unwrap(), json!(wire));
        assert_eq!(
            serde_json::from_value::<TradingState>(json!(wire)).unwrap(),
            state
        );
    }
    assert!(serde_json::from_value::<TradingState>(json!("Reducing")).is_err());
}
