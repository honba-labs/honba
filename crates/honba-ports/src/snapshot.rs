//! The point-in-time market-snapshot read ports: top-of-book quotes and order-book depth.

use async_trait::async_trait;
use honba_messages::{InstrumentId, QuoteTick, UnixNanos};

use crate::error::PortResult;

/// Read-only access to the latest top-of-book quote of an instrument.
///
/// The query side of quote data for the REST and MCP surfaces. It is separate from
/// [`BarReader`](crate::BarReader) because a store that holds only bars can still derive a
/// last-price quote, while a store of ticks answers with real bid and ask.
#[async_trait]
pub trait QuoteReader: Send + Sync {
    /// Returns the latest quote of `id` known at `as_of`, or the latest held when `as_of` is
    /// `None`.
    ///
    /// "Known at `as_of`" is inclusive: a quote stamped exactly `as_of` counts, a later one never
    /// does, so a replayed query cannot see the future. `Ok(None)` means the instrument has no
    /// quote at or before `as_of` (including an instrument the reader holds nothing for). A reader
    /// that cannot quote at all returns [`PortError::Unsupported`](crate::PortError::Unsupported).
    async fn read_quote(
        &self,
        id: &InstrumentId,
        as_of: Option<UnixNanos>,
    ) -> PortResult<Option<QuoteTick>>;
}

/// One price level of an order book.
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct DepthLevel {
    /// Price of the level.
    pub price: f64,
    /// Quantity resting at that price.
    pub qty: f64,
}

/// An order book cut to at most the requested number of levels per side.
#[derive(Clone, Debug, Default, PartialEq)]
pub struct DepthSnapshot {
    /// Bid levels, best (highest) first.
    pub bids: Vec<DepthLevel>,
    /// Ask levels, best (lowest) first.
    pub asks: Vec<DepthLevel>,
}

/// Read-only access to the current order book of an instrument.
#[async_trait]
pub trait DepthReader: Send + Sync {
    /// Returns at most `levels` levels per side of the book of `id`.
    ///
    /// A reader that holds no order-book data (a bar store, for one) returns
    /// [`PortError::Unsupported`](crate::PortError::Unsupported); it never invents levels.
    async fn read_depth(&self, id: &InstrumentId, levels: usize) -> PortResult<DepthSnapshot>;
}
