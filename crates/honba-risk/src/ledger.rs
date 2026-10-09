//! Idempotent fill ledger with content fingerprint deduplication (E2-S11).

use std::collections::BTreeSet;
use std::fmt;

use honba_entities::{Money, Trade};
use honba_messages::{InstrumentId, OrderId, OrderSide, TradeId, UnixNanos, VenueOrderId};
use serde::{Deserialize, Serialize};
use sha2::{Digest, Sha256};

/// Content fingerprint uniquely identifying a fill execution.
///
/// Deterministically computed as a SHA-256 hash formatted as lowercase hex.
#[derive(Clone, Debug, PartialEq, Eq, PartialOrd, Ord, Hash, Serialize, Deserialize)]
pub struct FillFingerprint(String);

impl FillFingerprint {
    /// Creates a fingerprint directly from its hex representation.
    pub fn new(hex: impl Into<String>) -> Self {
        Self(hex.into())
    }

    /// Returns the fingerprint as a lowercase hex slice.
    pub fn as_str(&self) -> &str {
        &self.0
    }

    /// Deterministically computes a content fingerprint from fill attributes.
    #[allow(clippy::too_many_arguments)]
    pub fn compute(
        order_id: &str,
        trade_id: Option<&str>,
        venue_order_id: Option<&str>,
        instrument_id: &str,
        side: &str,
        quantity: f64,
        price: f64,
        ts_event: u64,
    ) -> Self {
        let mut hasher = Sha256::new();
        hasher.update(order_id.as_bytes());
        hasher.update(b"|");
        if let Some(tid) = trade_id {
            hasher.update(tid.as_bytes());
        }
        hasher.update(b"|");
        if let Some(vid) = venue_order_id {
            hasher.update(vid.as_bytes());
        }
        hasher.update(b"|");
        hasher.update(instrument_id.as_bytes());
        hasher.update(b"|");
        hasher.update(side.as_bytes());
        hasher.update(b"|");
        hasher.update(quantity.to_bits().to_be_bytes());
        hasher.update(b"|");
        hasher.update(price.to_bits().to_be_bytes());
        hasher.update(b"|");
        hasher.update(ts_event.to_be_bytes());

        let hash_bytes = hasher.finalize();
        Self(format!("{hash_bytes:x}"))
    }

    /// Computes a content fingerprint directly from a [`Trade`].
    pub fn from_trade(trade: &Trade) -> Self {
        let side_str = match trade.side() {
            OrderSide::Buy => "buy",
            OrderSide::Sell => "sell",
            _ => "unknown",
        };
        let inst_str = format!(
            "{}.{}",
            trade.instrument_id().symbol(),
            trade.instrument_id().exchange().as_str()
        );
        Self::compute(
            trade.order_id().as_str(),
            None,
            None,
            &inst_str,
            side_str,
            trade.quantity(),
            trade.price(),
            trade.ts_event().as_u64(),
        )
    }

    /// Computes a content fingerprint with optional trade id and venue order id.
    pub fn from_trade_with_ids(
        trade: &Trade,
        trade_id: Option<&TradeId>,
        venue_order_id: Option<&VenueOrderId>,
    ) -> Self {
        let side_str = match trade.side() {
            OrderSide::Buy => "buy",
            OrderSide::Sell => "sell",
            _ => "unknown",
        };
        let inst_str = format!(
            "{}.{}",
            trade.instrument_id().symbol(),
            trade.instrument_id().exchange().as_str()
        );
        Self::compute(
            trade.order_id().as_str(),
            trade_id.map(|t| t.as_str()),
            venue_order_id.map(|v| v.as_str()),
            &inst_str,
            side_str,
            trade.quantity(),
            trade.price(),
            trade.ts_event().as_u64(),
        )
    }
}

impl fmt::Display for FillFingerprint {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.write_str(&self.0)
    }
}

/// A recorded fill in the idempotent fill ledger.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct FillRecord {
    /// The unique content fingerprint.
    pub fingerprint: FillFingerprint,
    /// Client order id.
    pub order_id: OrderId,
    /// Exchange trade identifier, if available.
    pub trade_id: Option<TradeId>,
    /// Venue order identifier, if available.
    pub venue_order_id: Option<VenueOrderId>,
    /// Instrument traded.
    pub instrument_id: InstrumentId,
    /// Trade side.
    pub side: OrderSide,
    /// Quantity filled.
    pub quantity: f64,
    /// Price filled.
    pub price: f64,
    /// Total transaction costs.
    pub costs: Money,
    /// Execution timestamp.
    pub ts_event: UnixNanos,
}

impl FillRecord {
    /// Creates a record from a [`Trade`].
    pub fn from_trade(trade: &Trade) -> Self {
        let fingerprint = FillFingerprint::from_trade(trade);
        Self {
            fingerprint,
            order_id: trade.order_id().clone(),
            trade_id: None,
            venue_order_id: None,
            instrument_id: trade.instrument_id().clone(),
            side: trade.side(),
            quantity: trade.quantity(),
            price: trade.price(),
            costs: trade.costs(),
            ts_event: trade.ts_event(),
        }
    }

    /// Creates a record from a [`Trade`] with known identifiers.
    pub fn from_trade_with_ids(
        trade: &Trade,
        trade_id: Option<TradeId>,
        venue_order_id: Option<VenueOrderId>,
    ) -> Self {
        let fingerprint =
            FillFingerprint::from_trade_with_ids(trade, trade_id.as_ref(), venue_order_id.as_ref());
        Self {
            fingerprint,
            order_id: trade.order_id().clone(),
            trade_id,
            venue_order_id,
            instrument_id: trade.instrument_id().clone(),
            side: trade.side(),
            quantity: trade.quantity(),
            price: trade.price(),
            costs: trade.costs(),
            ts_event: trade.ts_event(),
        }
    }
}

/// An idempotent fill ledger that deduplicates incoming fills via content fingerprints.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct FillLedger {
    seen: BTreeSet<FillFingerprint>,
    records: Vec<FillRecord>,
}

impl FillLedger {
    /// Creates a new, empty fill ledger.
    pub fn new() -> Self {
        Self::default()
    }

    /// Records a trade fill idempotently.
    ///
    /// Returns `true` if the fill is new and recorded.
    /// Returns `false` if the fill was already seen (duplicate), leaving the ledger unchanged.
    pub fn record(&mut self, trade: &Trade) -> bool {
        let record = FillRecord::from_trade(trade);
        self.record_record(record)
    }

    /// Records a trade fill with optional trade id and venue order id idempotently.
    pub fn record_with_ids(
        &mut self,
        trade: &Trade,
        trade_id: Option<TradeId>,
        venue_order_id: Option<VenueOrderId>,
    ) -> bool {
        let record = FillRecord::from_trade_with_ids(trade, trade_id, venue_order_id);
        self.record_record(record)
    }

    /// Records a fill record idempotently.
    ///
    /// Returns `true` if recorded, or `false` if the fingerprint is already present.
    pub fn record_record(&mut self, record: FillRecord) -> bool {
        if self.seen.contains(&record.fingerprint) {
            return false;
        }
        self.seen.insert(record.fingerprint.clone());
        self.records.push(record);
        true
    }

    /// Returns `true` if a fill with `fingerprint` has already been recorded.
    pub fn contains(&self, fingerprint: &FillFingerprint) -> bool {
        self.seen.contains(fingerprint)
    }

    /// Alias for `contains` for duplicate checking clarity.
    pub fn is_duplicate(&self, fingerprint: &FillFingerprint) -> bool {
        self.contains(fingerprint)
    }

    /// Returns the number of distinct fills recorded.
    pub fn len(&self) -> usize {
        self.records.len()
    }

    /// Returns `true` if no fills have been recorded.
    pub fn is_empty(&self) -> bool {
        self.records.is_empty()
    }

    /// Returns the list of recorded fills in insertion order.
    pub fn records(&self) -> &[FillRecord] {
        &self.records
    }

    /// Returns the set of all observed fingerprints.
    pub fn fingerprints(&self) -> &BTreeSet<FillFingerprint> {
        &self.seen
    }
}
