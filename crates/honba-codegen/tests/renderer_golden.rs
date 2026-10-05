//! Golden tests for the TypeScript and `.pyi` renderers.
//!
//! A fixed fixture covers each schema shape `schemars` emits for the wire
//! types: unit enums as `oneOf`-of-`enum`, `anyOf` enums, nullable type arrays,
//! nullable enums, `allOf` newtype wrappers, maps, arrays, tagged unions of
//! inline objects, a keyword-named field, and the generic envelope. The
//! expected output is checked in under `tests/golden/`; set
//! `HONBA_BLESS=1` to rewrite it after an intended rendering change.

use std::path::Path;

use honba_codegen::{typescript, typings, SchemaSet};
use serde_json::json;

fn fixture() -> SchemaSet {
    let mut set = SchemaSet::new();
    set.insert(
        "Side",
        json!({"oneOf": [
            {"description": "Buy.", "type": "string", "enum": ["buy"]},
            {"description": "Sell.", "type": "string", "enum": ["sell"]}
        ]}),
    );
    set.insert(
        "Venue",
        json!({"anyOf": [{"enum": ["NSE"]}, {"enum": ["BSE"]}]}),
    );
    set.insert("MaybeFlag", json!({"enum": ["on", null]}));
    set.insert("Count", json!({"type": "integer", "format": "uint64"}));
    set.insert("Wrapper", json!({"allOf": [{"$ref": "#/$defs/Side"}]}));
    set.insert(
        "Holder",
        json!({
            "type": "object",
            "required": ["side", "tags"],
            "properties": {
                "side": {"$ref": "#/$defs/Side"},
                "price": {"type": ["number", "null"]},
                "venue": {"anyOf": [{"$ref": "#/$defs/Venue"}, {"type": "null"}]},
                "tags": {"type": "array", "items": {"type": "string"}},
                "weights": {"type": "object", "additionalProperties": {"type": "number"}},
                "extra": {}
            }
        }),
    );
    set.insert(
        "Range",
        json!({
            "type": "object",
            "required": ["tf"],
            "properties": {"from": {"type": ["string", "null"]}, "tf": {"type": "string"}}
        }),
    );
    set.insert(
        "Tick",
        json!({"oneOf": [
            {"type": "object", "required": ["type"], "properties": {"type": {"enum": ["quote"]}, "bid": {"type": "number"}}},
            {"type": "object", "required": ["type"], "properties": {"type": {"enum": ["trade"]}, "px": {"type": "number"}}}
        ]}),
    );
    set.insert("ResponseEnvelope", json!({"type": "object"}));
    set
}

/// Masks the crate version so a release bump does not churn the goldens.
fn check_golden(name: &str, actual: &str) {
    let actual = actual.replace(
        &format!("CORE_VERSION: str = \"{}\"", honba_codegen::CORE_VERSION),
        "CORE_VERSION: str = \"<core>\"",
    );
    let actual = actual.as_str();
    let path = Path::new(env!("CARGO_MANIFEST_DIR"))
        .join("tests/golden")
        .join(name);
    if std::env::var_os("HONBA_BLESS").is_some() {
        std::fs::write(&path, actual).expect("bless golden");
    }
    let expected = std::fs::read_to_string(&path)
        .unwrap_or_else(|e| panic!("reading {}: {e}", path.display()));
    assert_eq!(actual, expected, "{name} drifted from its golden");
}

#[test]
fn typescript_matches_its_golden() {
    check_golden("fixture.ts", &typescript::render(&fixture(), 9, "9.9.9"));
}

#[test]
fn pyi_matches_its_golden() {
    check_golden("fixture.pyi", &typings::render_pyi(&fixture(), 9, "9.9.9"));
}
