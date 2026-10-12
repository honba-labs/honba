//! Declarative per-adapter canonical\[<->\]broker mapping tables (E1-S4).
//!
//! Each adapter ships one JSON file under `schema/adapter_mappings/` listing,
//! for every canonical wire enum ([`Product`], [`OrderType`], [`TimeInForce`]),
//! the broker spellings it accepts in both directions. The files are data, not
//! code: teaching an adapter a new product is a JSON edit plus a round-trip
//! test, never a new match arm.
//!
//! The files are embedded with [`include_str!`] so they ship inside the library
//! and every consumer (including the round-trip tests) reads the same bytes.
//! Broker product codes such as Zerodha's `CNC`/`MIS`/`NRML` never appear in
//! the core enums (ADR 0010); they live only here.

use std::collections::BTreeMap;
use std::fmt;

use serde::de::DeserializeOwned;
use serde::{Deserialize, Serialize};

use super::order::{OrderType, Product, TimeInForce};

/// The embedded mapping files, one per adapter, as `(adapter, json)`.
pub const BUNDLED: &[(&str, &str)] = &[
    (
        "zerodha",
        include_str!("../../schema/adapter_mappings/zerodha.json"),
    ),
    (
        "dhan",
        include_str!("../../schema/adapter_mappings/dhan.json"),
    ),
];

/// One canonical enum's mapping in both directions.
///
/// `canonical_to_broker` is the encoding direction; `broker_to_canonical` is
/// its declared inverse. `broker_aliases` lists extra broker spellings that
/// decode to a canonical value (Dhan's `MTF`, `CO`, `BO`) but are never
/// emitted: a round trip recognizes them, it does not reproduce them.
#[derive(Clone, Debug, Default, Deserialize)]
pub struct WireMapping {
    #[serde(default)]
    canonical_to_broker: BTreeMap<String, String>,
    #[serde(default)]
    broker_to_canonical: BTreeMap<String, String>,
    #[serde(default)]
    broker_aliases: BTreeMap<String, String>,
}

impl WireMapping {
    /// The canonical wire string -> broker string table.
    pub fn canonical_to_broker(&self) -> &BTreeMap<String, String> {
        &self.canonical_to_broker
    }

    /// The broker string -> canonical wire string table.
    pub fn broker_to_canonical(&self) -> &BTreeMap<String, String> {
        &self.broker_to_canonical
    }

    /// Extra broker spellings that decode but are not the canonical encoding.
    pub fn broker_aliases(&self) -> &BTreeMap<String, String> {
        &self.broker_aliases
    }

    fn to_broker<'a>(
        &'a self,
        adapter: &str,
        domain: &'static str,
        canonical: &str,
    ) -> Result<&'a str, MappingError> {
        self.canonical_to_broker
            .get(canonical)
            .map(String::as_str)
            .ok_or_else(|| MappingError::Unsupported {
                adapter: adapter.to_string(),
                domain,
                value: canonical.to_string(),
            })
    }

    fn lookup<'a>(
        &'a self,
        adapter: &str,
        domain: &'static str,
        broker: &str,
    ) -> Result<&'a str, MappingError> {
        self.broker_to_canonical
            .get(broker)
            .or_else(|| self.broker_aliases.get(broker))
            .map(String::as_str)
            .ok_or_else(|| MappingError::UnknownBroker {
                adapter: adapter.to_string(),
                domain,
                value: broker.to_string(),
            })
    }
}

/// One adapter's three mapping tables, parsed from a bundled JSON file.
#[derive(Clone, Debug, Deserialize)]
pub struct AdapterMapping {
    adapter: String,
    #[serde(default)]
    description: Option<String>,
    #[serde(default)]
    products: WireMapping,
    #[serde(default)]
    order_types: WireMapping,
    #[serde(default)]
    time_in_force: WireMapping,
}

impl AdapterMapping {
    /// The adapter's stable name (`zerodha`, `dhan`).
    pub fn adapter(&self) -> &str {
        &self.adapter
    }

    /// A human note carried by the mapping file, if any.
    pub fn description(&self) -> Option<&str> {
        self.description.as_deref()
    }

    /// The product mapping table.
    pub fn products(&self) -> &WireMapping {
        &self.products
    }

    /// The order-type mapping table.
    pub fn order_types(&self) -> &WireMapping {
        &self.order_types
    }

    /// The time-in-force mapping table.
    pub fn time_in_force(&self) -> &WireMapping {
        &self.time_in_force
    }

    /// Encodes a canonical product to the broker's spelling.
    pub fn product_to_broker(&self, product: Product) -> Result<&str, MappingError> {
        self.products
            .to_broker(&self.adapter, "product", &wire_string(&product)?)
    }

    /// Decodes a broker product spelling to a canonical product.
    pub fn product_from_broker(&self, broker: &str) -> Result<Product, MappingError> {
        let canonical = self.products.lookup(&self.adapter, "product", broker)?;
        from_wire(canonical, "product")
    }

    /// Encodes a canonical order type to the broker's spelling.
    pub fn order_type_to_broker(&self, order_type: OrderType) -> Result<&str, MappingError> {
        self.order_types
            .to_broker(&self.adapter, "order_type", &wire_string(&order_type)?)
    }

    /// Decodes a broker order-type spelling to a canonical order type.
    pub fn order_type_from_broker(&self, broker: &str) -> Result<OrderType, MappingError> {
        let canonical = self
            .order_types
            .lookup(&self.adapter, "order_type", broker)?;
        from_wire(canonical, "order_type")
    }

    /// Encodes a canonical time-in-force to the broker's spelling.
    pub fn time_in_force_to_broker(&self, tif: TimeInForce) -> Result<&str, MappingError> {
        self.time_in_force
            .to_broker(&self.adapter, "time_in_force", &wire_string(&tif)?)
    }

    /// Decodes a broker time-in-force spelling to a canonical value.
    pub fn time_in_force_from_broker(&self, broker: &str) -> Result<TimeInForce, MappingError> {
        let canonical = self
            .time_in_force
            .lookup(&self.adapter, "time_in_force", broker)?;
        from_wire(canonical, "time_in_force")
    }
}

/// Parses every embedded mapping file.
///
/// Returns the first malformed file as a typed error so a broken table fails
/// loudly rather than silently dropping an adapter.
pub fn bundled_mappings() -> Result<Vec<AdapterMapping>, MappingError> {
    BUNDLED
        .iter()
        .map(|(name, json)| {
            serde_json::from_str(json).map_err(|e| MappingError::Malformed {
                detail: format!("{name}.json: {e}"),
            })
        })
        .collect()
}

/// The canonical wire spelling of a fieldless wire enum.
fn wire_string<T: Serialize>(value: &T) -> Result<String, MappingError> {
    serde_json::to_value(value)
        .ok()
        .and_then(|value| value.as_str().map(str::to_string))
        .ok_or_else(|| MappingError::Malformed {
            detail: "wire enum did not serialize as a string".to_string(),
        })
}

/// Parses a canonical wire spelling back into its enum.
fn from_wire<T: DeserializeOwned>(
    canonical: &str,
    domain: &'static str,
) -> Result<T, MappingError> {
    serde_json::from_value(serde_json::Value::String(canonical.to_string())).map_err(|e| {
        MappingError::Malformed {
            detail: format!("{domain}: unknown canonical spelling {canonical:?}: {e}"),
        }
    })
}

/// A mapping lookup failure.
#[derive(Clone, Debug, PartialEq, Eq)]
pub enum MappingError {
    /// The adapter declares no broker spelling for this canonical value (for
    /// example Zerodha has no trailing-stop order type).
    Unsupported {
        /// The adapter name.
        adapter: String,
        /// The mapping domain (`product`, `order_type`, `time_in_force`).
        domain: &'static str,
        /// The canonical wire value that has no broker spelling.
        value: String,
    },
    /// A broker spelling that is not in the table.
    UnknownBroker {
        /// The adapter name.
        adapter: String,
        /// The mapping domain (`product`, `order_type`, `time_in_force`).
        domain: &'static str,
        /// The unrecognized broker string.
        value: String,
    },
    /// A mapping file or canonical spelling that could not be decoded.
    Malformed {
        /// What was being decoded and why it failed.
        detail: String,
    },
}

impl fmt::Display for MappingError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::Unsupported {
                adapter,
                domain,
                value,
            } => write!(f, "{adapter} has no {domain} mapping for {value:?}"),
            Self::UnknownBroker {
                adapter,
                domain,
                value,
            } => write!(f, "{adapter} does not recognize {domain} {value:?}"),
            Self::Malformed { detail } => write!(f, "malformed mapping: {detail}"),
        }
    }
}

impl std::error::Error for MappingError {}
