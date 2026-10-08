//! End-to-end: generate every artifact into a temporary directory and compare
//! it byte-for-byte with the committed copy.
//!
//! This is the drift check inside `cargo test`: a wire type that changes
//! without `make schema openapi pyi mcp` fails here, in the Rust CI job, before
//! the Makefile's `git status` checks ever run. The TypeScript output lives in
//! the sibling `honba-frontend` repo, so it is only rendered here (the
//! cross-repo comparison is `make check-schema-ts`, local only).

use std::path::{Path, PathBuf};

use honba_codegen::{unresolved_local_refs, Codegen};

/// The repository root, two levels above this crate.
fn repo() -> PathBuf {
    Path::new(env!("CARGO_MANIFEST_DIR")).join("../..")
}

/// A fresh, empty scratch directory unique to one test.
fn scratch(name: &str) -> PathBuf {
    let dir = std::env::temp_dir().join(format!("honba-codegen-{}-{name}", std::process::id()));
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).expect("scratch dir");
    dir
}

fn read(path: &Path) -> String {
    std::fs::read_to_string(path).unwrap_or_else(|e| panic!("reading {}: {e}", path.display()))
}

/// Writes all artifacts and returns `(generated, committed)` path pairs.
fn generate(name: &str) -> Vec<(PathBuf, PathBuf)> {
    let out = scratch(name);
    let written = Codegen::new().write_all(&out).expect("write_all");
    let root = repo();
    let committed = [
        root.join("schema/domain/domain_schema.json"),
        root.join("schema/openapi/openapi.json"),
        PathBuf::new(), // TypeScript: committed in honba-frontend, not here.
        root.join("python/src/honba/wire/generated/__init__.pyi"),
        root.join("schema/mcp/mcp_tools.json"),
    ];
    assert_eq!(written.len(), committed.len(), "artifact list changed");
    written
        .into_iter()
        .zip(committed)
        .filter(|(_, c)| !c.as_os_str().is_empty())
        .collect()
}

#[test]
fn every_committed_artifact_matches_regeneration() {
    let mut stale = Vec::new();
    for (generated, committed) in generate("match") {
        if read(&generated) != read(&committed) {
            stale.push(committed.display().to_string());
        }
    }
    assert!(
        stale.is_empty(),
        "stale generated artifacts (run `make schema openapi pyi mcp`): {stale:?}"
    );
}

#[test]
fn every_artifact_says_it_is_generated_and_not_to_be_edited() {
    let out = scratch("header");
    for path in Codegen::new().write_all(&out).expect("write_all") {
        let text = read(&path);
        assert!(
            text.contains("DO NOT EDIT"),
            "{} carries no DO NOT EDIT marker",
            path.display()
        );
    }
}

#[test]
fn every_committed_json_artifact_resolves_all_refs() {
    let root = repo();
    for rel in [
        "schema/domain/domain_schema.json",
        "schema/openapi/openapi.json",
    ] {
        let doc: serde_json::Value =
            serde_json::from_str(&read(&root.join(rel))).expect("valid JSON");
        let unresolved = unresolved_local_refs(&doc);
        assert!(unresolved.is_empty(), "{rel}: {unresolved:?}");
    }
    let mcp: serde_json::Value =
        serde_json::from_str(&read(&root.join("schema/mcp/mcp_tools.json"))).expect("valid JSON");
    for tool in mcp["tools"].as_array().expect("tools") {
        let unresolved = unresolved_local_refs(&tool["inputSchema"]);
        assert!(unresolved.is_empty(), "{}: {unresolved:?}", tool["name"]);
    }
}

#[test]
fn generation_is_deterministic() {
    let first: Vec<String> = Codegen::new()
        .write_all(&scratch("det-a"))
        .unwrap()
        .iter()
        .map(|p| read(p))
        .collect();
    let second: Vec<String> = Codegen::new()
        .write_all(&scratch("det-b"))
        .unwrap()
        .iter()
        .map(|p| read(p))
        .collect();
    assert_eq!(first, second);
}

#[test]
fn config_types_include_risk_limits() {
    assert_eq!(
        honba_codegen::CONFIG_TYPES,
        ["BacktestRunConfig", "RiskLimits"]
    );
    assert!(honba_codegen::published_names().contains(&"RiskLimits"));
    let schema = read(&repo().join("schema/domain/domain_schema.json"));
    assert!(
        schema.contains("\"RiskLimits\""),
        "committed schema lacks RiskLimits"
    );
}
