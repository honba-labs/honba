//! Unit tests for `crate::typings`.

use crate::schemas::SchemaSet;
use crate::typings::*;
use serde_json::json;

#[test]
fn an_optional_field_is_wrapped_exactly_once() {
    // The old output was Optional[Optional[ErrorDetail | Any]], which is
    // meaningless even though it parses.
    let schema = json!({
        "type": "object",
        "properties": {
            "error": {"oneOf": [{"$ref": "#/$defs/ErrorDetail"}, {"type": "null"}]}
        }
    });
    let out = alias("Response", &schema);
    assert_eq!(
        out,
        "class Response:\n    error: ErrorDetail | None = None\n"
    );
}

#[test]
fn there_is_no_double_optional_anywhere_in_the_stub() {
    let mut set = SchemaSet::new();
    set.insert(
        "Wrapper",
        json!({
            "type": "object",
            "properties": {"detail": {"oneOf": [{"$ref": "#/$defs/ErrorDetail"}, {"type": "null"}]}}
        }),
    );
    set.insert("ErrorDetail", json!({"type": "object", "properties": {}}));
    let pyi = render_pyi(&set, 2, "1.0.0");
    assert!(
        !pyi.contains("Optional["),
        "stubs must not use Optional:\n{pyi}"
    );
}

#[test]
fn the_generic_envelope_keeps_its_parameter() {
    let out = alias("ResponseEnvelope", &json!({"type": "object"}));
    assert!(out.contains("Generic[T]"), "{out}");
    assert!(out.contains("data: T | None"), "{out}");
}

#[test]
fn an_enum_becomes_a_literal_alias() {
    let out = alias(
        "OrderSide",
        &json!({"oneOf": [{"enum": ["buy"]}, {"enum": ["sell"]}]}),
    );
    assert_eq!(out, "OrderSide = Literal[\"buy\", \"sell\"]\n");
}

#[test]
fn a_required_field_has_no_default_and_an_optional_one_does() {
    let schema = json!({
        "type": "object",
        "properties": {"name": {"type": "string"}, "qty": {"type": "number"}},
        "required": ["name"]
    });
    let out = class("Order", &schema);
    assert!(out.contains("    name: str\n"), "{out}");
    assert!(out.contains("    qty: float | None = None\n"), "{out}");
}

#[test]
fn an_array_renders_as_a_builtin_generic() {
    assert_eq!(
        py_type(&json!({"type": "array", "items": {"$ref": "#/$defs/Bar"}})),
        "list[Bar]"
    );
}

#[test]
fn scalars_map_to_scalars() {
    assert_eq!(py_type(&json!({"type": "string"})), "str");
    assert_eq!(py_type(&json!({"type": "integer"})), "int");
    assert_eq!(py_type(&json!({"type": "number"})), "float");
    assert_eq!(py_type(&json!({"type": "boolean"})), "bool");
}

#[test]
fn an_object_with_no_properties_still_yields_a_class() {
    let out = class("Empty", &json!({"type": "object", "properties": {}}));
    assert!(out.contains("pass"), "{out}");
}

#[test]
fn rendering_is_deterministic_and_declares_versions() {
    let mut set = SchemaSet::new();
    set.insert("Bar", json!({"type": "object", "properties": {}}));
    let a = render_pyi(&set, 3, "2.0.0");
    let b = render_pyi(&set, 3, "2.0.0");
    assert_eq!(a, b);
    assert!(a.contains("SCHEMA_VERSION: int = 3"));
    assert!(a.contains("API_VERSION: str = \"2.0.0\""));
    assert!(a.contains("DO NOT EDIT"));
}
