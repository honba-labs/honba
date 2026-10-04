//! honba-codegen: Code generation from Rust wire types.
//!
//! Single source of truth: Rust types in `honba-messages`, `honba-entities`,
//! `honba-strategy` with `schemars` derives. Generates:
//! - JSON Schema (for validation, golden vectors)
//! - OpenAPI 3.1 (for REST surface)
//! - TypeScript (for frontend)
//! - Python .pyi stubs (for honba._honba)
//! - MCP tool schemas (for agent surfaces)

#![deny(missing_docs)]

use std::collections::BTreeMap;
use std::path::{Path, PathBuf};

use anyhow::{Context, Result};
use schemars::{schema::RootSchema, JsonSchema};
use serde_json::{json, Value};

/// The canonical wire types to include in all generated outputs.
pub const WIRE_TYPES: &[&str] = &[
    "honba_messages::InstrumentId",
    "honba_messages::Bar",
    "honba_messages::Order",
    "honba_strategy::OrderIntent",
    "honba_entities::Trade",
    "honba_entities::Position",
    "honba_messages::Event",
    "honba_messages::Message",
    "honba_entities::ScreenerFilterPredicate",
];

/// The canonical wire enums.
pub const WIRE_ENUMS: &[&str] = &[
    "honba_messages::OrderSide",
    "honba_messages::OrderType",
    "honba_messages::OrderStatus",
    "honba_messages::TimeInForce",
    "honba_messages::BarAggregation",
    "honba_messages::PriceType",
    "honba_messages::AggressorSide",
    "honba_entities::PositionSide",
    "honba_entities::Currency",
];

/// SCHEMA_VERSION from honba-messages.
pub const SCHEMA_VERSION: u32 = honba_messages::SCHEMA_VERSION;

/// API version for OpenAPI and REST endpoints.
pub const API_VERSION: &str = "1.0.0";

/// The core codegen context, holding all generated schemas.
#[derive(Default)]
pub struct Codegen {
    schemas: BTreeMap<String, Value>,
}


impl Codegen {
    /// Create a new codegen context and register all wire types.
    pub fn new() -> Self {
        let mut this = Self::default();
        this.register_all();
        this
    }

    fn register_all(&mut self) {
        self.register_type::<honba_messages::InstrumentId>("InstrumentId");
        self.register_type::<honba_messages::Bar>("Bar");
        self.register_type::<honba_messages::Order>("Order");
        self.register_type::<honba_messages::Event>("Event");
        self.register_type::<honba_messages::Message>("Message");
        self.register_type::<honba_entities::Trade>("Trade");
        self.register_type::<honba_entities::Position>("Position");
        self.register_type::<honba_entities::ScreenerFilterPredicate>("ScreenerFilterPredicate");
        self.register_type::<honba_strategy::OrderIntent>("OrderIntent");
        self.register_type::<honba_messages::OrderSide>("OrderSide");
        self.register_type::<honba_messages::OrderType>("OrderType");
        self.register_type::<honba_messages::OrderStatus>("OrderStatus");
        self.register_type::<honba_messages::TimeInForce>("TimeInForce");
        self.register_type::<honba_messages::BarAggregation>("BarAggregation");
        self.register_type::<honba_messages::PriceType>("PriceType");
        self.register_type::<honba_messages::AggressorSide>("AggressorSide");
        self.register_type::<honba_entities::PositionSide>("PositionSide");
        self.register_type::<honba_entities::Currency>("Currency");
    }

    fn register_type<T: JsonSchema>(&mut self, name: &str) {
        let root: RootSchema =
            schemars::gen::SchemaGenerator::default().into_root_schema_for::<T>();
        let root_value = serde_json::to_value(&root).unwrap_or(json!({}));
        if let Some(defs) = root_value.get("definitions").and_then(|d| d.as_object()) {
            for (k, v) in defs {
                self.schemas.insert(k.clone(), v.clone());
            }
        }
        // Also add the root if it's a named type
        if let Some(title) = root.schema.metadata.as_ref().and_then(|m| m.title.as_ref()) {
            if title == name || title.contains(name) {
                let mut schema_value = serde_json::to_value(&root.schema).unwrap_or(json!({}));
                if let Value::Object(ref mut map) = schema_value {
                    map.remove("title");
                }
                self.schemas.insert(name.to_string(), schema_value);
            }
        }
    }

    /// Get the merged JSON Schema as a serde_json::Value.
    pub fn json_schema(&self) -> Value {
        json!({
            "$schema": "http://json-schema.org/draft-07/schema#",
            "title": "HonbaDomainEnvelope",
            "description": "Honba canonical wire models generated from Rust single source of truth contracts",
            "type": "object",
            "properties": {
                "message": {"$ref": "#/$defs/Message"},
                "event": {"$ref": "#/$defs/Event"},
                "order": {"$ref": "#/$defs/Order"},
                "order_intent": {"$ref": "#/$defs/OrderIntent"},
                "trade": {"$ref": "#/$defs/Trade"},
                "position": {"$ref": "#/$defs/Position"},
                "bar": {"$ref": "#/$defs/Bar"},
                "instrument_id": {"$ref": "#/$defs/InstrumentId"},
                "screener_filter_predicate": {"$ref": "#/$defs/ScreenerFilterPredicate"},
            },
            "$defs": self.schemas,
        })
    }

    /// Write JSON Schema to a file.
    pub fn write_json_schema(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let schema_file = output_dir.join("domain_schema.json");
        let schema = self.json_schema();
        std::fs::write(
            &schema_file,
            serde_json::to_string_pretty(&schema)?.as_bytes(),
        )
        .context("writing JSON schema file")?;
        println!(
            "Exported JSON Schema: {} ({} definitions)",
            schema_file.display(),
            self.schemas.len()
        );
        Ok(schema_file)
    }

    /// Generate OpenAPI 3.1 spec.
    pub fn openapi(&self) -> Value {
        json!({
            "openapi": "3.1.0",
            "info": {
                "title": "Honba API",
                "version": API_VERSION,
                "description": "Honba trading platform API - generated from Rust wire types"
            },
            "servers": [{"url": "/api/v1"}],
            "components": {
                "schemas": self.schemas
            }
        })
    }

    /// Write OpenAPI spec to a file.
    pub fn write_openapi(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let file = output_dir.join("openapi.json");
        let spec = self.openapi();
        std::fs::write(&file, serde_json::to_string_pretty(&spec)?.as_bytes())
            .context("writing OpenAPI file")?;
        println!("Exported OpenAPI: {}", file.display());
        Ok(file)
    }

    /// Generate TypeScript definitions from JSON Schema values.
    pub fn typescript(&self) -> String {
        let mut out = String::new();
        out.push_str("// This file was automatically generated by honba-codegen.\n");
        out.push_str("// DO NOT MODIFY IT BY HAND. Instead, modify the source Rust types,\n");
        out.push_str("// and run honba-codegen to regenerate this file.\n\n");
        out.push_str(&format!(
            "export const SCHEMA_VERSION: number = {};\n\n",
            SCHEMA_VERSION
        ));
        out.push_str(&format!(
            "export const API_VERSION: string = \"{}\";\n\n",
            API_VERSION
        ));

        let mut names: Vec<_> = self.schemas.keys().cloned().collect();
        names.sort();
        for name in names {
            if let Some(schema) = self.schemas.get(&name) {
                out.push_str(&json_schema_to_typescript(&name, schema));
                out.push('\n');
            }
        }
        out
    }

    /// Write TypeScript definitions to a file.
    pub fn write_typescript(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let file = output_dir.join("domain.ts");
        std::fs::write(&file, self.typescript()).context("writing TypeScript file")?;
        println!("Generated TypeScript definitions: {}", file.display());
        Ok(file)
    }

    /// Generate Python .pyi stubs from JSON Schema values.
    pub fn pyi(&self) -> String {
        let mut out = String::new();
        out.push_str("# This file was automatically generated by honba-codegen.\n");
        out.push_str("# DO NOT MODIFY IT BY HAND. Instead, modify the source Rust types,\n");
        out.push_str("# and run honba-codegen to regenerate this file.\n\n");
        out.push_str("from __future__ import annotations\n\n");
        out.push_str("from typing import Optional, List, Dict, Any, Literal\n\n");
        out.push_str(&format!("SCHEMA_VERSION: int = {}\n\n", SCHEMA_VERSION));
        out.push_str(&format!("API_VERSION: str = \"{}\"\n\n", API_VERSION));

        let mut names: Vec<_> = self.schemas.keys().cloned().collect();
        names.sort();
        for name in names {
            if let Some(schema) = self.schemas.get(&name) {
                out.push_str(&json_schema_to_pyi(&name, schema));
                out.push_str("\n\n");
            }
        }
        out
    }

    /// Write Python .pyi stubs to a file.
    pub fn write_pyi(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let file = output_dir.join("__init__.pyi");
        std::fs::write(&file, self.pyi()).context("writing .pyi file")?;
        println!("Generated Python stubs: {}", file.display());
        Ok(file)
    }

    /// Generate MCP tool schemas.
    pub fn mcp(&self) -> Value {
        let tools = json!([
            {
                "name": "backtest",
                "description": "Run a backtest with a strategy over historical data",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "strategy_manifest": {"$ref": "#/definitions/StrategyManifest"},
                        "dataset_id": {"type": "string"},
                        "cost_model_version": {"type": "string", "default": "latest"}
                    },
                    "required": ["strategy_manifest", "dataset_id"]
                }
            },
            {
                "name": "sweep",
                "description": "Run a parameter sweep over a strategy",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "strategy_manifest": {"$ref": "#/definitions/StrategyManifest"},
                        "parameter_space": {"type": "object"},
                        "trials": {"type": "integer", "minimum": 1, "default": 100},
                        "seed": {"type": "integer"},
                        "fitness": {"type": "string", "enum": ["sharpe", "calmar", "sortino", "omega"], "default": "sharpe"}
                    },
                    "required": ["strategy_manifest", "parameter_space"]
                }
            },
            {
                "name": "verify_strategy",
                "description": "Verify a strategy against the contract (look-ahead, AST sandbox)",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "strategy_source": {"type": "string"},
                        "check_lookahead": {"type": "boolean", "default": true}
                    },
                    "required": ["strategy_source"]
                }
            },
            {
                "name": "screen",
                "description": "Run a screener query over the instrument catalog",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "predicate": {"$ref": "#/definitions/ScreenerFilterPredicate"},
                        "universe": {"type": "string", "default": "nifty50"}
                    },
                    "required": ["predicate"]
                }
            },
            {
                "name": "get_instrument",
                "description": "Look up an instrument by symbol and exchange",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "exchange": {"type": "string", "default": "NSE"},
                        "as_of": {"type": "string", "format": "date-time"}
                    },
                    "required": ["symbol"]
                }
            },
            {
                "name": "get_bars",
                "description": "Get historical bars for an instrument",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "symbol": {"type": "string"},
                        "exchange": {"type": "string", "default": "NSE"},
                        "timeframe": {"type": "string", "default": "1D"},
                        "start": {"type": "string", "format": "date-time"},
                        "end": {"type": "string", "format": "date-time"}
                    },
                    "required": ["symbol", "start", "end"]
                }
            }
        ]);

        json!({
            "tools": tools,
            "definitions": self.schemas
        })
    }

    /// Write MCP tool schemas to a file.
    pub fn write_mcp(&self, output_dir: &Path) -> Result<PathBuf> {
        std::fs::create_dir_all(output_dir).context("creating output directory")?;
        let file = output_dir.join("mcp_tools.json");
        std::fs::write(&file, serde_json::to_string_pretty(&self.mcp())?.as_bytes())
            .context("writing MCP file")?;
        println!("Generated MCP tool schemas: {}", file.display());
        Ok(file)
    }

    /// Write all artifacts.
    pub fn write_all(&self, base_dir: &Path) -> Result<()> {
        self.write_json_schema(&base_dir.join("domain"))?;
        self.write_openapi(&base_dir.join("openapi"))?;
        self.write_typescript(&base_dir.join("typescript"))?;
        self.write_pyi(&base_dir.join("python"))?;
        self.write_mcp(&base_dir.join("mcp"))?;
        Ok(())
    }
}

fn json_type_to_ts(value: &Value) -> String {
    if let Some(ref_str) = value.get("$ref").and_then(|v| v.as_str()) {
        return ref_str.split('/').next_back().unwrap_or(ref_str).to_string();
    }
    match value.get("type").and_then(|v| v.as_str()) {
        Some("string") => {
            if let Some(enum_vals) = value.get("enum").and_then(|v| v.as_array()) {
                let literals: Vec<String> = enum_vals
                    .iter()
                    .filter_map(|v| v.as_str().map(|s| format!("\"{}\"", s)))
                    .collect();
                if !literals.is_empty() {
                    return literals.join(" | ");
                }
            }
            "string".to_string()
        }
        Some("integer") => "number".to_string(),
        Some("number") => "number".to_string(),
        Some("boolean") => "boolean".to_string(),
        Some("array") => {
            let item = value
                .get("items")
                .map(json_type_to_ts)
                .unwrap_or_else(|| "any".to_string());
            format!("{}[]", item)
        }
        Some("object") => "any".to_string(),
        _ => {
            if value.get("anyOf").is_some() || value.get("oneOf").is_some() {
                let variants = value.get("anyOf").or_else(|| value.get("oneOf"));
                if let Some(arr) = variants.and_then(|v| v.as_array()) {
                    let types: Vec<String> = arr.iter().map(json_type_to_ts).collect();
                    return types.join(" | ");
                }
            }
            if value.get("enum").is_some() {
                return "string".to_string();
            }
            "any".to_string()
        }
    }
}

fn json_schema_to_typescript(name: &str, schema: &Value) -> String {
    if let Some(ref_str) = schema.get("$ref").and_then(|v| v.as_str()) {
        let ref_name = ref_str.split('/').next_back().unwrap_or(ref_str);
        return format!("export type {} = {};\n", name, ref_name);
    }
    if let Some(enum_vals) = schema.get("enum").and_then(|v| v.as_array()) {
        let literals: Vec<String> = enum_vals
            .iter()
            .filter_map(|v| v.as_str().map(|s| format!("\"{}\"", s)))
            .collect();
        if !literals.is_empty() {
            return format!("export type {} = {};\n", name, literals.join(" | "));
        }
    }
    if schema.get("properties").is_some() {
        let mut out = format!("export interface {} {{\n", name);
        let props = schema.get("properties").and_then(|v| v.as_object());
        let required: Vec<String> = schema
            .get("required")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str())
                    .map(|s| s.to_string())
                    .collect()
            })
            .unwrap_or_default();
        if let Some(props) = props {
            for (prop_name, prop_schema) in props {
                let ts_type = json_type_to_ts(prop_schema);
                let is_required = required.contains(prop_name);
                let optional = if is_required { "" } else { "?" };
                out.push_str(&format!("  {}{}: {};\n", prop_name, optional, ts_type));
            }
        }
        out.push_str("}\n");
        return out;
    }
    if let Some(_type_str) = schema.get("type").and_then(|v| v.as_str()) {
        let ts_type = json_type_to_ts(schema);
        return format!("export type {} = {};\n", name, ts_type);
    }
    format!("export type {} = any;\n", name)
}

fn json_type_to_py(value: &Value) -> String {
    if let Some(ref_str) = value.get("$ref").and_then(|v| v.as_str()) {
        return ref_str.split('/').next_back().unwrap_or(ref_str).to_string();
    }
    match value.get("type").and_then(|v| v.as_str()) {
        Some("string") => "str".to_string(),
        Some("integer") => "int".to_string(),
        Some("number") => "float".to_string(),
        Some("boolean") => "bool".to_string(),
        Some("array") => {
            let item = value
                .get("items")
                .map(json_type_to_py)
                .unwrap_or_else(|| "Any".to_string());
            format!("List[{}]", item)
        }
        Some("object") => "Any".to_string(),
        _ => {
            if value.get("anyOf").is_some() || value.get("oneOf").is_some() {
                let variants = value.get("anyOf").or_else(|| value.get("oneOf"));
                if let Some(arr) = variants.and_then(|v| v.as_array()) {
                    let types: Vec<String> = arr.iter().map(json_type_to_py).collect();
                    return format!("Optional[{}]", types.join(" | "));
                }
            }
            if value.get("enum").is_some() {
                return "str".to_string();
            }
            "Any".to_string()
        }
    }
}

fn json_schema_to_pyi(name: &str, schema: &Value) -> String {
    if let Some(ref_str) = schema.get("$ref").and_then(|v| v.as_str()) {
        let ref_name = ref_str.split('/').next_back().unwrap_or(ref_str);
        return format!("{} = {}\n", name, ref_name);
    }
    if let Some(enum_vals) = schema.get("enum").and_then(|v| v.as_array()) {
        let literals: Vec<String> = enum_vals
            .iter()
            .filter_map(|v| v.as_str().map(|s| format!("\"{}\"", s)))
            .collect();
        if !literals.is_empty() {
            return format!("{} = Literal[{}]\n", name, literals.join(", "));
        }
    }
    if schema.get("properties").is_some() {
        let mut out = format!("class {}:\n", name);
        let props = schema.get("properties").and_then(|v| v.as_object());
        let required: Vec<String> = schema
            .get("required")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .filter_map(|v| v.as_str())
                    .map(|s| s.to_string())
                    .collect()
            })
            .unwrap_or_default();
        if let Some(props) = props {
            for (prop_name, prop_schema) in props {
                let py_type = json_type_to_py(prop_schema);
                let is_required = required.contains(prop_name);
                let optional = if !is_required { "Optional[" } else { "" };
                let close = if !is_required { "]" } else { "" };
                let default = if !is_required { " = None" } else { "" };
                out.push_str(&format!(
                    "    {}: {}{}{}{}\n",
                    prop_name, optional, py_type, close, default
                ));
            }
        }
        return out;
    }
    if schema.get("type").is_some() {
        let py_type = json_type_to_py(schema);
        return format!("{} = {}\n", name, py_type);
    }
    format!("{} = Any\n", name)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn codegen_runs() {
        let codegen = Codegen::new();
        let schema = codegen.json_schema();
        assert!(schema["$defs"].is_object());
        assert!(!schema["$defs"].as_object().unwrap().is_empty());
        let defs = schema["$defs"].as_object().unwrap();
        for name in WIRE_TYPES.iter().map(|s| s.split("::").last().unwrap()) {
            assert!(defs.contains_key(name), "missing type: {}", name);
        }
        for name in WIRE_ENUMS.iter().map(|s| s.split("::").last().unwrap()) {
            assert!(defs.contains_key(name), "missing enum: {}", name);
        }
    }

    #[test]
    fn openapi_generates() {
        let codegen = Codegen::new();
        let spec = codegen.openapi();
        assert_eq!(spec["openapi"], "3.1.0");
        assert_eq!(spec["info"]["version"], API_VERSION);
        assert!(spec["components"]["schemas"].is_object());
    }

    #[test]
    fn typescript_generates() {
        let codegen = Codegen::new();
        let ts = codegen.typescript();
        assert!(ts.contains("export type") || ts.contains("export interface"));
        assert!(ts.contains("InstrumentId"));
        assert!(ts.contains("Message"));
    }

    #[test]
    fn pyi_generates() {
        let codegen = Codegen::new();
        let pyi = codegen.pyi();
        assert!(pyi.contains("class InstrumentId") || pyi.contains("InstrumentId ="));
        assert!(pyi.contains("Message"));
    }
}
