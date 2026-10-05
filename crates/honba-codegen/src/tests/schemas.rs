//! Unit tests for `crate::schemas`.

use crate::schemas::*;
use serde_json::json;

#[test]
fn refs_are_normalized_to_bare_names() {
    let mut set = SchemaSet::new();
    set.insert("Bar", json!({"type": "object"}));
    set.insert(
        "Holder",
        json!({"properties": {"bar": {"$ref": "#/definitions/Bar"}}}),
    );
    let defs = set.to_defs("#/$defs/");
    assert_eq!(
        defs["Holder"]["properties"]["bar"]["$ref"],
        json!("#/$defs/Bar")
    );
}

#[test]
fn a_dangling_reference_is_detected() {
    // This is the defect that shipped StrategyManifest-less MCP tools.
    let mut set = SchemaSet::new();
    set.insert("Tool", json!({"$ref": "#/definitions/StrategyManifest"}));
    assert_eq!(
        set.dangling_references(),
        vec!["StrategyManifest".to_string()]
    );
}

#[test]
fn a_resolved_reference_is_not_dangling() {
    let mut set = SchemaSet::new();
    set.insert("StrategyManifest", json!({"type": "object"}));
    set.insert("Tool", json!({"$ref": "#/definitions/StrategyManifest"}));
    assert!(set.dangling_references().is_empty());
}

#[test]
fn nested_references_are_collected_at_any_depth() {
    let mut set = SchemaSet::new();
    set.insert(
        "Outer",
        json!({"items": [{"properties": {"x": {"$ref": "#/$defs/Inner"}}}]}),
    );
    assert!(set.referenced_names().contains("Inner"));
}

#[test]
fn components_render_component_pointer_refs() {
    // A bare `{"$ref": "Bar"}` inside `components.schemas` is a relative URI
    // reference, not a component reference; OpenAPI tooling cannot resolve it.
    let mut set = SchemaSet::new();
    set.insert("Bar", json!({"type": "object"}));
    set.insert("Holder", json!({"$ref": "#/definitions/Bar"}));
    let comps = set.to_components();
    assert_eq!(comps["Holder"]["$ref"], json!("#/components/schemas/Bar"));
}

#[test]
fn the_canonical_registry_has_no_dangling_references() {
    let set = canonical_schemas();
    assert!(!set.is_empty(), "registry is empty");
    assert_eq!(
        set.dangling_references(),
        Vec::<String>::new(),
        "every $ref must resolve within the registry"
    );
}
