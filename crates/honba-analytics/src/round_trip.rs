//! A completed round-trip trade: entry and exit paired.

use honba_entities::{Money, PositionSide, Trade};
use honba_messages::{InstrumentId, OrderSide, UnixNanos};
use serde::{Deserialize, Serialize};

use crate::error::{AnalyticsError, Result};

/// A closed position: an entry fill matched with its exit.
///
/// Analytics operate on round trips, not raw fills. Pairing fills into round
/// trips is the caller's job (or a dedicated module), since the matching
/// rule varies — FIFO, LIFO, or average cost.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct RoundTrip {
    /// The instrument traded.
    pub instrument_id: InstrumentId,
    /// Direction of the position.
    pub side: PositionSide,
    /// Quantity entered.
    pub quantity: f64,
    /// Entry price.
    pub entry_price: f64,
    /// Exit price.
    pub exit_price: f64,
    /// Entry timestamp.
    pub entry_ts: UnixNanos,
    /// Exit timestamp.
    pub exit_ts: UnixNanos,
    /// Realized profit and loss, before fees.
    pub gross_pnl: f64,
    /// Fees paid, positive magnitude.
    pub fees: f64,
    /// Realized profit and loss, net of fees.
    pub net_pnl: f64,
    /// Net PnL as a fraction of entry notional.
    pub pnl_pct: f64,
}

impl RoundTrip {
    /// Pairs an entry fill and an exit fill into a round trip.
    ///
    /// The entry fill's side determines the direction. The exit fill must be
    /// the opposite side and reference the same instrument. Fees are read off
    /// the trades themselves — passing them separately let callers report fees
    /// the ledger never charged.
    pub fn from_fills(entry: &Trade, exit: &Trade) -> Result<Self> {
        if entry.instrument_id() != exit.instrument_id() {
            return Err(AnalyticsError::TradeMismatch(
                "entry and exit reference different instruments".into(),
            ));
        }

        let (side, quantity, entry_price, exit_price) = match (entry.side(), exit.side()) {
            (OrderSide::Buy, OrderSide::Sell) => (
                PositionSide::Long,
                entry.quantity(),
                entry.price(),
                exit.price(),
            ),
            (OrderSide::Sell, OrderSide::Buy) => (
                PositionSide::Short,
                entry.quantity(),
                entry.price(),
                exit.price(),
            ),
            _ => {
                return Err(AnalyticsError::TradeMismatch(
                    "entry and exit must be opposite sides".into(),
                ))
            }
        };

        let gross_pnl = match side {
            PositionSide::Long => (exit_price - entry_price) * quantity,
            PositionSide::Short => (entry_price - exit_price) * quantity,
            _ => {
                return Err(AnalyticsError::TradeMismatch(
                    "unsupported position side".into(),
                ))
            }
        };
        // Fees are ledger Money; gross and net stay f64 statistics, so the
        // sum converts once, at the boundary, rounded to minor units.
        let fees_minor = Money::new(
            entry.costs().minor() + exit.costs().minor(),
            entry.costs().currency(),
        );
        let fees_major = fees_minor.to_major_f64();
        let fees = fees_major;
        let net_pnl = gross_pnl - fees;
        let entry_notional = entry_price * quantity;
        let pnl_pct = if entry_notional != 0.0 {
            net_pnl / entry_notional
        } else {
            0.0
        };

        Ok(Self {
            instrument_id: entry.instrument_id().clone(),
            side,
            quantity,
            entry_price,
            exit_price,
            entry_ts: entry.ts_event(),
            exit_ts: exit.ts_event(),
            gross_pnl,
            fees,
            net_pnl,
            pnl_pct,
        })
    }

    /// Constructs a round trip directly from explicit values.
    ///
    /// Useful in tests and when importing external trade logs.
    #[allow(clippy::too_many_arguments)]
    pub fn new(
        instrument_id: InstrumentId,
        side: PositionSide,
        quantity: f64,
        entry_price: f64,
        exit_price: f64,
        entry_ts: UnixNanos,
        exit_ts: UnixNanos,
        fees: f64,
    ) -> Self {
        let gross_pnl = match side {
            PositionSide::Long => (exit_price - entry_price) * quantity,
            PositionSide::Short => (entry_price - exit_price) * quantity,
            _ => 0.0,
        };
        let net_pnl = gross_pnl - fees;
        let entry_notional = entry_price * quantity;
        let pnl_pct = if entry_notional != 0.0 {
            net_pnl / entry_notional
        } else {
            0.0
        };
        Self {
            instrument_id,
            side,
            quantity,
            entry_price,
            exit_price,
            entry_ts,
            exit_ts,
            gross_pnl,
            fees,
            net_pnl,
            pnl_pct,
        }
    }

    /// Returns the holding period in nanoseconds.
    pub fn duration_nanos(&self) -> u64 {
        self.exit_ts.as_u64().saturating_sub(self.entry_ts.as_u64())
    }
}
