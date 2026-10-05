//! `honba verify` – validate a strategy manifest (plan.md E0-S8).
//!
//! The CLI reads a JSON file, deserializes it as a `StrategyManifest`, and
//! runs `Manifest::validate()`. A non-zero exit code means the manifest is
//! invalid and cannot be run. This is the gate that keeps a bad manifest from
//! producing an empty journal that looks like a strategy that did nothing.

use anyhow::{Context, Result};
use std::path::Path;

use honba_strategy::StrategyManifest;

pub fn run(path: &Path) -> Result<()> {
    let text =
        std::fs::read_to_string(path).with_context(|| format!("reading {}", path.display()))?;
    let manifest: StrategyManifest = serde_json::from_str(&text)
        .with_context(|| format!("parsing {} as StrategyManifest", path.display()))?;
    manifest.validate().map_err(|e| anyhow::anyhow!("{e}"))?;
    println!("✓ manifest is valid (api_version={})", manifest.api_version);
    Ok(())
}
