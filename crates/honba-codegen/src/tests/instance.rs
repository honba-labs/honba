//! Unit tests for `crate::instance`.

use crate::instance::validate_instance;
use serde_json::json;

fn root() -> serde_json::Value {
    json!({"$defs": {
        "Side": {"type": "string", "enum": ["buy", "sell"]},
        "Id": {"type": "object", "properties": {"s": {"type": "string"}}, "required": ["s"]},
        "Order": {
            "type": "object",
            "properties": {
                "side": {"$ref": "#/$defs/Side"},
                "id": {"$ref": "#/$defs/Id"},
                "px": {"type": ["number", "null"]},
                "n": {"type": "integer", "minimum": 0.0},
                "tags": {"type": "array", "items": {"type": "string"}, "maxItems": 2}
            },
            "required": ["side", "id"],
            "additionalProperties": false
        },
        "Odd": {"oneOf": [{"type": "string"}, {"type": "integer"}]},
        "Weird": {"pattern": "x"}
    }})
}

#[test]
fn a_conforming_instance_is_valid() {
    let v = json!({"side": "buy", "id": {"s": "a"}, "px": null, "n": 3, "tags": ["a"]});
    assert!(validate_instance(&root(), "Order", &v).is_empty());
}

#[test]
fn each_kind_of_violation_is_reported_with_a_path() {
    let r = root();
    let e = validate_instance(&r, "Order", &json!({"side": "hold", "id": {}}));
    assert!(e.iter().any(|m| m.starts_with("/side:")), "{e:?}");
    assert!(
        e.iter()
            .any(|m| m.contains("missing required property `s`")),
        "{e:?}"
    );
    let e = validate_instance(
        &r,
        "Order",
        &json!({"side": "buy", "id": {"s": "a"}, "extra": 1}),
    );
    assert!(
        e.iter().any(|m| m.contains("unknown property `extra`")),
        "{e:?}"
    );
    let e = validate_instance(
        &r,
        "Order",
        &json!({"side": "buy", "id": {"s": "a"}, "n": -1}),
    );
    assert!(e.iter().any(|m| m.contains("minimum")), "{e:?}");
    let e = validate_instance(
        &r,
        "Order",
        &json!({"side": "buy", "id": {"s": "a"}, "tags": [1, "b", "c"]}),
    );
    assert!(e.iter().any(|m| m.starts_with("/tags/0:")), "{e:?}");
    assert!(e.iter().any(|m| m.contains("maxItems")), "{e:?}");
}

#[test]
fn one_of_requires_exactly_one_match() {
    let r = root();
    assert!(validate_instance(&r, "Odd", &json!("a")).is_empty());
    assert!(!validate_instance(&r, "Odd", &json!(1.5)).is_empty());
}

#[test]
fn unknown_type_and_unsupported_keyword_fail_loudly() {
    let r = root();
    assert!(!validate_instance(&r, "Nope", &json!(1)).is_empty());
    let e = validate_instance(&r, "Weird", &json!("x"));
    assert!(
        e[0].contains("unsupported schema keyword `pattern`"),
        "{e:?}"
    );
}
