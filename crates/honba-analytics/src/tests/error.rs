//! Unit tests for `crate::error`.

use crate::AnalyticsError;

#[test]
fn errors_render_human_readable_messages() {
    assert_eq!(AnalyticsError::EmptyInput.to_string(), "empty input");
    assert_eq!(
        AnalyticsError::InsufficientData { needed: 2, got: 1 }.to_string(),
        "insufficient data: needed 2, got 1"
    );
    assert_eq!(
        AnalyticsError::ZeroVariance.to_string(),
        "zero variance in input"
    );
    assert_eq!(
        AnalyticsError::TradeMismatch("x".into()).to_string(),
        "trade mismatch: x"
    );
}
