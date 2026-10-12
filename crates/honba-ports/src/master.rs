//! The instrument-reference-data port.

use async_trait::async_trait;
use chrono::NaiveDate;
use honba_entities::Instrument;
use honba_messages::InstrumentId;

use crate::error::PortResult;

/// An instrument as it stood on a particular date.
///
/// Reference data is not timeless: a symbol's lot size changes, a contract
/// expires, and a backtest must resolve the values that were in force on the
/// bar's date, not today's. A [`DatedInstrument`] pairs an [`Instrument`] with
/// the half-open validity window `[effective_from, effective_to)` during which
/// that version applied.
///
/// This is a plain data carrier: no serialization, no schema. The wire format
/// of a dated snapshot lives with the implementation that reads it (for example
/// the Parquet catalog in `honba-feed`), so the port stays free of one vendor's
/// file layout.
#[derive(Clone, Debug, PartialEq)]
pub struct DatedInstrument {
    /// The instrument metadata in force over the window.
    pub instrument: Instrument,
    /// The order quantity above which the exchange freezes further buying.
    pub freeze_qty: f64,
    /// The first date (inclusive) on which this version was in force.
    pub effective_from: NaiveDate,
    /// The date (exclusive) this version stopped applying, or `None` while it is
    /// still the current one.
    pub effective_to: Option<NaiveDate>,
}

/// Static reference data: what an instrument is, and the lot size and tick size that size an
/// order.
///
/// The lookup itself is pure and cheap, so this port only exists where the data comes from a
/// network or a cache: a vendor's symbol master, an exchange's daily `.csv`, or the Parquet
/// catalogue in `honba-feed`. Implementations never mutate, so the port is `Send + Sync` and one
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

    /// Returns the dated snapshot in force on `as_of`.
    ///
    /// Each entry carries the version of the instrument that applied on `as_of`
    /// and the window it was valid over. The default implementation is timeless:
    /// it wraps [`InstrumentMaster::list_instruments`] with an unbounded window
    /// and a unit freeze quantity, so a master that carries no dates still
    /// answers.
    async fn snapshot(&self, _as_of: NaiveDate) -> PortResult<Vec<DatedInstrument>> {
        let instruments = self.list_instruments().await?;
        Ok(instruments
            .into_iter()
            .map(|instrument| DatedInstrument {
                instrument,
                freeze_qty: 1.0,
                effective_from: NaiveDate::MIN,
                effective_to: None,
            })
            .collect())
    }

    /// Resolves `id` as it stood on `as_of`.
    ///
    /// The default implementation ignores the date and delegates to
    /// [`InstrumentMaster::get_instrument`], so a timeless master keeps its
    /// current behaviour.
    async fn resolve_as_of(
        &self,
        id: &InstrumentId,
        _as_of: NaiveDate,
    ) -> PortResult<Option<Instrument>> {
        self.get_instrument(id).await
    }
}
