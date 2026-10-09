//! Unit tests for feed staleness breaker (E2-S10).

use honba_messages::{ErrorCode, UnixNanos};

use super::{refusal, req, stage_with};
use crate::{RiskCheck, RiskDecision, RiskLimits, RiskRefusal};

#[test]
fn stale_after_blocks_orders() {
    let limits = RiskLimits {
        stale_after_ms: Some(500),
        ..Default::default()
    };
    let mut stage = stage_with(limits);

    // 1. Missing feed data refuses closed
    let missing_req = req();
    let d = stage.check(&missing_req);
    match refusal(d) {
        RiskRefusal::FeedStale {
            instrument_id,
            age_ns,
            stale_after_ns,
        } => {
            assert_eq!(instrument_id, missing_req.instrument_id);
            assert_eq!(age_ns, u64::MAX);
            assert_eq!(stale_after_ns, 500_000_000);
        }
        other => panic!("expected FeedStale, got {other:?}"),
    }

    // 2. Feed older than 500ms refuses
    let mut stale_req = req();
    stale_req.ts = UnixNanos::new(1_000_000_000); // 1.0s
    stale_req.last_feed_ts = Some(UnixNanos::new(400_000_000)); // 0.4s -> age = 600ms > 500ms
    let d = stage.check(&stale_req);
    match refusal(d) {
        RiskRefusal::FeedStale { age_ns, .. } => {
            assert_eq!(age_ns, 600_000_000);
        }
        other => panic!("expected FeedStale, got {other:?}"),
    }

    // Error code wire mapping
    let refusal = refusal(stage.check(&stale_req));
    assert_eq!(refusal.error_code(), ErrorCode::RiskFeedStale);
    assert_eq!(refusal.rule(), "feed_staleness");

    // 3. Fresh feed within 500ms passes
    let mut fresh_req = req();
    fresh_req.ts = UnixNanos::new(1_000_000_000); // 1.0s
    fresh_req.last_feed_ts = Some(UnixNanos::new(800_000_000)); // 0.8s -> age = 200ms <= 500ms
    let d = stage.check(&fresh_req);
    assert_eq!(d, RiskDecision::Approved);
}

#[test]
fn disabled_stale_after_allows_orders_without_feed() {
    let limits = RiskLimits::default();
    let mut stage = stage_with(limits);
    let mut r = req();
    r.last_feed_ts = None;
    assert_eq!(stage.check(&r), RiskDecision::Approved);
}
