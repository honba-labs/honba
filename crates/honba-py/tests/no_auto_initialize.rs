//! ADR 0015: `honba-py` is an `extension-module` cdylib and must never resolve pyo3 with
//! `auto-initialize` (it embeds an interpreter, which is wrong for a module loaded by Python).
//! Reads the resolved feature set from `cargo metadata`, so a transitive enable fails too.

use std::process::Command;

use serde_json::Value;

/// The host target triple, so the resolve graph is limited to crates this build already has.
///
/// Without a platform filter `cargo metadata` resolves target-specific dependencies of every
/// platform (for example `android-tzdata`, pulled in by chrono), which fails when the registry
/// cache only holds what the host build needs. The pyo3 feature set does not depend on it.
fn host_triple() -> String {
    let rustc = std::env::var("RUSTC").unwrap_or_else(|_| "rustc".into());
    let out = Command::new(rustc)
        .arg("-vV")
        .output()
        .expect("rustc -vV runs");
    String::from_utf8(out.stdout)
        .unwrap()
        .lines()
        .find_map(|l| l.strip_prefix("host: ").map(str::to_owned))
        .expect("rustc -vV reports a host triple")
}

fn resolved_pyo3_features() -> Vec<String> {
    let cargo = std::env::var("CARGO").unwrap_or_else(|_| "cargo".into());
    let out = Command::new(cargo)
        .args(["metadata", "--format-version", "1", "--offline"])
        .args(["--filter-platform", &host_triple()])
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
