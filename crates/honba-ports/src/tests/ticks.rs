//! Unit tests for the historical-tick port: request invariants and object safety.

use std::sync::Arc;

use async_trait::async_trait;
use honba_messages::{Exchange, InstrumentId, QuoteTick, TradeTick, UnixNanos};

use crate::{PortError, PortResult, TickReader, TickRequest};

fn id() -> InstrumentId {
    InstrumentId::new("TCS", Exchange::new("NSE"))
}

fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

struct Empty;

#[async_trait]
impl TickReader for Empty {
    async fn read_quotes(&self, _request: &TickRequest) -> PortResult<Vec<QuoteTick>> {
        Ok(Vec::new())
    }
    async fn read_trades(&self, _request: &TickRequest) -> PortResult<Vec<TradeTick>> {
        Ok(Vec::new())
    }
}

#[test]
fn the_tick_reader_is_object_safe_and_shareable() {
    fn assert_send_sync<T: Send + Sync + ?Sized>() {}
    assert_send_sync::<dyn TickReader>();
    let _shared: Arc<dyn TickReader> = Arc::new(Empty);
}

#[test]
fn a_request_exposes_its_parts() {
    let r = TickRequest::new(id(), Some(ts(5)), Some(ts(9))).unwrap();
    assert_eq!(r.instrument(), &id());
    assert_eq!(r.from(), Some(ts(5)));
    assert_eq!(r.to(), Some(ts(9)));
    let open = TickRequest::new(id(), None, None).unwrap();
    assert_eq!((open.from(), open.to()), (None, None));
}

#[test]
fn an_empty_or_inverted_range_is_invalid() {
    for (a, b) in [(5, 5), (9, 5)] {
        let err = TickRequest::new(id(), Some(ts(a)), Some(ts(b))).unwrap_err();
        assert!(matches!(err, PortError::InvalidRequest(_)), "{a}..{b}");
    }
}
