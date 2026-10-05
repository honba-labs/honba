//! honba-codegen: one registry of Rust wire types, rendered five ways.
//!
//! Rust is the source of truth for the contract (plan.md §4.1). This crate walks
//! the wire types once and emits:
//!
//! - **JSON Schema** — the conformance bundle, with every `$ref` resolvable.
//! - **OpenAPI 3.1** — including real `paths`, generated from the endpoint
//!   registry, so the served spec cannot describe a route the router lacks.
//! - **TypeScript** — for `honba-frontend`, with enums as literal unions rather
//!   than `any`.
//! - **Python `.pyi`** — type stubs for the `honba` package.
//! - **MCP tool schemas** — built from the same types, so a tool's arguments
//!   cannot drift from the DTOs.
//!
//! Generation is checked in CI by drift, not by review: regenerate and
//! `git diff --exit-code`.
//!
//! Two version axes are owned here (plan.md §4.1, §4.2):
//! `SCHEMA_VERSION` (wire shape, integer) and `API_VERSION` (endpoint surface,
//! semver). Both are re-exported from the crates that define the types so there
//! is exactly one of each.

#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]

use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use serde_json::{json, Value};

pub mod endpoints;
pub mod mcp;
pub mod registry;
pub mod schemas;
pub mod typescript;
pub mod typings;

pub use registry::{
    published_names, request_type_names, API_TYPES, CONFIG_TYPES, MANIFEST_TYPES, WIRE_ENUMS,
    WIRE_TYPES,
};
pub use schemas::SchemaSet;

/// Version of the JSON wire contract, owned here per plan.md §4.1.
///
/// Re-exported from `honba-messages`, where the envelope is defined.
pub const SCHEMA_VERSION: u32 = honba_messages::SCHEMA_VERSION;

/// Semantic version of the API surface, owned here per plan.md §4.1.
///
/// Re-exported from `honba-messages`, where the envelope is defined.
pub const API_VERSION: &str = honba_messages::API_VERSION;

/// Crate version, surfaced to clients so they can report what they are running.
pub const CORE_VERSION: &str = env!("CARGO_PKG_VERSION");

/// The generator: builds every artifact from the shared registry.
#[derive(Default)]
pub struct Codegen {
    set: SchemaSet,
}

impl Codegen {
    /// Builds a generator over the full registry.
    pub fn new() -> Self {
        Self {
            set: registry::full_registry(),
        }
    }

    /// The registry every artifact is rendered from.
    pub fn schemas(&self) -> &SchemaSet {
        &self.set
    }

    /// The JSON Schema conformance bundle.
    ///
    /// Uses `$defs` throughout, and every `$ref` points at `#/$defs/<name>`, so
    /// the bundle is internally resolvable. Draft 2020-12 is declared because
    /// `$defs` is a 2020-12 keyword; declaring draft-07 while using `$defs`
    /// produced a file whose nested references could not resolve.
    pub fn json_schema(&self) -> Value {
        json!({
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": "https://honba.dev/schema/domain.json",
            "title": "HonbaDomainBundle",
            "description": "Canonical Honba wire models, generated from the Rust source of truth",
            "schema_version": SCHEMA_VERSION,
            "api_version": API_VERSION,
            "$defs": self.set.to_defs("#/$defs/"),
        })
    }

    /// Writes [`Codegen::json_schema`] into `output_dir/domain_schema.json`.
    pub fn write_json_schema(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let path = output_dir.join("domain_schema.json");
        write_json_pretty(&path, &self.json_schema())?;
        println!(
            "Exported JSON Schema: {} ({} definitions)",
            path.display(),
            self.set.len()
        );
        Ok(path)
    }

    /// The OpenAPI 3.1 document, including `paths` from the endpoint registry.
    pub fn openapi(&self) -> Value {
        json!({
            "openapi": "3.1.0",
            "info": {
                "title": "Honba API",
                "version": API_VERSION,
                "description": "Generated from the Rust source of truth. Do not edit by hand."
            },
            "servers": [{"url": "/api/v1"}],
            "paths": endpoints::openapi_paths(&self.set),
            "components": {
                "schemas": self.set.to_components(),
            },
        })
    }

    /// Writes [`Codegen::openapi`] into `output_dir/openapi.json`.
    pub fn write_openapi(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let path = output_dir.join("openapi.json");
        write_json_pretty(&path, &self.openapi())?;
        println!("Exported OpenAPI: {}", path.display());
        Ok(path)
    }

    /// The TypeScript declarations for `honba-frontend`.
    pub fn typescript(&self) -> String {
        typescript::render(&self.set, SCHEMA_VERSION, API_VERSION)
    }

    /// Writes [`Codegen::typescript`] into `output_dir/domain.ts`.
    pub fn write_typescript(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let path = output_dir.join("domain.ts");
        std::fs::write(&path, self.typescript()).context("writing TypeScript")?;
        println!("Generated TypeScript definitions: {}", path.display());
        Ok(path)
    }

    /// The Python type stubs for the `honba` package.
    pub fn pyi(&self) -> String {
        typings::render_pyi(&self.set, SCHEMA_VERSION, API_VERSION)
    }

    /// Writes [`Codegen::pyi`] into `output_dir/__init__.pyi`.
    ///
    /// The target is `wire/generated/__init__.pyi`: generated and never edited,
    /// with hand-extended types living beside it.
    pub fn write_pyi(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let path = output_dir.join("__init__.pyi");
        std::fs::write(&path, self.pyi()).context("writing .pyi")?;
        println!("Generated Python stubs: {}", path.display());
        Ok(path)
    }

    /// The MCP tool schemas, built from the registry.
    pub fn mcp(&self) -> Value {
        mcp::render(&self.set)
    }

    /// Writes [`Codegen::mcp`] into `output_dir/mcp_tools.json`.
    pub fn write_mcp(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let path = output_dir.join("mcp_tools.json");
        write_json_pretty(&path, &self.mcp())?;
        println!("Exported MCP tool schemas: {}", path.display());
        Ok(path)
    }

    /// Writes every artifact under `base_dir`.
    ///
    /// Layout matches the Makefile targets: `domain/`, `openapi/`, `typescript/`,
    /// `python/`, `mcp/`.
    pub fn write_all(&self, base_dir: &Path) -> Result<Vec<PathBuf>> {
        Ok(vec![
            self.write_json_schema(&base_dir.join("domain"))?,
            self.write_openapi(&base_dir.join("openapi"))?,
            self.write_typescript(&base_dir.join("typescript"))?,
            self.write_pyi(&base_dir.join("python"))?,
            self.write_mcp(&base_dir.join("mcp"))?,
        ])
    }
}

/// Writes pretty JSON with a trailing newline, so the file is diff-friendly.
pub fn write_json_pretty(path: &Path, value: &Value) -> Result<()> {
    let mut text = serde_json::to_string_pretty(value).context("serializing JSON")?;
    text.push('\n');
    std::fs::write(path, text).with_context(|| format!("writing {}", path.display()))
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn the_version_axes_are_owned_here() {
        // plan.md 4.1: both constants have exactly one owner; codegen
        // re-exports them rather than repeating the literal.
        assert_eq!(SCHEMA_VERSION, honba_messages::SCHEMA_VERSION);
        assert_eq!(API_VERSION, honba_messages::API_VERSION);
        assert!(!API_VERSION.is_empty());
    }

    #[test]
    fn the_bundle_declares_a_draft_that_supports_defs() {
        let schema = Codegen::new().json_schema();
        assert_eq!(
            schema["$schema"],
            json!("https://json-schema.org/draft/2020-12/schema")
        );
        assert!(schema["$defs"].is_object());
    }

    #[test]
    fn every_ref_in_the_bundle_is_resolvable() {
        // The defect this guards: the bundle declared `$defs` while the
        // generated types still used `#/definitions/`, so 23 nested references
        // pointed at a key that did not exist.
        let schema = Codegen::new().json_schema();
        let defs = schema["$defs"].as_object().expect("$defs object");
        let mut missing = Vec::new();
        check_refs(&schema["$defs"], "#/$defs/", defs, &mut missing);
        assert!(missing.is_empty(), "unresolvable $refs: {missing:?}");
    }

    fn check_refs(
        value: &Value,
        prefix: &str,
        defs: &serde_json::Map<String, Value>,
        missing: &mut Vec<String>,
    ) {
        match value {
            Value::Object(map) => {
                for (key, child) in map {
                    if key == "$ref" {
                        if let Some(reference) = child.as_str() {
                            let name = reference.strip_prefix(prefix).unwrap_or(reference);
                            if !defs.contains_key(name) {
                                missing.push(reference.to_string());
                            }
                        }
                    } else {
                        check_refs(child, prefix, defs, missing);
                    }
                }
            }
            Value::Array(items) => {
                for item in items {
                    check_refs(item, prefix, defs, missing);
                }
            }
            _ => {}
        }
    }

    #[test]
    fn the_openapi_document_has_paths() {
        // An OpenAPI document with no `paths` describes nothing; codegen used to
        // emit only info and components.
        let spec = Codegen::new().openapi();
        assert_eq!(spec["openapi"], json!("3.1.0"));
        let paths = spec["paths"].as_object().expect("paths object");
        assert!(!paths.is_empty());
        assert!(paths.contains_key("/health"));
        assert!(paths.contains_key("/instruments"));
    }

    #[test]
    fn the_openapi_document_has_no_dangling_refs() {
        let spec = Codegen::new().openapi();
        let components = &spec["components"]["schemas"];
        assert_eq!(
            spec["paths"],
            endpoints::openapi_paths(&Codegen::new().schemas().clone())
        );
        let _ = components;
        let mut missing = Vec::new();
        check_component_refs(&spec, &mut missing);
        assert!(missing.is_empty(), "unresolvable $refs: {missing:?}");
    }

    fn check_component_refs(value: &Value, missing: &mut Vec<String>) {
        let prefix = "#/components/schemas/";
        match value {
            Value::Object(map) => {
                for (key, child) in map {
                    if key == "$ref" {
                        if let Some(reference) = child.as_str() {
                            let name = reference.strip_prefix(prefix).unwrap_or(reference);
                            if !Codegen::new().schemas().get(name).is_some() {
                                missing.push(reference.to_string());
                            }
                        }
                    } else {
                        check_component_refs(child, missing);
                    }
                }
            }
            Value::Array(items) => {
                for item in items {
                    check_component_refs(item, missing);
                }
            }
            _ => {}
        }
    }

    #[test]
    fn the_bundle_carries_both_version_axes() {
        let schema = Codegen::new().json_schema();
        assert_eq!(schema["schema_version"], json!(SCHEMA_VERSION));
        assert_eq!(schema["api_version"], json!(API_VERSION));
    }

    #[test]
    fn the_core_version_is_reported() {
        assert!(!CORE_VERSION.is_empty());
    }
}
