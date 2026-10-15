//! The historical quote/trade tick read port.

use async_trait::async_trait;
use honba_messages::{InstrumentId, QuoteTick, TradeTick, UnixNanos};

use crate::error::{PortError, PortResult};

/// A request for the stored ticks of one instrument over a range.
///
/// The range is half-open, `[from, to)` on `ts_event`, with either end optional. A range whose
/// end is not after its start is rejected at construction, so every implementation can assume
/// `from < to`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct TickRequest {
    instrument: InstrumentId,
    from: Option<UnixNanos>,
    to: Option<UnixNanos>,
}

impl TickRequest {
    /// Creates a request, rejecting an empty or inverted range with
    /// [`PortError::InvalidRequest`].
    pub fn new(
        instrument: InstrumentId,
        from: Option<UnixNanos>,
        to: Option<UnixNanos>,
    ) -> PortResult<Self> {
        if let (Some(from), Some(to)) = (from, to) {
            if from >= to {
                return Err(PortError::InvalidRequest(format!(
                    "tick range is empty: from {from} is not before to {to}"
                )));
            }
        }
        Ok(Self {
            instrument,
            from,
            to,
        })
    }

    /// Returns the instrument whose ticks are requested.
    pub fn instrument(&self) -> &InstrumentId {
        &self.instrument
    }

    /// Returns the inclusive start, if bounded.
    pub fn from(&self) -> Option<UnixNanos> {
        self.from
    }

    /// Returns the exclusive end, if bounded.
    pub fn to(&self) -> Option<UnixNanos> {
        self.to
    }
}

/// Read-only access to stored historical quote and trade ticks (ADR 0030).
///
/// The tick side of the replay input an L2 backtest needs: `QuoteTick` is the book, `TradeTick`
/// the prints. Like [`BarReader`](crate::BarReader) it only reads, so it is `Send + Sync` and
/// shared behind an `Arc`.
#[async_trait]
pub trait TickReader: Send + Sync {
    /// Returns the quotes matching `request`, in ascending `ts_event` order (stable for ties).
    ///
    /// An instrument or range with no quotes is `Ok` with an empty vector. A reader that holds
    /// no tick data at all returns [`PortError::Unsupported`], never an invented answer.
    async fn read_quotes(&self, request: &TickRequest) -> PortResult<Vec<QuoteTick>>;

    /// Returns the trades matching `request`, in ascending `ts_event` order (stable for ties),
    /// with the same empty and unsupported semantics as [`Self::read_quotes`].
    async fn read_trades(&self, request: &TickRequest) -> PortResult<Vec<TradeTick>>;
}
