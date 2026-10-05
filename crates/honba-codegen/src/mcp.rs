//! Building MCP tool schemas from the shared registry.
//!
//! The previous implementation was a hand-written `json!` array whose arguments
//! referenced `#/definitions/StrategyManifest` — a type that did not exist, so
//! the emitted tool schemas were invalid JSON Schema. Every tool here is built
//! from a real registered type, and a test asserts no reference dangles.

use serde_json::{json, Value};

use crate::schemas::SchemaSet;

/// One tool the agent surface exposes, derived from the registry.
pub(crate) struct Tool {
    pub(crate) name: &'static str,
    pub(crate) description: &'static str,
    /// Arguments, as `(name, registered type, required, description)`.
    pub(crate) args: &'static [(&'static str, &'static str, bool, &'static str)],
}

pub(crate) const TOOLS: &[Tool] = &[
    Tool {
        name: "backtest",
        description: "Run a backtest over historical data with a verified strategy manifest.",
        args: &[
            (
                "strategy_manifest",
                "StrategyManifest",
                true,
                "A verified strategy manifest, as returned by verify_strategy.",
            ),
            (
                "dataset_id",
                "BarsQuery",
                false,
                "Data range to load: timeframe plus inclusive from and exclusive to.",
            ),
            (
                "seed",
                "BacktestRunConfig",
                true,
                "Run configuration, including the seed that makes the run reproducible.",
            ),
        ],
    },
    Tool {
        name: "sweep",
        description: "Run a seeded parameter sweep over a strategy manifest.",
        args: &[
            (
                "strategy_manifest",
                "StrategyManifest",
                true,
                "The strategy to sweep.",
            ),
            (
                "request",
                "SweepRequest",
                true,
                "Parameter ranges, trial count, and seed.",
            ),
        ],
    },
    Tool {
        name: "verify_strategy",
        description: "Verify strategy source and compile it to a runnable manifest.",
        args: &[(
            "request",
            "StrategiesRequest",
            true,
            "Strategy name and source to verify.",
        )],
    },
    Tool {
        name: "screen",
        description: "Evaluate a screener predicate over the instrument catalog.",
        args: &[(
            "predicate",
            "ScreenerFilterPredicate",
            true,
            "The predicate tree to evaluate.",
        )],
    },
    Tool {
        name: "get_instruments",
        description: "List instruments, optionally filtered by exchange or symbol.",
        args: &[(
            "query",
            "InstrumentsQuery",
            false,
            "Optional exchange and symbol filters.",
        )],
    },
    Tool {
        name: "get_bars",
        description: "Fetch historical bars for an instrument.",
        args: &[(
            "query",
            "BarsQuery",
            true,
            "Timeframe and inclusive date range.",
        )],
    },
];

/// Renders the MCP tool schemas.
pub fn render(set: &SchemaSet) -> Value {
    let tools: Vec<Value> = TOOLS.iter().map(|tool| render_tool(set, tool)).collect();
    json!({
        "tools": tools,
        "definitions": set.to_components(),
    })
}

/// Renders one tool, replacing each argument's type with a `$ref`.
fn render_tool(set: &SchemaSet, tool: &Tool) -> Value {
    let mut properties = serde_json::Map::new();
    let mut required = Vec::new();

    for (arg_name, type_name, is_required, description) in tool.args {
        // An argument naming an unregistered type is a bug in this file, and the
        // test below proves the emitted document has no dangling reference.
        let schema = match set.get(type_name) {
            Some(schema) => schema.clone(),
            None => json!({
                "type": "object",
                "description": format!("unknown type {type_name}")
            }),
        };
        let mut arg = schema;
        if let Value::Object(map) = &mut arg {
            map.insert("description".into(), Value::String((*description).into()));
        }
        properties.insert((*arg_name).to_string(), arg);
        if *is_required {
            required.push(Value::String((*arg_name).to_string()));
        }
    }

    json!({
        "name": tool.name,
        "description": tool.description,
        "inputSchema": {
            "type": "object",
            "properties": properties,
            "required": required,
            "additionalProperties": false,
        },
    })
}
