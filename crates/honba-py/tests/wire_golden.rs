//! The Python-facing wire contract (`canonical_json`) agrees with the shared
//! golden vectors in `schema/golden/` (ADR 006).
//!
//! Exercises `honba::pyclasses::wire::canonical` across the messages,
//! entities and strategy crates through the Rust API only: no Python
//! interpreter is started, so this runs under plain `cargo test`.

use std::path::PathBuf;

use honba::pyclasses::wire::{canonical, KINDS};
use serde_json::Value;

fn golden_dir() -> PathBuf {
    PathBuf::from(env!("CARGO_MANIFEST_DIR")).join("../../schema/golden")
}

fn golden_files() -> Vec<(String, Value)> {
    let mut files: Vec<_> = std::fs::read_dir(golden_dir())
        .unwrap()
        .map(|e| e.unwrap().path())
        .filter(|p| p.extension().is_some_and(|e| e == "json"))
        .collect();
    files.sort();
    files
        .into_iter()
        .map(|p| {
            let doc: Value = serde_json::from_str(&std::fs::read_to_string(&p).unwrap()).unwrap();
            (p.file_name().unwrap().to_string_lossy().into_owned(), doc)
        })
        .collect()
}

fn entries<'a>(doc: &'a Value, key: &str) -> impl Iterator<Item = &'a Value> {
    doc.get(key).and_then(Value::as_array).into_iter().flatten()
}

#[test]
fn every_wire_kind_has_a_golden_file() {
    let mut covered: Vec<String> = golden_files()
        .iter()
        .map(|(_, doc)| doc["type"].as_str().unwrap().to_owned())
        .collect();
    covered.sort();
    let mut kinds: Vec<String> = KINDS.iter().map(|k| (*k).to_owned()).collect();
    kinds.sort();
    assert_eq!(covered, kinds);
}

#[test]
fn golden_cases_round_trip_unchanged_and_idempotently() {
    for (file, doc) in golden_files() {
        let kind = doc["type"].as_str().unwrap();
        for case in entries(&doc, "cases") {
            let name = &case["name"];
            let value = &case["value"];
            let out = canonical(kind, &value.to_string())
                .unwrap_or_else(|e| panic!("{file} {name}: {e}"));
            let parsed: Value = serde_json::from_str(&out).unwrap();
            assert_eq!(&parsed, value, "{file} {name}");
            assert_eq!(canonical(kind, &out).unwrap(), out, "{file} {name}");
        }
    }
}

#[test]
fn golden_invalid_values_and_texts_are_rejected() {
    let mut checked = 0;
    for (file, doc) in golden_files() {
        let kind = doc["type"].as_str().unwrap();
        for case in entries(&doc, "invalid") {
            let payload = case["value"].to_string();
            assert!(
                canonical(kind, &payload).is_err(),
                "{file} {}: accepted {payload}",
                case["name"]
            );
            checked += 1;
        }
        for case in entries(&doc, "invalid_text") {
            let text = case["text"].as_str().unwrap();
            assert!(
                canonical(kind, text).is_err(),
                "{file} {}: accepted {text}",
                case["name"]
            );
            checked += 1;
        }
    }
    assert!(checked > 0, "no invalid golden cases found");
}
