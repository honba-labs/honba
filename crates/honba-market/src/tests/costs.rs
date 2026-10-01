//! Unit tests for `crate::costs`.

use honba_messages::OrderSide;

use crate::{Charge, CostSchedule, FeeBreakdown, MarketSegment, NullCostSchedule};

#[test]
fn fee_breakdown_totals_and_looks_up_charges_by_name() {
    let mut fees = FeeBreakdown::empty();
    assert_eq!(fees.total(), 0.0);
    fees.add("brokerage", 20.0);
    fees.add("gst", 3.6);
    assert!((fees.total() - 23.6).abs() < 1e-12);
    assert_eq!(fees.get("gst"), Some(3.6));
    assert_eq!(fees.get("stt"), None);
    assert_eq!(
        FeeBreakdown::from_charges(vec![Charge::new("a", 1.0)]),
        FeeBreakdown {
            charges: vec![Charge::new("a", 1.0)]
        }
    );
}

#[test]
fn charge_displays_with_four_decimals() {
    assert_eq!(Charge::new("stt", 1.5).to_string(), "stt: 1.5000");
}

#[test]
fn market_segment_round_trips_through_strings() {
    let a = MarketSegment::from("EQ");
    let b = MarketSegment::from(String::from("EQ"));
    assert_eq!(a, b);
    assert_eq!(a.as_str(), "EQ");
    assert_eq!(a.to_string(), "EQ");
}

#[test]
fn null_cost_schedule_charges_nothing() {
    let fees = NullCostSchedule.compute_costs(&"EQ".into(), OrderSide::Buy, 1_000_000.0);
    assert_eq!(fees.total(), 0.0);
}
