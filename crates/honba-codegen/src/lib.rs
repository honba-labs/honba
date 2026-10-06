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
    published_names, request_type_names, API_TYPES, CONFIG_TYPES, MANIFEST_TYPES, SCREENER_TYPES,
    WIRE_ENUMS, WIRE_TYPES,
};
pub use schemas::{local_refs, unresolved_local_refs, SchemaSet};

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

/// One generated artifact: what it is called and where it is written.
///
/// This is the single list both CLIs (the Rust `honba schema export` and the
/// Python one, through `honba._honba.codegen_render`) iterate, so they cannot
/// disagree about file names or content.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub enum Artifact {
    /// The JSON Schema conformance bundle, `domain_schema.json`.
    JsonSchema,
    /// The OpenAPI 3.1 document, `openapi.json`.
    OpenApi,
    /// TypeScript declarations for `honba-frontend`, `domain.ts`.
    TypeScript,
    /// Python type stubs, `__init__.pyi`.
    Pyi,
    /// MCP tool schemas, `mcp_tools.json`.
    Mcp,
}

impl Artifact {
    /// Every artifact, in [`Codegen::write_all`] order.
    pub const ALL: &'static [Artifact] = &[
        Artifact::JsonSchema,
        Artifact::OpenApi,
        Artifact::TypeScript,
        Artifact::Pyi,
        Artifact::Mcp,
    ];

    /// The stable name used across the FFI boundary and on the command line.
    pub fn name(self) -> &'static str {
        match self {
            Artifact::JsonSchema => "json_schema",
            Artifact::OpenApi => "openapi",
            Artifact::TypeScript => "typescript",
            Artifact::Pyi => "pyi",
            Artifact::Mcp => "mcp",
        }
    }

    /// Parses [`Artifact::name`].
    pub fn from_name(name: &str) -> Option<Self> {
        Self::ALL.iter().copied().find(|a| a.name() == name)
    }

    /// The file the artifact is written to inside its output directory.
    pub fn file_name(self) -> &'static str {
        match self {
            Artifact::JsonSchema => "domain_schema.json",
            Artifact::OpenApi => "openapi.json",
            Artifact::TypeScript => "domain.ts",
            Artifact::Pyi => "__init__.pyi",
            Artifact::Mcp => "mcp_tools.json",
        }
    }
}

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

    /// The exact file content of `artifact`, as the writers write it.
    pub fn render(&self, artifact: Artifact) -> String {
        match artifact {
            Artifact::JsonSchema => to_pretty_json(&self.json_schema()),
            Artifact::OpenApi => to_pretty_json(&self.openapi()),
            Artifact::TypeScript => self.typescript(),
            Artifact::Pyi => self.pyi(),
            Artifact::Mcp => to_pretty_json(&self.mcp()),
        }
    }

    /// Writes `artifact` into `output_dir/<file_name>`.
    pub fn write(&self, artifact: Artifact, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let path = output_dir.join(artifact.file_name());
        std::fs::write(&path, self.render(artifact))
            .with_context(|| format!("writing {}", path.display()))?;
        Ok(path)
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
            "$comment": "Generated by honba-codegen from the Rust wire types. DO NOT EDIT: regenerate with `make schema`.",
            "title": "HonbaDomainBundle",
            "description": "Canonical Honba wire models, generated from the Rust source of truth",
            "schema_version": SCHEMA_VERSION,
            "api_version": API_VERSION,
            "type": "object",
            "properties": root_properties(),
            "$defs": self.set.to_defs("#/$defs/"),
        })
    }

    /// Writes [`Codegen::json_schema`] into `output_dir/domain_schema.json`.
    pub fn write_json_schema(&self, output_dir: &Path) -> Result<PathBuf> {
        let path = self.write(Artifact::JsonSchema, output_dir)?;
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
                "description": "Generated by honba-codegen from the Rust source of truth. DO NOT EDIT: regenerate with `make openapi`."
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
        let path = self.write(Artifact::OpenApi, output_dir)?;
        println!("Exported OpenAPI: {}", path.display());
        Ok(path)
    }

    /// The TypeScript declarations for `honba-frontend`.
    pub fn typescript(&self) -> String {
        typescript::render(&self.set, SCHEMA_VERSION, API_VERSION)
    }

    /// Writes [`Codegen::typescript`] into `output_dir/domain.ts`.
    pub fn write_typescript(&self, output_dir: &Path) -> Result<PathBuf> {
        let path = self.write(Artifact::TypeScript, output_dir)?;
        println!("Generated TypeScript definitions: {}", path.display());
        Ok(path)
    }

    /// The Python type stubs for the `honba` package.
    pub fn pyi(&self) -> String {
        typings::render_pyi(&self.set, SCHEMA_VERSION, API_VERSION)
    }

    /// Writes [`Codegen::pyi`] into `output_dir/__init__.pyi`.
    ///
    /// The committed target is `python/src/honba/wire/generated/__init__.pyi`
    /// (`make pyi`): generated and never edited, with the hand-written wire
    /// models (`honba.wire`) living beside it.
    pub fn write_pyi(&self, output_dir: &Path) -> Result<PathBuf> {
        let path = self.write(Artifact::Pyi, output_dir)?;
        println!("Generated Python stubs: {}", path.display());
        Ok(path)
    }

    /// The MCP tool schemas, built from the registry.
    pub fn mcp(&self) -> Value {
        mcp::render(&self.set)
    }

    /// Writes [`Codegen::mcp`] into `output_dir/mcp_tools.json`.
    pub fn write_mcp(&self, output_dir: &Path) -> Result<PathBuf> {
        let path = self.write(Artifact::Mcp, output_dir)?;
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

/// The bundle's root `properties`: each wire and screener type under its
/// snake_case name, so one document can carry any of them.
fn root_properties() -> Value {
    let mut out = serde_json::Map::new();
    for name in WIRE_TYPES.iter().chain(SCREENER_TYPES) {
        out.insert(snake_case(name), json!({"$ref": format!("#/$defs/{name}")}));
    }
    Value::Object(out)
}

/// `OrderIntent` -> `order_intent`.
fn snake_case(name: &str) -> String {
    let mut out = String::with_capacity(name.len() + 4);
    for (i, c) in name.chars().enumerate() {
        if c.is_ascii_uppercase() {
            if i > 0 {
                out.push('_');
            }
            out.push(c.to_ascii_lowercase());
        } else {
            out.push(c);
        }
    }
    out
}

/// Pretty JSON with a trailing newline, so the file is diff-friendly.
pub fn to_pretty_json(value: &Value) -> String {
    // Serializing a `Value` cannot fail: every key is already a string.
    let mut text = serde_json::to_string_pretty(value).unwrap_or_default();
    text.push('\n');
    text
}

/// Writes [`to_pretty_json`] of `value` to `path`.
pub fn write_json_pretty(path: &Path, value: &Value) -> Result<()> {
    std::fs::write(path, to_pretty_json(value))
        .with_context(|| format!("writing {}", path.display()))
}

#[cfg(test)]
mod tests;
