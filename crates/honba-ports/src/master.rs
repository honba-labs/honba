//! The instrument-reference-data port.

use async_trait::async_trait;
use honba_entities::Instrument;
use honba_messages::InstrumentId;

use crate::error::PortResult;

/// Static reference data: what an instrument is, and the lot size and tick size that size an
/// order.
///
/// The lookup itself is pure and cheap, so this port only exists where the data comes from a
/// network or a cache: a vendor's symbol master, an exchange's daily `.csv`, or the Parquet
/// catalogue in `honba-data`. Implementations never mutate, so the port is `Send + Sync` and one
/// instance can be shared by every task.
#[async_trait]
pub trait InstrumentMaster: Send + Sync {
    /// Returns the instrument with this id, or `Ok(None)` if it is unknown.
    ///
    /// `Ok(None)` means "no such instrument". A master that cannot answer at all returns an
    /// error.
    async fn get_instrument(&self, id: &InstrumentId) -> PortResult<Option<Instrument>>;

    /// Returns every instrument the master knows about.
    ///
    /// Implementations may page or stream internally; the contract is the complete set, in a
    /// stable order across calls.
    async fn list_instruments(&self) -> PortResult<Vec<Instrument>>;
}
