//! Unit tests for `endpoints`.

use crate::endpoints::*;

#[test]
fn write_endpoints_are_subset() {
    let writes = write_endpoints();
    for w in writes {
        assert!(ENDPOINTS.iter().any(|(m, p)| format!("{} {}", m, p) == w));
    }
}

#[test]
fn no_duplicate_endpoint_keys() {
    let mut seen = std::collections::BTreeSet::new();
    for (m, p) in ENDPOINTS {
        assert!(
            seen.insert(format!("{} {}", m, p)),
            "duplicate: {} {}",
            m,
            p
        );
    }
}
