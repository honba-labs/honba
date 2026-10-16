//! Wire types of the per-user profile (`/profile` routes).
//!
//! The server stores a profile as one JSON document per user; these are the shapes the
//! routes read and write. The store itself lives in `honba-api-rest`.

use std::collections::BTreeMap;

use schemars::JsonSchema;
use serde::{Deserialize, Serialize};
use serde_json::Value;

/// A source feeding a [`Module`], for example a broker adapter or a symbol list.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
pub struct Source {
    /// Source kind, for example `broker`, `symbols` or `parquet`.
    pub kind: String,
    /// Kind-specific reference (an adapter name, a list name, a directory).
    pub reference: String,
}

/// A named collection of sources.
#[derive(Debug, Clone, PartialEq, Eq, Default, Serialize, Deserialize, JsonSchema)]
pub struct Module {
    /// Stable module id.
    pub id: String,
    /// Display name.
    pub name: String,
    /// The sources the module groups.
    pub sources: Vec<Source>,
    /// Whether the module is active.
    pub enabled: bool,
}

/// Everything stored for one user (`GET` / `PUT /profile`).
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema)]
#[serde(default)]
pub struct Profile {
    /// Monotonic revision, bumped by every successful write. A `PUT /profile` body carries the
    /// revision the client last read.
    pub revision: u64,
    /// Named workbench layouts (opaque to the server).
    pub layouts: BTreeMap<String, Value>,
    /// Modules by id.
    pub modules: BTreeMap<String, Module>,
    /// Named watchlists of symbols.
    pub watchlists: BTreeMap<String, Vec<String>>,
    /// Shortlisted symbols.
    pub shortlist: Vec<String>,
    /// Preferences and theme (opaque to the server).
    pub preferences: Value,
    /// Workbench grid and the symbol in each pane (opaque to the server).
    pub workbench: Value,
    /// Alert rules (opaque to the server; may hold webhook URLs, visible only to this user).
    pub alerts: Value,
}

/// `GET /profile/layouts` response: the named layouts by name.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema)]
pub struct LayoutsResponse {
    /// Layouts by name (opaque to the server).
    pub layouts: BTreeMap<String, Value>,
}

/// `GET /profile/modules` response: the modules by id.
#[derive(Debug, Clone, PartialEq, Default, Serialize, Deserialize, JsonSchema)]
pub struct ModulesResponse {
    /// Modules by id.
    pub modules: BTreeMap<String, Module>,
}

/// Answer to a profile write that returns no profile: the revision now stored.
#[derive(Debug, Clone, PartialEq, Eq, Serialize, Deserialize, JsonSchema)]
pub struct ProfileRevision {
    /// The profile's revision after the write.
    pub revision: u64,
}
