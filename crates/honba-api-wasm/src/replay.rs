//! Pure Journal v1 replay, free of any wasm-bindgen types.
//!
//! Everything here runs natively, so it is conformance-tested without a wasm runtime (see
//! `tests/replay_conformance.rs`). The `#[wasm_bindgen]` layer in [`crate`] only forwards to
//! [`journal_header_json`] and [`replay_journal_json`].
//!
//! The audit trail itself is `honba_engine::audit` (L3). The browser has no filesystem, so the
//! NDJSON crosses the boundary as an in-memory string: this module parses it with
//! [`std::io::BufReader`] over the string's bytes (no file handle) and answers with a JSON string.
//! Nothing is reimplemented — the header, the records and the reconstructed [`ReplayState`] all
//! come from the engine.
//!
//! # Wire contract
//!
//! A journal is NDJSON: the first non-empty line is the provenance header (its `type` is
//! `"header"`), and every later line is one audit record. [`journal_header_json`] returns the
//! [`JournalHeader`] as JSON; [`replay_journal_json`] returns
//! `{"header": <JournalHeader>, "replay": <summary>}` where `summary` is
//! `{"trading_state", "dispatches", "positions", "orders", "rejections"}` with `positions` and
//! `orders` sorted by their key and `rejections` keyed by order id. The shape is deterministic
//! and stable.
//!
//! Invalid input is refused with a message rather than answered: an empty stream, a missing
//! header, a malformed line, or an unsupported journal version all throw a JavaScript `Error`
//! carrying the underlying `io`/`serde` message. No field is ever `NaN` or infinite; a
//! non-finite value that somehow reached the log is rendered as `null`.

use std::collections::BTreeMap;
use std::io::BufReader;

use honba_engine::{AuditKind, AuditLog, JournalHeader, JournalV1, ReplayState};
use honba_messages::{OrderSide, TradingState};
use serde_json::{json, Value};

/// Why a journal request was refused: a stable `reason` in a `reason: message` pair
/// (ADR 0012 rule 4).
///
/// Malformed header JSON classifies as [`ReplayError::InvalidHeaderJson`]; a header that parses
/// but carries wrong field values classifies as [`ReplayError::InvalidHeader`].
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum ReplayError {
    /// The stream has no non-empty line at all.
    EmptyJournal,
    /// The first line fails header JSON parsing.
    InvalidHeaderJson,
    /// The first line is not a journal v1 header.
    MissingHeader,
    /// The header fails `JournalHeader` deserialization.
    InvalidHeader,
    /// `journal_version` is not 1.
    UnsupportedJournalVersion,
    /// A later line fails record JSON parsing or record deserialization.
    InvalidRecord,
    /// The header fails JSON serialization for the answer.
    HeaderSerialize,
}

impl ReplayError {
    /// A stable machine-readable reason string.
    #[must_use]
    pub const fn reason(self) -> &'static str {
        match self {
            Self::EmptyJournal => "empty_journal",
            Self::InvalidHeaderJson => "invalid_header_json",
            Self::MissingHeader => "missing_header",
            Self::InvalidHeader => "invalid_header",
            Self::UnsupportedJournalVersion => "unsupported_journal_version",
            Self::InvalidRecord => "invalid_record",
            Self::HeaderSerialize => "header_serialize",
        }
    }
}

impl std::fmt::Display for ReplayError {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        write!(f, "{}: journal rejected", self.reason())
    }
}

/// Parses an in-memory NDJSON journal without touching the filesystem.
///
/// Every refusal is `(reason, message)`: the stable `reason` string comes from
/// `ReplayError::reason`, the message is the reader's original diagnostic, separated by
/// `": "` so the [`crate`] boundary can split them apart again.
/// Classifies one `read_ndjson` failure. The reader carries no phase, so header failures are
/// told apart by their engine message: `"missing journal v1 header"` is a missing header,
/// `"unsupported journal version: N"` a version refusal, and any header JSON or value failure
/// lands as the malformed-header or bad-header reason respectively; record failures carry the
/// record text.
fn classify(error: &std::io::Error, header_line: Option<&str>, record_lines: usize) -> ReplayError {
    let message = error.to_string();
    match error.kind() {
        std::io::ErrorKind::UnexpectedEof => ReplayError::EmptyJournal,
        std::io::ErrorKind::InvalidData if message == "missing journal v1 header" => {
            ReplayError::MissingHeader
        }
        std::io::ErrorKind::InvalidData if message.starts_with("unsupported journal version:") => {
            ReplayError::UnsupportedJournalVersion
        }
        // A header-value failure names the field; anything else from the header line is
        // malformed header JSON.
        std::io::ErrorKind::InvalidData if record_lines == 0 && header_line.is_some() => {
            if message.contains("missing field")
                || message.contains("invalid type")
                || message.contains("invalid value")
            {
                ReplayError::InvalidHeader
            } else {
                ReplayError::InvalidHeaderJson
            }
        }
        _ => ReplayError::InvalidRecord,
    }
}

fn parse_journal(journal_ndjson: &str) -> Result<JournalV1, (ReplayError, String)> {
    let mut lines = journal_ndjson
        .lines()
        .filter(|line| !line.trim().is_empty());
    let header_line = lines.next().map(str::to_string);
    let record_lines = lines.count();
    JournalV1::read_ndjson(BufReader::new(journal_ndjson.as_bytes())).map_err(|error| {
        let message = error.to_string();
        let reason = classify(&error, header_line.as_deref(), record_lines);
        (reason, message)
    })
}

fn pair(error: (ReplayError, String)) -> String {
    format!("{}: {}", error.0.reason(), error.1)
}

/// Renders a finite `f64` as a JSON number, and anything else as `null` (never `NaN`/infinity).
fn number(x: f64) -> Value {
    serde_json::Number::from_f64(x).map_or(Value::Null, Value::Number)
}

fn side_str(side: OrderSide) -> &'static str {
    match side {
        OrderSide::Buy => "buy",
        OrderSide::Sell => "sell",
        OrderSide::NoOrderSide => "no_order_side",
        // `OrderSide` is `#[non_exhaustive]`; an unknown side reads as no side.
        _ => "no_order_side",
    }
}

fn trading_state_str(state: TradingState) -> &'static str {
    match state {
        TradingState::Active => "active",
        TradingState::Reducing => "reducing",
        TradingState::Halted => "halted",
    }
}

fn header_value(header: &JournalHeader) -> Result<Value, String> {
    serde_json::to_value(header)
        .map_err(|error| pair((ReplayError::HeaderSerialize, error.to_string())))
}

/// The final rejection reason per order id, as recorded by the replayed [`ReplayState`].
///
/// The engine exposes rejections only by lookup ([`ReplayState::rejection`]); the order ids come
/// from the log's own risk-refusal and pre-gate rejection records, so the map holds exactly the
/// rejected orders and no others. Later records overwrite earlier ones, matching the state.
fn rejection_reasons(state: &ReplayState, log: &AuditLog) -> BTreeMap<String, String> {
    let mut rejections = BTreeMap::new();
    for record in log.records() {
        let order_id = match &record.kind {
            AuditKind::RiskRefused { order_id, .. } | AuditKind::OrderRejected { order_id, .. } => {
                order_id
            }
            _ => continue,
        };
        if let Some(reason) = state.rejection(order_id) {
            rejections.insert(order_id.clone(), reason.to_string());
        }
    }
    rejections
}

/// Parses the journal and returns its header as JSON.
///
/// # Errors
///
/// A `reason: message` pair for an empty stream, a missing or malformed header, an unsupported
/// journal version, or a header that fails serialization.
pub fn journal_header_json(journal_ndjson: &str) -> Result<String, String> {
    let journal = parse_journal(journal_ndjson).map_err(pair)?;
    serde_json::to_string(&journal.header)
        .map_err(|error| pair((ReplayError::HeaderSerialize, error.to_string())))
}

/// Replays the journal and returns the compact summary JSON described in the module docs.
///
/// # Errors
///
/// A `reason: message` pair for an empty stream, a missing or malformed header, a malformed
/// record line, an unsupported journal version, or a header that fails serialization.
pub fn replay_journal_json(journal_ndjson: &str) -> Result<String, String> {
    let journal = parse_journal(journal_ndjson).map_err(pair)?;
    let state = ReplayState::from_log(&journal.log);

    let positions: Vec<Value> = state
        .positions()
        .iter()
        .map(|(instrument, quantity)| (instrument.to_string(), number(*quantity)))
        .collect::<BTreeMap<_, _>>()
        .into_iter()
        .map(|(instrument, quantity)| json!({"instrument": instrument, "quantity": quantity}))
        .collect();

    let orders: Vec<Value> = state
        .orders()
        .values()
        .map(|order| {
            let lifecycle = match order.lifecycle {
                Some(kind) => Value::String(kind.to_string()),
                None => Value::Null,
            };
            (
                order.order_id.clone(),
                json!({
                    "order_id": order.order_id,
                    "instrument": order.instrument.to_string(),
                    "side": side_str(order.side),
                    "filled_qty": number(order.filled_qty),
                    "lifecycle": lifecycle,
                    "cancel_requested": order.cancel_requested,
                }),
            )
        })
        .collect::<BTreeMap<_, _>>()
        .into_values()
        .collect();

    let rejections: serde_json::Map<String, Value> = rejection_reasons(&state, &journal.log)
        .into_iter()
        .map(|(order_id, reason)| (order_id, Value::String(reason)))
        .collect();

    let summary = json!({
        "trading_state": trading_state_str(state.trading_state()),
        "dispatches": state.dispatches(),
        "positions": positions,
        "orders": orders,
        "rejections": Value::Object(rejections),
    });

    let out = json!({
        "header": header_value(&journal.header)?,
        "replay": summary,
    });
    Ok(out.to_string())
}

/// Parses the journal and returns the number of audit records after the header.
///
/// # Errors
///
/// A `reason: message` pair for an empty stream, a missing or malformed header, a malformed
/// record line, or an unsupported journal version.
pub fn journal_record_count(journal_ndjson: &str) -> Result<usize, String> {
    let journal = parse_journal(journal_ndjson).map_err(pair)?;
    Ok(journal.log.len())
}
