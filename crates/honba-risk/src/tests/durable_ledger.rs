//! Unit tests for FillFingerprint, FillLedger, and DurableRiskState (E2-S11).

use honba_entities::{Currency, Trade};
use honba_messages::{Exchange, InstrumentId, OrderId, OrderSide, UnixNanos, VenueOrderId};

use crate::durable::{DurableRiskState, InFlightOrder};
use crate::ledger::{FillFingerprint, FillLedger};

fn sample_trade(order: &str, qty: f64, px: f64, ts: u64) -> Trade {
    Trade::new(
        OrderId::new(order),
        InstrumentId::new("RELIANCE", Exchange::new("NSE")),
        OrderSide::Buy,
        qty,
        px,
        Currency::Inr,
        UnixNanos::from_u64(ts),
        UnixNanos::from_u64(ts),
    )
}

#[test]
fn fingerprint_dedup() {
    let t1 = sample_trade("O-1", 10.0, 2500.0, 1000);
    let t1_dup = sample_trade("O-1", 10.0, 2500.0, 1000);
    let t2 = sample_trade("O-1", 10.0, 2500.0, 2000); // different timestamp
    let t3 = sample_trade("O-2", 10.0, 2500.0, 1000); // different order id

    let fp1 = FillFingerprint::from_trade(&t1);
    let fp1_dup = FillFingerprint::from_trade(&t1_dup);
    let fp2 = FillFingerprint::from_trade(&t2);
    let fp3 = FillFingerprint::from_trade(&t3);

    // Identical content -> identical fingerprint
    assert_eq!(fp1, fp1_dup);
    assert_eq!(fp1.as_str(), fp1_dup.as_str());

    // Different fields -> distinct fingerprints
    assert_ne!(fp1, fp2);
    assert_ne!(fp1, fp3);

    let mut ledger = FillLedger::new();
    assert!(ledger.is_empty());

    // First time recorded: accepted
    assert!(ledger.record(&t1));
    assert_eq!(ledger.len(), 1);
    assert!(ledger.contains(&fp1));
    assert!(ledger.is_duplicate(&fp1));

    // Duplicate trade: refused by ledger (returns false, does not grow)
    assert!(!ledger.record(&t1_dup));
    assert_eq!(ledger.len(), 1);

    // Another distinct trade: accepted
    assert!(ledger.record(&t2));
    assert_eq!(ledger.len(), 2);
}

#[test]
fn atomic_snapshot() {
    let mut state = DurableRiskState::new();
    let inst = InstrumentId::new("INFY", Exchange::new("NSE"));

    // Add an in-flight order
    let order = InFlightOrder::new(
        OrderId::new("O-100"),
        inst.clone(),
        OrderSide::Buy,
        50.0,
        Some(1500.0),
        Some(VenueOrderId::new("V-100")),
        UnixNanos::from_u64(500),
    );
    state.add_in_flight(order.clone());
    assert!(state.is_in_flight("O-100"));

    // Record a fill
    let trade = sample_trade("O-99", 25.0, 2400.0, 800);
    assert!(state.record_fill(&trade));
    // Duplicate fill rejected
    assert!(!state.record_fill(&trade));

    let rel = InstrumentId::new("RELIANCE", Exchange::new("NSE"));
    assert_eq!(state.position(&rel), 25.0);

    // Write atomic snapshot to a temporary directory
    let temp_dir = std::env::temp_dir().join(format!("honba_test_{}", std::process::id()));
    let snapshot_path = temp_dir.join("risk_state.json");

    state.atomic_snapshot(&snapshot_path).unwrap();
    assert!(snapshot_path.exists());

    // Restore from snapshot
    let restored = DurableRiskState::load_snapshot(&snapshot_path).unwrap();
    assert_eq!(restored.position(&rel), 25.0);
    assert!(restored.is_in_flight("O-100"));
    assert_eq!(restored.in_flight("O-100"), Some(&order));
    assert_eq!(restored.fill_ledger.len(), 1);

    // Restored ledger still rejects the re-delivered fill!
    let mut restored_mut = restored;
    assert!(!restored_mut.record_fill(&trade));
    assert_eq!(restored_mut.position(&rel), 25.0); // Not doubled!

    // Clean up
    let _ = std::fs::remove_dir_all(&temp_dir);
}
