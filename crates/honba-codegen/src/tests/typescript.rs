//! Unit tests for `crate::typescript`.

use crate::schemas::SchemaSet;
use crate::typescript::*;
use serde_json::json;

#[test]
fn a_unit_enum_renders_as_a_literal_union_not_any() {
    // The regression this file exists for: schemars emits unit enums as
    // oneOf-of-single-enums, which the old renderer read as `any`.
    let schema = json!({
        "oneOf": [
            {"enum": ["buy"]},
            {"enum": ["sell"]}
        ]
    });
    assert_eq!(ts_type(&schema), "\"buy\" | \"sell\"");
}

#[test]
fn a_plain_enum_renders_as_a_literal_union() {
    let schema = json!({"type": "string", "enum": ["day", "gtc"]});
    assert_eq!(ts_type(&schema), "\"day\" | \"gtc\"");
}

#[test]
fn an_enum_declaration_uses_the_union() {
    let out = declaration("OrderSide", &json!({"oneOf": [{"enum": ["buy"]}]}));
    assert_eq!(out, "export type OrderSide = \"buy\";\n");
}

#[test]
fn an_all_of_composition_renders_rather_than_collapsing() {
    // Capabilities was `any` before because allOf was not handled.
    let schema = json!({
        "allOf": [{"$ref": "#/definitions/CapabilityManifest"}]
    });
    assert_eq!(ts_type(&schema), "CapabilityManifest");
}

#[test]
fn an_array_of_refs_renders_as_an_array_of_that_ref() {
    let schema = json!({"type": "array", "items": {"$ref": "#/$defs/Bar"}});
    assert_eq!(ts_type(&schema), "Bar[]");
}

#[test]
fn a_free_form_object_renders_as_a_record_not_any() {
    let schema = json!({"type": "object"});
    assert_eq!(ts_type(&schema), "Record<string, unknown>");
}

#[test]
fn a_map_of_a_known_type_renders_the_value_type() {
    let schema = json!({"type": "object", "additionalProperties": {"$ref": "#/$defs/Bar"}});
    assert_eq!(ts_type(&schema), "Record<string, Bar>");
}

#[test]
fn a_ref_becomes_a_bare_type_name() {
    assert_eq!(
        ts_type(&json!({"$ref": "#/components/schemas/Order"})),
        "Order"
    );
}

#[test]
fn an_interface_marks_optional_fields() {
    let schema = json!({
        "type": "object",
        "properties": {
            "name": {"type": "string"},
            "qty": {"type": "number"}
        },
        "required": ["name"]
    });
    let out = declaration("OrderRequest", &schema);
    assert!(out.contains("  name: string;"), "{out}");
    assert!(out.contains("  qty?: number;"), "{out}");
}

#[test]
fn a_property_that_is_not_an_identifier_is_quoted() {
    let schema = json!({
        "type": "object",
        "properties": {"user-agent": {"type": "string"}}
    });
    let out = declaration("Headers", &schema);
    assert!(out.contains("\"user-agent\"?: string;"), "{out}");
}

#[test]
fn scalars_map_to_scalars() {
    assert_eq!(ts_type(&json!({"type": "string"})), "string");
    assert_eq!(ts_type(&json!({"type": "integer"})), "number");
    assert_eq!(ts_type(&json!({"type": "number"})), "number");
    assert_eq!(ts_type(&json!({"type": "boolean"})), "boolean");
}

#[test]
fn the_rendered_module_declares_both_versions() {
    let mut set = SchemaSet::new();
    set.insert("Bar", json!({"type": "object", "properties": {}}));
    let ts = render(&set, 4, "1.0.0");
    assert!(ts.contains("export const SCHEMA_VERSION: number = 4;"));
    assert!(ts.contains("export const API_VERSION: string = \"1.0.0\";"));
    assert!(ts.contains("DO NOT EDIT"));
}

#[test]
fn rendering_is_deterministic() {
    let mut set = SchemaSet::new();
    set.insert("Zebra", json!({"type": "string"}));
    set.insert("Apple", json!({"type": "number"}));
    let first = render(&set, 2, "1.0.0");
    let second = render(&set, 2, "1.0.0");
    assert_eq!(first, second);
    assert!(first.find("Apple").unwrap() < first.find("Zebra").unwrap());
}

#[test]
fn a_nullable_type_array_renders_as_a_union_not_unknown() {
    // schemars emits `Option<String>` as {"type": ["string", "null"]}; the old
    // renderer only read a string `type` and fell through to `unknown`.
    assert_eq!(
        ts_type(&json!({"type": ["string", "null"]})),
        "string | null"
    );
    assert_eq!(
        ts_type(&json!({"type": ["integer", "null"], "format": "uint64"})),
        "number | null"
    );
}

#[test]
fn a_nullable_enum_keeps_its_null_member() {
    // Non-string enum values used to be dropped silently.
    assert_eq!(ts_type(&json!({"enum": ["a", null]})), "\"a\" | null");
    assert_eq!(ts_type(&json!({"enum": [1, 2]})), "1 | 2");
}

#[test]
fn an_any_of_of_enums_collapses_to_one_union() {
    let schema = json!({"anyOf": [{"enum": ["a"]}, {"enum": ["b", "c"]}]});
    assert_eq!(ts_type(&schema), "\"a\" | \"b\" | \"c\"");
}

#[test]
fn an_all_of_of_enums_is_not_rendered_as_a_union() {
    // allOf is an intersection; a single member is that member.
    let schema = json!({"allOf": [{"$ref": "#/$defs/Side"}]});
    assert_eq!(ts_type(&schema), "Side");
}

#[test]
fn a_const_renders_as_a_literal() {
    assert_eq!(ts_type(&json!({"const": "quote"})), "\"quote\"");
}

#[test]
fn a_string_literal_with_quotes_is_escaped() {
    assert_eq!(ts_type(&json!({"enum": ["a\"b"]})), "\"a\\\"b\"");
}

#[test]
fn duplicate_union_members_are_rendered_once() {
    let schema = json!({"anyOf": [{"type": "string"}, {"type": "string"}, {"type": "null"}]});
    assert_eq!(ts_type(&schema), "string | null");
}

#[test]
fn the_module_ends_with_exactly_one_newline() {
    let mut set = SchemaSet::new();
    set.insert("Bar", json!({"type": "string"}));
    let ts = render(&set, 3, "1.0.0");
    assert!(
        ts.ends_with("export type Bar = string;\n") && !ts.ends_with("\n\n"),
        "{ts:?}"
    );
}

#[test]
fn the_envelope_is_generic_over_its_data() {
    let schema = json!({"type": "object", "required": ["api_version"], "properties": {
        "api_version": {"type": "string"},
        "data": {"type": "null"},
        "error": {"anyOf": [{"$ref": "#/$defs/ErrorDetail"}, {"type": "null"}]}
    }});
    assert_eq!(
        declaration("ResponseEnvelope", &schema),
        "export interface ResponseEnvelope<T = unknown> {\n  api_version: string;\n  data?: T | null;\n  error?: ErrorDetail | null;\n}\n"
    );
}
