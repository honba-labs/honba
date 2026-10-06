//! ADR 0015: `honba-py` is an `extension-module` cdylib and must never resolve pyo3 with
//! `auto-initialize` (it embeds an interpreter, which is wrong for a module loaded by Python).
//! Reads the resolved feature set from `cargo metadata`, so a transitive enable fails too.

use std::process::Command;

use serde_json::Value;

fn resolved_pyo3_features() -> Vec<String> {
    let cargo = std::env::var("CARGO").unwrap_or_else(|_| "cargo".into());
    let out = Command::new(cargo)
        .args(["metadata", "--format-version", "1", "--offline"])
        .current_dir(env!("CARGO_MANIFEST_DIR"))
        .output()
        .expect("cargo metadata runs");
    assert!(
        out.status.success(),
        "{}",
        String::from_utf8_lossy(&out.stderr)
    );
    let meta: Value = serde_json::from_slice(&out.stdout).unwrap();
    let nodes = meta["resolve"]["nodes"].as_array().unwrap();
    let mut features = Vec::new();
    for node in nodes {
        let id = node["id"].as_str().unwrap();
        if id.contains("#pyo3@") || id.contains("/pyo3#") {
            for f in node["features"].as_array().unwrap() {
                features.push(f.as_str().unwrap().to_owned());
            }
        }
    }
    features
}

#[test]
fn pyo3_resolves_with_extension_module_and_without_auto_initialize() {
    let features = resolved_pyo3_features();
    assert!(
        features.iter().any(|f| f == "extension-module"),
        "pyo3 not found or lacks extension-module: {features:?}"
    );
    assert!(
        !features.iter().any(|f| f == "auto-initialize"),
        "pyo3 `auto-initialize` is enabled (ADR 0015): {features:?}"
    );
}
