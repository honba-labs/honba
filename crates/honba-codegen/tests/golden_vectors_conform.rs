//! Golden wire vectors validate against the generated `domain_schema.json`.
//!
//! ADR 0014: the schema must describe the serde form faithfully. The committed
//! vectors are the serde form, so each wire-shaped instance must validate.
//! Vectors that are not wire instances (engine scenario scripts, indicator
//! series, rejection scenarios, `invalid*` cases that Rust rejects on
//! invariants the schema does not express) are deliberately not checked.

use std::path::{Path, PathBuf};

use honba_codegen::validate_instance;
use serde_json::Value;

fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

fn load(rel: &str) -> Value {
    let p = repo().join(rel);
    let text = std::fs::read_to_string(&p).unwrap_or_else(|e| panic!("{}: {e}", p.display()));
    serde_json::from_str(&text).unwrap_or_else(|e| panic!("{}: {e}", p.display()))
}

/// One wire instance: where it came from, its schema type, and the value.
struct Vector {
    origin: String,
    ty: String,
    value: Value,
}

fn cases(file: &str, ty: &str, key: &str, out: &mut Vec<Vector>) {
    let doc = load(file);
    for c in doc[key]
        .as_array()
        .unwrap_or_else(|| panic!("{file}: no `{key}`"))
    {
        out.push(Vector {
            origin: format!("{file}#{key}/{}", c["name"].as_str().unwrap_or("?")),
            ty: ty.to_string(),
            value: c["value"].clone(),
        });
    }
}

fn collect() -> Vec<Vector> {
    let mut v = Vec::new();
    // schema/golden: `{type, cases[].value, tolerated[].value}`; the document
    // `type` names the schema definition.
    let dir = repo().join("schema/golden");
    let mut files: Vec<_> = std::fs::read_dir(dir)
        .unwrap()
        .map(|e| e.unwrap().path())
        .collect();
    files.sort();
    for f in files {
        let rel = format!("schema/golden/{}", f.file_name().unwrap().to_str().unwrap());
        let ty = load(&rel)["type"].as_str().unwrap().to_string();
        for key in ["cases", "tolerated"] {
            if load(&rel).get(key).is_some() {
                cases(&rel, &ty, key, &mut v);
            }
        }
    }
    // schema/conformance: only the manifest cases the Rust side accepts, and
    // the screener predicates and groups, are wire instances.
    let rel = "schema/conformance/strategy_manifest.json";
    for c in load(rel)["cases"].as_array().unwrap() {
        if c["error"].is_null() {
            v.push(Vector {
                origin: format!("{rel}#cases/{}", c["name"].as_str().unwrap()),
                ty: "StrategyManifest".into(),
                value: c["value"].clone(),
            });
        }
    }
    let rel = "schema/conformance/screener_scan.json";
    let scan = load(rel);
    for (key, field, ty) in [
        ("cases", "predicate", "ScreenerFilterPredicate"),
        ("groups", "group", "ScreenerFilterGroup"),
    ] {
        for c in scan[key].as_array().unwrap() {
            if c.get(field).is_some() {
                v.push(Vector {
                    origin: format!("{rel}#{key}/{}", c["name"].as_str().unwrap()),
                    ty: ty.into(),
                    value: c[field].clone(),
                });
            }
        }
    }
    v
}

fn failures(schema: &Value, vectors: &[Vector]) -> Vec<String> {
    vectors
        .iter()
        .flat_map(|v| {
            validate_instance(schema, &v.ty, &v.value)
                .into_iter()
                .map(move |e| format!("{} ({}): {e}", v.origin, v.ty))
        })
        .collect()
}

#[test]
fn golden_wire_vectors_validate_against_domain_schema() {
    let schema = load("schema/domain/domain_schema.json");
    let vectors = collect();
    assert!(
        vectors.len() >= 40,
        "only {} vectors collected",
        vectors.len()
    );
    let bad = failures(&schema, &vectors);
    assert!(
        bad.is_empty(),
        "{} violations:\n{}",
        bad.len(),
        bad.join("\n")
    );
}

#[test]
fn the_check_fails_when_the_schema_drifts() {
    // Mutate the schema in memory: rename a required Order property and
    // narrow an enum. The unmodified vectors must now be rejected.
    let mut schema = load("schema/domain/domain_schema.json");
    schema["$defs"]["Order"]["required"]
        .as_array_mut()
        .unwrap()
        .push(Value::String("not_a_field".into()));
    schema["$defs"]["OrderSide"] = serde_json::json!({"type": "string", "enum": ["sideways"]});
    let bad = failures(&schema, &collect());
    assert!(bad.iter().any(|m| m.contains("not_a_field")), "{bad:?}");
    assert!(bad.iter().any(|m| m.contains("is not one of")), "{bad:?}");
}
