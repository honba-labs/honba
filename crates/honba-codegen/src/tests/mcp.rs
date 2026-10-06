//! Unit tests for `crate::mcp`.

use crate::mcp::*;
use crate::registry;
use crate::schemas::SchemaSet;
use honba_messages::{ENDPOINTS, WRITE_PATHS};
use serde_json::{json, Value};

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

#[test]
fn every_tool_input_schema_is_self_contained() {
    // An MCP client receives each tool on its own, so its inputSchema must
    // resolve every `$ref` within itself. The old document referenced a
    // top-level `definitions` block (and mixed bare and `#/definitions/` refs)
    // that a client never sees.
    let rendered = render(&full());
    for tool in rendered["tools"].as_array().unwrap() {
        let schema = &tool["inputSchema"];
        let unresolved = crate::unresolved_local_refs(schema);
        assert!(
            unresolved.is_empty(),
            "tool {} has unresolvable refs: {unresolved:?}",
            tool["name"]
        );
    }
}

#[test]
fn tool_defs_carry_only_what_the_tool_references() {
    let set = full();
    let rendered = render(&set);
    let tools = rendered["tools"].as_array().unwrap();
    let instruments = tools
        .iter()
        .find(|t| t["name"] == "get_instruments")
        .unwrap();
    // InstrumentsQuery has only scalar fields, so it needs no $defs at all.
    let defs = instruments["inputSchema"]["$defs"].as_object();
    assert!(defs.map_or(true, |d| d.is_empty()), "{defs:?}");
}

#[test]
fn every_tool_maps_to_a_registered_endpoint() {
    for tool in TOOLS {
        let (method, path) = tool.endpoint;
        assert!(
            ENDPOINTS.contains(&(method, path)),
            "tool {} names unregistered endpoint {method} {path}",
            tool.name
        );
    }
}

#[test]
fn the_read_only_hint_comes_from_the_write_registry() {
    let rendered = render(&full());
    for (tool, out) in TOOLS.iter().zip(rendered["tools"].as_array().unwrap()) {
        let is_write = WRITE_PATHS.contains(&tool.endpoint);
        assert_eq!(
            out["annotations"]["readOnlyHint"],
            json!(!is_write),
            "tool {}",
            tool.name
        );
    }
}

#[test]
fn endpoint_path_parameters_become_required_string_arguments() {
    let rendered = render(&full());
    let tools = rendered["tools"].as_array().unwrap();
    let bars = tools.iter().find(|t| t["name"] == "get_bars").unwrap();
    assert_eq!(
        bars["inputSchema"]["properties"]["id"]["type"],
        json!("string")
    );
    let required = bars["inputSchema"]["required"].as_array().unwrap();
    assert!(required.contains(&json!("id")), "{required:?}");
}

#[test]
fn the_document_has_no_detached_definitions_block() {
    let rendered = render(&full());
    assert!(rendered.get("definitions").is_none());
}

#[test]
fn verify_strategy_drives_the_dedicated_read_only_endpoint() {
    let tool = TOOLS.iter().find(|t| t.name == "verify_strategy").unwrap();
    assert_eq!(tool.endpoint, ("POST", "/strategies/verify"));
    let rendered = render(&full());
    let tools = rendered["tools"].as_array().unwrap();
    let verify = tools
        .iter()
        .find(|t| t["name"] == "verify_strategy")
        .unwrap();
    assert_eq!(verify["annotations"]["readOnlyHint"], json!(true));
    assert_eq!(
        verify["inputSchema"]["required"],
        json!(["strategy_manifest"])
    );
}
