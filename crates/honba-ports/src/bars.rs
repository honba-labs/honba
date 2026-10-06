//! The historical-bar read port.

use async_trait::async_trait;
use honba_messages::{Bar, BarSpecification, InstrumentId, UnixNanos};

use crate::error::{PortError, PortResult};

/// A request for the stored bars of one instrument at one bar specification.
///
/// The range is half-open, `[from, to)` on `ts_event`, with either end optional. A range whose
/// end is not after its start is rejected at construction, so every implementation can assume
/// `from < to`.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct BarRequest {
    instrument: InstrumentId,
    spec: BarSpecification,
    from: Option<UnixNanos>,
    to: Option<UnixNanos>,
}

impl BarRequest {
    /// Creates a request, rejecting an empty or inverted range with
    /// [`PortError::InvalidRequest`].
    pub fn new(
        instrument: InstrumentId,
        spec: BarSpecification,
        from: Option<UnixNanos>,
        to: Option<UnixNanos>,
    ) -> PortResult<Self> {
        if let (Some(from), Some(to)) = (from, to) {
            if from >= to {
                return Err(PortError::InvalidRequest(format!(
                    "bar range is empty: from {from} is not before to {to}"
                )));
            }
        }
        Ok(Self {
            instrument,
            spec,
            from,
            to,
        })
    }

    /// Returns the instrument whose bars are requested.
    pub fn instrument(&self) -> &InstrumentId {
        &self.instrument
    }

    /// Returns the bar specification (timeframe) requested.
    pub fn spec(&self) -> BarSpecification {
        self.spec
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

/// Read-only access to stored historical bars.
///
/// This is the query side of market data: the REST and MCP surfaces read through it, and the
/// implementation (a Parquet catalogue in `honba-data`, a vendor history API, a cache) stays at
/// the edge. It only reads, so the port is `Send + Sync` and one instance is shared behind an
/// `Arc`.
#[async_trait]
pub trait BarReader: Send + Sync {
    /// Returns the bars matching `request`, in ascending `ts_event` order.
    ///
    /// An instrument or range with no bars is `Ok` with an empty vector. A bar specification the
    /// reader does not hold for that instrument is [`PortError::Unsupported`], not an empty
    /// answer, so a caller can tell "no data in range" from "no such timeframe".
    async fn read_bars(&self, request: &BarRequest) -> PortResult<Vec<Bar>>;
}
