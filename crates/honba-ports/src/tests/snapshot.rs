//! Unit tests for the quote and depth ports: object safety and the shared-handle bound.

use std::sync::Arc;

use async_trait::async_trait;
use honba_messages::{Exchange, InstrumentId, QuoteTick, UnixNanos};

use crate::{DepthLevel, DepthReader, DepthSnapshot, PortError, PortResult, QuoteReader};

fn id() -> InstrumentId {
    InstrumentId::new("TCS", Exchange::new("NSE"))
}

struct Fixed;

#[async_trait]
impl QuoteReader for Fixed {
    async fn read_quote(
        &self,
        id: &InstrumentId,
        as_of: Option<UnixNanos>,
    ) -> PortResult<Option<QuoteTick>> {
        let ts = as_of.unwrap_or_else(|| UnixNanos::from_u64(1));
        Ok(Some(QuoteTick::new(
            id.clone(),
            9.0,
            10.0,
            1.0,
            2.0,
            ts,
            ts,
        )))
    }
}

#[async_trait]
impl DepthReader for Fixed {
    async fn read_depth(&self, _id: &InstrumentId, _levels: usize) -> PortResult<DepthSnapshot> {
        Err(PortError::Unsupported("no book".into()))
    }
}

#[test]
fn both_ports_are_object_safe_and_shareable() {
    fn assert_send_sync<T: Send + Sync + ?Sized>() {}
    assert_send_sync::<dyn QuoteReader>();
    assert_send_sync::<dyn DepthReader>();
    let _quotes: Arc<dyn QuoteReader> = Arc::new(Fixed);
    let _depth: Arc<dyn DepthReader> = Arc::new(Fixed);
}

#[tokio::test]
async fn the_trait_objects_are_callable() {
    let quotes: Arc<dyn QuoteReader> = Arc::new(Fixed);
    let quote = quotes.read_quote(&id(), None).await.unwrap().unwrap();
    assert_eq!(quote.bid_price(), 9.0);
    let depth: Arc<dyn DepthReader> = Arc::new(Fixed);
    assert!(matches!(
        depth.read_depth(&id(), 5).await,
        Err(PortError::Unsupported(_))
    ));
}

#[test]
fn an_empty_book_is_the_default_snapshot() {
    let book = DepthSnapshot::default();
    assert!(book.bids.is_empty() && book.asks.is_empty());
    let level = DepthLevel {
        price: 1.0,
        qty: 2.0,
    };
    assert_eq!(level, level);
}
