//! Unit tests for the historical-bar port: request invariants and object safety.

use std::sync::Arc;

use async_trait::async_trait;
use honba_messages::{
    Bar, BarAggregation, BarSpecification, Exchange, InstrumentId, PriceType, UnixNanos,
};

use crate::{BarReader, BarRequest, PortError, PortResult};

fn id() -> InstrumentId {
    InstrumentId::new("TCS", Exchange::new("NSE"))
}

fn spec() -> BarSpecification {
    BarSpecification::new(1, BarAggregation::Minute, PriceType::Last)
}

fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

struct Empty;

#[async_trait]
impl BarReader for Empty {
    async fn read_bars(&self, _request: &BarRequest) -> PortResult<Vec<Bar>> {
        Ok(Vec::new())
    }
}

#[test]
fn the_bar_reader_is_object_safe_and_shareable() {
    fn assert_send_sync<T: Send + Sync + ?Sized>() {}
    assert_send_sync::<dyn BarReader>();
    let _shared: Arc<dyn BarReader> = Arc::new(Empty);
}

#[test]
fn an_unbounded_request_exposes_its_parts() {
    let request = BarRequest::new(id(), spec(), None, None).unwrap();
    assert_eq!(request.instrument(), &id());
    assert_eq!(request.spec(), spec());
    assert_eq!(request.from(), None);
    assert_eq!(request.to(), None);
}

#[test]
fn a_bounded_request_keeps_inclusive_from_and_exclusive_to() {
    let request = BarRequest::new(id(), spec(), Some(ts(10)), Some(ts(20))).unwrap();
    assert_eq!(request.from(), Some(ts(10)));
    assert_eq!(request.to(), Some(ts(20)));
}

#[test]
fn an_empty_or_inverted_range_is_rejected() {
    for (from, to) in [(20, 20), (30, 20)] {
        let err = BarRequest::new(id(), spec(), Some(ts(from)), Some(ts(to))).unwrap_err();
        assert!(matches!(err, PortError::InvalidRequest(_)), "{err:?}");
    }
}

#[test]
fn a_half_open_range_is_always_valid() {
    assert!(BarRequest::new(id(), spec(), Some(ts(u64::MAX)), None).is_ok());
    assert!(BarRequest::new(id(), spec(), None, Some(ts(0))).is_ok());
}

#[tokio::test]
async fn the_trait_object_is_callable() {
    let reader: Arc<dyn BarReader> = Arc::new(Empty);
    let request = BarRequest::new(id(), spec(), None, None).unwrap();
    assert!(reader.read_bars(&request).await.unwrap().is_empty());
}
