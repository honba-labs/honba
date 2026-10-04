# ADR 003: Phase 3 Data Consolidation and Legacy Crate Retirement

## Status
Accepted / Completed

## Context
Per [ROADMAP-borrowed-ideas.md](../../ROADMAP-borrowed-ideas.md) Section 2b:
1. `honba-algo-import` and `honba-algo-export` were small, split crates dealing with complementary halves of market data ingestion and report generation. The roadmap calls for consolidating them into a unified catalog and data crate `honba-data` (L5).
2. `honba-india` had all its functionality lifted to `honba-market` (L2) with a pluggable architecture (generic market interfaces + `india` feature module + `null` market pack).
3. The old directories (`honba-algo-import`, `honba-algo-export`, `honba-india`) remained as duplicate or legacy folders.

## Decisions
1. **Consolidate Import and Export into `honba-data`:**
   - Create `crates/honba-data` containing:
     - `import`: Parquet bar loaders, CSV, broker/exchange connectors
     - `export`: CSV, JSON, Markdown, and report writers
2. **Retire Old Directories:**
   - Delete `crates/honba-algo-import/` and `crates/honba-algo-export/`.
   - Delete `crates/honba-india/` now that `crates/honba-market/` is the authoritative L2 market pack crate.
3. **Update Workspace & Dependency Graph:**
   - Remove retired crates from `honba/Cargo.toml` and dependents.
   - Update `scripts/dependency_graph.py` to lock down `honba-data` and `honba-market`.

## Consequences
- Clean, canonical crate footprint matching the Roadmap Section 2b target layering.
- Zero redundant/duplicate crates in the workspace.
- Enforced inward layering validated in CI.
