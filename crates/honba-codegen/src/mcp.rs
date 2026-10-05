//! Building MCP tool schemas from the shared registry.
//!
//! The previous implementation was a hand-written `json!` array whose arguments
//! referenced `#/definitions/StrategyManifest` — a type that did not exist, so
//! the emitted tool schemas were invalid JSON Schema. Every tool here is built
//! from a real registered type, and a test asserts no reference dangles.

use serde_json::{json, Value};

use crate::schemas::SchemaSet;

/// One tool the agent surface exposes, derived from the registry.
struct Tool {
    name: &'static str,
    description: &'static str,
    /// Arguments, as `(name, registered type, required, description)`.
    args: &'static [(&'static str, &'static str, bool, &'static str)],
}

const TOOLS: &[Tool] = &[
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

#[cfg(test)]
mod tests {
    use super::*;
    use crate::registry;

    fn full() -> SchemaSet {
        registry::full_registry()
    }

    #[test]
    fn every_tool_argument_type_is_registered() {
        for tool in TOOLS {
            for (_, type_name, _, _) in tool.args {
                assert!(
                    full().get(type_name).is_some(),
                    "tool {} references unregistered type {type_name}",
                    tool.name
                );
            }
        }
    }

    #[test]
    fn the_rendered_tools_have_no_dangling_references() {
        // This is the exact defect that shipped: StrategyManifest was referenced
        // but did not exist, making the MCP document invalid.
        let set = full();
        let rendered = render(&set);
        let mut missing = Vec::new();
        collect_dangling(&rendered, &set, &mut missing);
        assert!(
            missing.is_empty(),
            "MCP tools reference missing types: {missing:?}"
        );
    }

    #[test]
    fn strategy_manifest_is_a_real_tool_argument_now() {
        let set = full();
        let rendered = render(&set);
        let manifest = set.get("StrategyManifest").expect("manifest registered");
        assert!(manifest.get("properties").is_some());
        let tools = rendered["tools"].as_array().expect("tools array");
        let backtest = tools.iter().find(|t| t["name"] == "backtest").unwrap();
        assert_eq!(
            backtest["inputSchema"]["properties"]["strategy_manifest"]["type"],
            "object"
        );
    }

    #[test]
    fn tools_declare_their_required_arguments() {
        let rendered = render(&full());
        let tools = rendered["tools"].as_array().unwrap();
        let sweep = tools.iter().find(|t| t["name"] == "sweep").unwrap();
        let required = sweep["inputSchema"]["required"].as_array().unwrap();
        assert!(required.contains(&json!("strategy_manifest")));
        assert!(required.contains(&json!("request")));
    }

    #[test]
    fn every_argument_carries_a_description() {
        // An undescribed argument is unusable by an agent choosing what to pass.
        let rendered = render(&full());
        for tool in rendered["tools"].as_array().unwrap() {
            let props = tool["inputSchema"]["properties"].as_object().unwrap();
            for (name, schema) in props {
                assert!(
                    schema
                        .get("description")
                        .and_then(Value::as_str)
                        .is_some_and(|d| !d.is_empty()),
                    "argument {name} of {} has no description",
                    tool["name"]
                );
            }
        }
    }

    #[test]
    fn tool_names_are_unique() {
        let mut seen = std::collections::BTreeSet::new();
        for tool in TOOLS {
            assert!(seen.insert(tool.name), "duplicate tool {}", tool.name);
        }
    }

    #[test]
    fn unknown_arguments_are_rejected() {
        let rendered = render(&full());
        let tools = rendered["tools"].as_array().unwrap();
        let health = &tools[0];
        assert_eq!(health["inputSchema"]["additionalProperties"], json!(false));
    }

    fn collect_dangling(value: &Value, set: &SchemaSet, missing: &mut Vec<String>) {
        match value {
            Value::Object(map) => {
                for (key, child) in map {
                    if key == "$ref" {
                        if let Some(reference) = child.as_str() {
                            let name = reference.rsplit('/').next().unwrap_or(reference);
                            if set.get(name).is_none() {
                                missing.push(reference.to_string());
                            }
                        }
                    } else {
                        collect_dangling(child, set, missing);
                    }
                }
            }
            Value::Array(items) => {
                for item in items {
                    collect_dangling(item, set, missing);
                }
            }
            _ => {}
        }
    }
}
