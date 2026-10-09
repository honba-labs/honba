//! Durable risk state with atomic snapshot persistence (E2-S11).

use std::collections::BTreeMap;
use std::io::Write;
use std::path::Path;
use std::sync::atomic::{AtomicU64, Ordering};

use honba_entities::Trade;
use honba_messages::{InstrumentId, OrderId, OrderSide, UnixNanos, VenueOrderId};
use serde::{Deserialize, Serialize};

use crate::ledger::FillLedger;

static SNAPSHOT_NONCE: AtomicU64 = AtomicU64::new(1);

/// An in-flight order tracked by client order id.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
pub struct InFlightOrder {
    /// Client order identifier.
    pub order_id: OrderId,
    /// Instrument.
    pub instrument_id: InstrumentId,
    /// Order side.
    pub side: OrderSide,
    /// Ordered quantity.
    pub quantity: f64,
    /// Order price, if limit.
    pub price: Option<f64>,
    /// Venue order id, if acknowledged.
    pub venue_order_id: Option<VenueOrderId>,
    /// Timestamp when submitted.
    pub submitted_at: UnixNanos,
}

impl InFlightOrder {
    /// Creates an in-flight order.
    pub fn new(
        order_id: OrderId,
        instrument_id: InstrumentId,
        side: OrderSide,
        quantity: f64,
        price: Option<f64>,
        venue_order_id: Option<VenueOrderId>,
        submitted_at: UnixNanos,
    ) -> Self {
        Self {
            order_id,
            instrument_id,
            side,
            quantity,
            price,
            venue_order_id,
            submitted_at,
        }
    }
}

/// Durable risk state: in-flight orders, open positions, daily loss, venue capital,
/// and idempotent fill ledger with atomic snapshot persistence.
#[derive(Clone, Debug, Default, PartialEq, Serialize, Deserialize)]
pub struct DurableRiskState {
    /// In-flight orders keyed by client order id string.
    pub in_flight_orders: BTreeMap<String, InFlightOrder>,
    /// Open net positions per instrument string (`{symbol}.{exchange}`).
    pub positions: BTreeMap<String, f64>,
    /// Accumulated realized daily loss (positive number representing loss).
    pub daily_loss: f64,
    /// Per-venue capital / notional tracked.
    pub venue_capital: BTreeMap<String, f64>,
    /// Idempotent fill ledger preventing double-counting on reconnect or replay.
    pub fill_ledger: FillLedger,
    /// Timestamp (Unix nanoseconds) when state was last updated.
    pub last_updated_ns: u64,
}

impl DurableRiskState {
    /// Creates an empty durable risk state.
    pub fn new() -> Self {
        Self::default()
    }

    /// Adds or updates an in-flight order.
    pub fn add_in_flight(&mut self, order: InFlightOrder) {
        self.in_flight_orders
            .insert(order.order_id.as_str().to_string(), order);
    }

    /// Removes an in-flight order by client order id, returning it if present.
    pub fn remove_in_flight(&mut self, order_id: &str) -> Option<InFlightOrder> {
        self.in_flight_orders.remove(order_id)
    }

    /// Retrieves an in-flight order by client order id.
    pub fn in_flight(&self, order_id: &str) -> Option<&InFlightOrder> {
        self.in_flight_orders.get(order_id)
    }

    /// Returns `true` if an order is currently in-flight.
    pub fn is_in_flight(&self, order_id: &str) -> bool {
        self.in_flight_orders.contains_key(order_id)
    }

    /// Returns the net position for `instrument` (0.0 if flat/unknown).
    pub fn position(&self, instrument: &InstrumentId) -> f64 {
        let key = format!("{}.{}", instrument.symbol(), instrument.exchange().as_str());
        self.positions.get(&key).copied().unwrap_or(0.0)
    }

    /// Sets the net position for `instrument`.
    pub fn set_position(&mut self, instrument: &InstrumentId, qty: f64) {
        let key = format!("{}.{}", instrument.symbol(), instrument.exchange().as_str());
        if qty.abs() < 1e-9 {
            self.positions.remove(&key);
        } else {
            self.positions.insert(key, qty);
        }
    }

    /// Applies a fill to the durable state idempotently via the fill ledger.
    ///
    /// If the fill is already recorded, returns `false` and leaves positions,
    /// loss counters, and venue capital completely untouched.
    ///
    /// If new, records the fill, updates positions, updates venue notional, and returns `true`.
    pub fn record_fill(&mut self, trade: &Trade) -> bool {
        if !self.fill_ledger.record(trade) {
            return false;
        }

        let signed = match trade.side() {
            OrderSide::Sell => -trade.quantity(),
            _ => trade.quantity(),
        };

        let key = format!(
            "{}.{}",
            trade.instrument_id().symbol(),
            trade.instrument_id().exchange().as_str()
        );
        let current = self.positions.entry(key.clone()).or_insert(0.0);
        *current += signed;
        if current.abs() < 1e-9 {
            self.positions.remove(&key);
        }

        let venue = trade.instrument_id().exchange().as_str().to_string();
        *self.venue_capital.entry(venue).or_insert(0.0) += trade.notional();

        self.last_updated_ns = trade.ts_event().as_u64();
        true
    }

    /// Persists the risk state atomically to `path` via a temporary file + atomic rename.
    ///
    /// Ensures that an interrupted write never corrupts an existing snapshot.
    pub fn atomic_snapshot(&self, path: impl AsRef<Path>) -> std::io::Result<()> {
        let path = path.as_ref();
        let parent = path.parent().unwrap_or_else(|| Path::new("."));
        if !parent.as_os_str().is_empty() {
            std::fs::create_dir_all(parent)?;
        }
        let file_name = path
            .file_name()
            .and_then(|n| n.to_str())
            .unwrap_or("risk_state");

        let pid = std::process::id();
        let nonce = SNAPSHOT_NONCE.fetch_add(1, Ordering::Relaxed);
        let tmp_path = parent.join(format!(".{file_name}.tmp.{pid}.{nonce}"));

        {
            let file = std::fs::File::create(&tmp_path)?;
            let mut writer = std::io::BufWriter::new(file);
            serde_json::to_writer_pretty(&mut writer, self)
                .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))?;
            writer.flush()?;
            writer.get_ref().sync_all()?;
        }

        std::fs::rename(&tmp_path, path)?;
        Ok(())
    }

    /// Loads a persisted risk state snapshot from `path`.
    pub fn load_snapshot(path: impl AsRef<Path>) -> std::io::Result<Self> {
        let file = std::fs::File::open(path)?;
        let reader = std::io::BufReader::new(file);
        serde_json::from_reader(reader)
            .map_err(|e| std::io::Error::new(std::io::ErrorKind::InvalidData, e))
    }
}
