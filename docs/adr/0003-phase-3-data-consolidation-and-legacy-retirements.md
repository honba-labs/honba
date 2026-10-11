# ADR 003: Phase 3 Data Consolidation and Legacy Crate Retirement

## Status
Accepted / Completed (Amended: crate renamed to `honba-feed`)

## Context
Per [docs/archive/ROADMAP-borrowed-ideas.md](../archive/ROADMAP-borrowed-ideas.md) Section 2b:
1. `honba-algo-import` and `honba-algo-export` were small, split crates dealing with complementary halves of market data ingestion and report generation. The roadmap calls for consolidating them into a unified catalog and data replay/feed crate (L5).
2. `honba-india` had all its functionality lifted to `honba-market` (L2) with a pluggable architecture (generic market interfaces + `india` feature module + `null` market pack).
3. The old directories (`honba-algo-import`, `honba-algo-export`, `honba-india`) remained as duplicate or legacy folders.

## Decisions
1. **Consolidate Import and Export into `crates/honba-feed`:**
   - Create unified crate `crates/honba-feed` (initially named `honba-data`, renamed to `honba-feed` to disambiguate from the top-level `honba-data` lakehouse repository) containing:
     - `import`: Parquet bar loaders, CSV, broker/exchange connectors
     - `dataset`: Deterministic columnar slicing and `DatasetFeed` implementing `honba_engine::DataFeed`
     - `reader`: Parquet `DatasetReader`
     - `export`: CSV, JSON, Markdown, Excel, and report writers
2. **Retire Old Directories:**
   - Delete `crates/honba-algo-import/` and `crates/honba-algo-export/`.
   - Delete `crates/honba-india/` now that `crates/honba-market/` is the authoritative L2 market pack crate.
3. **Update Workspace & Dependency Graph:**
   - Remove retired crates from `honba/Cargo.toml` and dependents.
   - Update `scripts/dependency_graph.py` to lock down `honba-feed` and `honba-market`.

## Amendment (October 2026)
With the introduction of the independent top-level repository `honba-data` (which houses the DuckDB corporate fundamentals lakehouse, balance sheets, and data providers), `crates/honba-data` was renamed to `crates/honba-feed` across the Cargo workspace. This makes the domain boundary explicit:
- `honba-feed` is the L5 engine replay feed and backtest exporter within the core Rust workspace.
- `honba-data` is the standalone financial data lakehouse repository.

## Consequences
- Clean, canonical crate footprint matching the Roadmap Section 2b target layering.
- Zero redundant/duplicate crates in the workspace.
- Enforced inward layering validated in CI.
- No semantic ambiguity between the core engine's replay feed (`honba-feed`) and the data lakehouse (`honba-data`).
