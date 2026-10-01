//! Unit tests for `crate::atr`.

use super::{assert_close, hlc};
use crate::{Atr, Indicator};

#[test]
#[should_panic(expected = "ATR period must be positive")]
fn zero_period_panics() {
    let _ = Atr::new(0);
}

#[test]
fn seeds_with_mean_true_range_then_uses_wilder_smoothing() {
    let mut a = Atr::new(2);
    assert_eq!(a.period(), 2);
    // First bar: high - low = 2.
    assert_eq!(a.update(&hlc(10.0, 8.0, 9.0)), None);
    // TR = max(3, |12 - 9|, |9 - 9|) = 3; seed = (2 + 3) / 2.
    assert_close(a.update(&hlc(12.0, 9.0, 11.0)).unwrap(), 2.5);
    // TR = max(1, |11 - 11|, |10 - 11|) = 1; (2.5 * 1 + 1) / 2.
    assert_close(a.update(&hlc(11.0, 10.0, 10.5)).unwrap(), 1.75);
}

#[test]
fn reset_clears_the_previous_close() {
    let mut a = Atr::new(1);
    a.update(&hlc(10.0, 9.0, 9.5));
    a.reset();
    assert_eq!(a.value(), None);
    // Without a previous close the gap to 9.5 is ignored: TR = 1.
    assert_close(a.update(&hlc(20.0, 19.0, 19.5)).unwrap(), 1.0);
}
