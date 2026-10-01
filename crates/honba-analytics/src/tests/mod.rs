//! Unit tests for this crate, one file per area.

mod equity_stats;
mod error;
mod report;
mod round_trip;
mod trade_stats;

use honba_entities::PositionSide;
use honba_messages::{InstrumentId, UnixNanos, Venue};

use crate::RoundTrip;

/// A placeholder instrument for tests where the instrument does not matter.
fn any_instrument() -> InstrumentId {
    InstrumentId::new("X", Venue::new("NSE"))
}

/// A long round trip of `qty` from `entry` to `exit` with `fees`, held 1..2.
fn long_trip(entry: f64, exit: f64, qty: f64, fees: f64) -> RoundTrip {
    RoundTrip::new(
        any_instrument(),
        PositionSide::Long,
        qty,
        entry,
        exit,
        UnixNanos::from_u64(1),
        UnixNanos::from_u64(2),
        fees,
    )
}

fn assert_close(a: f64, b: f64) {
    assert!((a - b).abs() < 1e-12, "{a} != {b}");
}
