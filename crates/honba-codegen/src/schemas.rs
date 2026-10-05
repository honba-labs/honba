//! Schema emission, one type registry walked five ways.
//!
//! The central abstraction is [`SchemaSet`]: a flat map of named JSON Schemas,
//! normalized so every internal reference is written the same way regardless of
//! which `schemars` version produced it. Everything downstream — the TS
//! renderer, the `.pyi` renderer, the MCP tool builder — reads that map, so a
//! defect in normalization cannot reach four artifacts separately.

use schemars::{schema::RootSchema, JsonSchema};
use serde_json::{json, Value};

use super::registry;

/// A flat map of named schemas with all internal references normalized.
///
/// References are stored as bare definition names (`{"$ref": "Bar"}`) and
/// rewritten to the target dialect's prefix at render time. `schemars` 0.8 emits
/// `#/definitions/X` while the emitted bundle declares `$defs`; normalizing
/// once here is what stops that mismatch from producing an unresolvable `$ref`
/// in every artifact.
#[derive(Clone, Debug, Default)]
pub struct SchemaSet {
    entries: std::collections::BTreeMap<String, Value>,
}

impl SchemaSet {
    /// An empty set.
    pub fn new() -> Self {
        Self::default()
    }

    /// Adds or replaces a named schema.
    pub fn insert(&mut self, name: impl Into<String>, schema: Value) {
        self.entries.insert(name.into(), schema);
    }

    /// Borrows a named schema.
    pub fn get(&self, name: &str) -> Option<&Value> {
        self.entries.get(name)
    }

    /// Number of registered schemas.
    pub fn len(&self) -> usize {
        self.entries.len()
    }

    /// Whether the set is empty.
    pub fn is_empty(&self) -> bool {
        self.entries.is_empty()
    }

    /// Iterates the schemas in name order, so output is deterministic.
    pub fn iter(&self) -> impl Iterator<Item = (&String, &Value)> {
        self.entries.iter()
    }

    /// Names of every registered schema, in order.
    pub fn names(&self) -> Vec<&str> {
        self.entries.keys().map(String::as_str).collect()
    }

    /// Walks `T`'s schema, adding it and everything it references.
    ///
    /// Named types come back under the `title` `schemars` derives; the
    /// `preferred_name` is used as a fallback when a type has no title (newtype
    /// wrappers such as `InstrumentId`).
    pub fn add_type<T: JsonSchema>(&mut self, preferred_name: &str) {
        let root: RootSchema =
            schemars::gen::SchemaGenerator::default().into_root_schema_for::<T>();
        let root_value = serde_json::to_value(&root).unwrap_or_else(|_| json!({}));

        if let Some(defs) = root_value.get("definitions").and_then(Value::as_object) {
            for (name, schema) in defs {
                if name == STR_PLACEHOLDER {
                    continue;
                }
                self.insert(name.clone(), inline_str_placeholder(schema));
            }
        }

        let title = root
            .schema
            .metadata
            .as_ref()
            .and_then(|m| m.title.clone())
            .unwrap_or_else(|| preferred_name.to_string());
        let mut own = serde_json::to_value(&root.schema).unwrap_or_else(|_| json!({}));
        if let Value::Object(map) = &mut own {
            map.remove("title");
        }
        // `schemars` titles a generic instantiation `ResponseEnvelope_for_Null`,
        // and registers `String` for types that reach `str`. Neither is a
        // published contract type: keep the requested name so the artifact
        // refers to `ResponseEnvelope`, and drop the `str` placeholder.
        self.insert(canonical_name(&title, preferred_name), own);
    }

    /// Returns a schema with its references rewritten for a given `$ref` prefix.
    ///
    /// Callers that embed a schema fragment — OpenAPI query parameters, for
    /// instance — must rewrite too, or the fragment carries the generator's
    /// internal `#/definitions/...` spelling into the output.
    pub fn normalized(&self, name: &str, prefix: &str) -> Value {
        match self.get(name) {
            Some(schema) => rewrite_refs(schema, prefix),
            None => json!({}),
        }
    }

    /// Returns every `#/$ref`-style reference used anywhere in the set.
    ///
    /// Used by the dangling-reference test: a `$ref` pointing at a name that is
    /// not registered is an invalid schema, and the MCP tools used to ship one
    /// (`StrategyManifest`) that silently did not exist.
    pub fn referenced_names(&self) -> std::collections::BTreeSet<String> {
        let mut out = std::collections::BTreeSet::new();
        for schema in self.entries.values() {
            collect_refs(schema, &mut out);
        }
        out
    }

    /// Names referenced but never registered.
    pub fn dangling_references(&self) -> Vec<String> {
        self.referenced_names()
            .into_iter()
            .filter(|name| !self.entries.contains_key(name))
            .collect()
    }

    /// Renders the set as the `$defs` block of a JSON Schema bundle.
    pub fn to_defs(&self, prefix: &str) -> Value {
        let mut out = serde_json::Map::new();
        for (name, schema) in &self.entries {
            out.insert(name.clone(), rewrite_refs(schema, prefix));
        }
        Value::Object(out)
    }

    /// The set as an OpenAPI `components.schemas` map.
    ///
    /// Every reference is written `#/components/schemas/<name>`. A bare
    /// `{"$ref": "Bar"}` is a relative URI reference, which OpenAPI tooling
    /// resolves against the document's location rather than the component map,
    /// so it never names the component.
    pub fn to_components(&self) -> Value {
        self.to_defs(COMPONENTS_PREFIX)
    }

    /// The names `roots` reference, directly or transitively, roots included.
    ///
    /// Used to give a self-contained schema (an MCP tool's `inputSchema`) the
    /// exact `$defs` it needs and nothing more.
    pub fn closure<'a>(
        &self,
        roots: impl IntoIterator<Item = &'a str>,
    ) -> std::collections::BTreeSet<String> {
        let mut seen = std::collections::BTreeSet::new();
        let mut pending: Vec<String> = roots.into_iter().map(str::to_string).collect();
        while let Some(name) = pending.pop() {
            if !seen.insert(name.clone()) {
                continue;
            }
            if let Some(schema) = self.get(&name) {
                let mut refs = std::collections::BTreeSet::new();
                collect_refs(schema, &mut refs);
                pending.extend(refs.into_iter().filter(|r| !seen.contains(r)));
            }
        }
        seen
    }
}

/// The `$ref` prefix of an OpenAPI component.
pub const COMPONENTS_PREFIX: &str = "#/components/schemas/";

/// Normalizes a `schemars` title to a published contract name.
///
/// A generic instantiation arrives as `Name_for_T`; the type argument is an
/// implementation detail of the generator, not part of the contract.
fn canonical_name(title: &str, preferred: &str) -> String {
    match title.split_once("_for_") {
        Some((base, _)) if !base.is_empty() => base.to_string(),
        _ if title == "String" => preferred.to_string(),
        _ => title.to_string(),
    }
}

/// The `str` placeholder `schemars` registers when a type reaches a primitive.
///
/// It is not a domain type, and publishing it would put a `String` entry in the
/// schema bundle that reads as part of the contract. References to it become an
/// inline `{"type": "string"}` instead.
const STR_PLACEHOLDER: &str = "String";

/// Substitutes the `str` placeholder with an inline string schema.
fn inline_str_placeholder(value: &Value) -> Value {
    match value {
        Value::Object(map) => {
            let mut out = serde_json::Map::with_capacity(map.len());
            for (key, child) in map {
                if key == "$ref" && child.as_str() == Some(STR_PLACEHOLDER) {
                    out.insert(key.clone(), json!({"type": "string"}));
                } else {
                    out.insert(key.clone(), inline_str_placeholder(child));
                }
            }
            Value::Object(out)
        }
        Value::Array(items) => Value::Array(items.iter().map(inline_str_placeholder).collect()),
        other => other.clone(),
    }
}

fn collect_refs(value: &Value, out: &mut std::collections::BTreeSet<String>) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                if key == "$ref" {
                    if let Some(name) = child.as_str() {
                        out.insert(bare_name(name).to_string());
                    }
                } else {
                    collect_refs(child, out);
                }
            }
        }
        Value::Array(items) => {
            for item in items {
                collect_refs(item, out);
            }
        }
        _ => {}
    }
}

/// Strips any `#/definitions/`, `#/$defs/`, or `#/components/schemas/` prefix.
fn bare_name(reference: &str) -> &str {
    reference.rsplit('/').next().unwrap_or(reference)
}

/// Rewrites every `$ref` to carry `prefix`, recursively.
///
/// An empty prefix leaves references bare, which is what a component map wants:
/// inside `components.schemas`, `#/components/schemas/Bar` is correct, and the
/// renderer adds that prefix itself.
fn rewrite_refs(value: &Value, prefix: &str) -> Value {
    match value {
        Value::Object(map) => {
            let mut out = serde_json::Map::with_capacity(map.len());
            for (key, child) in map {
                if key == "$ref" {
                    if let Some(name) = child.as_str() {
                        let rewritten = if prefix.is_empty() {
                            bare_name(name).to_string()
                        } else {
                            format!("{prefix}{}", bare_name(name))
                        };
                        out.insert(key.clone(), Value::String(rewritten));
                        continue;
                    }
                }
                out.insert(key.clone(), rewrite_refs(child, prefix));
            }
            Value::Object(out)
        }
        Value::Array(items) => {
            Value::Array(items.iter().map(|i| rewrite_refs(i, prefix)).collect())
        }
        other => other.clone(),
    }
}

/// Every `$ref` string in `doc`, in document order.
pub fn local_refs(doc: &Value) -> Vec<String> {
    let mut out = Vec::new();
    collect_ref_strings(doc, &mut out);
    out
}

fn collect_ref_strings(value: &Value, out: &mut Vec<String>) {
    match value {
        Value::Object(map) => {
            for (key, child) in map {
                match (key.as_str(), child.as_str()) {
                    ("$ref", Some(reference)) => out.push(reference.to_string()),
                    _ => collect_ref_strings(child, out),
                }
            }
        }
        Value::Array(items) => items.iter().for_each(|i| collect_ref_strings(i, out)),
        _ => {}
    }
}

/// The `$ref`s in `doc` that do not resolve as a local JSON Pointer into `doc`.
///
/// Resolution follows RFC 6901 the way a schema or OpenAPI validator would: a
/// reference must be a `#`-fragment whose pointer (with `~1` and `~0` decoded)
/// names an existing node of the same document. A bare name such as
/// `"AccountConfig"` is a relative URI, not a local reference, and is reported.
pub fn unresolved_local_refs(doc: &Value) -> Vec<String> {
    local_refs(doc)
        .into_iter()
        .filter(|reference| {
            reference
                .strip_prefix('#')
                .map_or(true, |pointer| doc.pointer(pointer).is_none())
        })
        .collect()
}

/// The canonical registry: every type the wire contract publishes.
///
/// Returned bare (no `#/...` prefix) so consumers can render it for their own
/// dialect.
pub fn canonical_schemas() -> SchemaSet {
    let mut set = SchemaSet::new();
    registry::register_all(&mut set);
    set
}
