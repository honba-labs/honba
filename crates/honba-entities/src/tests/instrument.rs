//! Unit tests for `crate::instrument`.

use crate::Currency;

#[test]
fn currency_serializes_as_iso_code() {
    for c in [Currency::Inr, Currency::Usd, Currency::Eur, Currency::Gbp] {
        let json = serde_json::to_value(c).unwrap();
        assert_eq!(json, c.code());
        assert_eq!(serde_json::from_value::<Currency>(json).unwrap(), c);
    }
}
