//! Serde structs for the Kite Connect v3 JSON wire format. These never leave the adapter.

use serde::Deserialize;

/// The `{"status": ..., "data": ...}` envelope around every JSON response.
#[derive(Debug, Clone, Deserialize)]
pub struct Envelope<T> {
    /// `"success"` or `"error"`.
    pub status: String,
    /// Payload on success.
    #[serde(default = "Option::default")]
    pub data: Option<T>,
    /// Human-readable error text.
    #[serde(default)]
    pub message: Option<String>,
    /// Kite error class, for example `TokenException`.
    #[serde(default)]
    pub error_type: Option<String>,
}

impl<T> Envelope<T> {
    /// Returns `true` when the envelope status is `"success"`.
    pub fn is_success(&self) -> bool {
        self.status == "success"
    }
}

/// An order as listed by `/orders` and `/orders/{id}`. Kite may omit or null most fields.
#[derive(Debug, Clone, Deserialize)]
pub struct OrderRecord {
    /// Venue order id.
    pub order_id: String,
    /// Exchange order id.
    #[serde(default)]
    pub exchange_order_id: Option<String>,
    /// Kite status string.
    #[serde(default)]
    pub status: Option<String>,
    /// Status detail, for example a rejection reason.
    #[serde(default)]
    pub status_message: Option<String>,
    /// Trading symbol.
    #[serde(default)]
    pub tradingsymbol: Option<String>,
    /// Exchange code.
    #[serde(default)]
    pub exchange: Option<String>,
    /// BUY or SELL.
    #[serde(default)]
    pub transaction_type: Option<String>,
    /// MARKET, LIMIT, SL or SL-M.
    #[serde(default)]
    pub order_type: Option<String>,
    /// CNC, MIS or NRML.
    #[serde(default)]
    pub product: Option<String>,
    /// Ordered quantity.
    #[serde(default)]
    pub quantity: Option<u64>,
    /// Filled quantity.
    #[serde(default)]
    pub filled_quantity: Option<u64>,
    /// Pending quantity.
    #[serde(default)]
    pub pending_quantity: Option<u64>,
    /// Limit price.
    #[serde(default)]
    pub price: Option<f64>,
    /// Trigger price.
    #[serde(default)]
    pub trigger_price: Option<f64>,
    /// Average fill price.
    #[serde(default)]
    pub average_price: Option<f64>,
    /// DAY or IOC.
    #[serde(default)]
    pub validity: Option<String>,
    /// Client tag.
    #[serde(default)]
    pub tag: Option<String>,
    /// `YYYY-MM-DD HH:MM:SS` in IST.
    #[serde(default)]
    pub order_timestamp: Option<String>,
    /// `YYYY-MM-DD HH:MM:SS` in IST.
    #[serde(default)]
    pub exchange_update_timestamp: Option<String>,
}

/// A trade as listed by `/trades`.
#[derive(Debug, Clone, Deserialize)]
pub struct TradeRecord {
    /// Trade id.
    pub trade_id: String,
    /// Parent order id.
    #[serde(default)]
    pub order_id: Option<String>,
    /// Trading symbol.
    #[serde(default)]
    pub tradingsymbol: Option<String>,
    /// Exchange code.
    #[serde(default)]
    pub exchange: Option<String>,
    /// BUY or SELL.
    #[serde(default)]
    pub transaction_type: Option<String>,
    /// Filled quantity.
    #[serde(default)]
    pub quantity: Option<u64>,
    /// Fill price (Kite sends `average_price`; `price` is accepted too).
    #[serde(default, alias = "price")]
    pub average_price: Option<f64>,
    /// `YYYY-MM-DD HH:MM:SS` in IST.
    #[serde(default)]
    pub fill_timestamp: Option<String>,
}

/// The `/session/token` payload.
#[derive(Clone, Deserialize)]
pub struct SessionData {
    /// Access token for subsequent calls.
    pub access_token: String,
    /// Kite user id.
    pub user_id: String,
}

impl std::fmt::Debug for SessionData {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("SessionData")
            .field("access_token", &"<redacted>")
            .field("user_id", &self.user_id)
            .finish()
    }
}

/// Payload of order placement, modification and cancellation.
#[derive(Debug, Clone, Deserialize)]
pub struct OrderIdData {
    /// Venue order id.
    pub order_id: String,
}
