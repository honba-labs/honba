# ADR 002: Phase 2 Crate Infix Harmonization and Core Engine Rename

## Status
Accepted / Completed

## Context
Per [ROADMAP-borrowed-ideas.md](../../ROADMAP-borrowed-ideas.md) Section 2b, the workspace uses inconsistent naming prefixes:
- `honba-algo` serves as the event loop engine but carries the ambiguous name `algo`.
- Five feature crates contain the verbose `-algo-` infix (`honba-algo-indicators`, `honba-algo-strategies`, `honba-algo-analytics`, `honba-algo-testing`), contrasting with `honba-messages`, `honba-entities`, `honba-sim`, and `honba-market`.

## Decisions
1. **Drop `-algo-` Infixes & Harmonize Crates:**
   - `honba-algo` $\rightarrow$ `honba-engine` (L3)
   - `honba-algo-indicators` $\rightarrow$ `honba-indicators` (L3)
   - `honba-algo-strategies` $\rightarrow$ `honba-strategy` (L4)
   - `honba-algo-analytics` $\rightarrow$ `honba-analytics` (L5)
   - `honba-algo-testing` $\rightarrow$ `honba-testing` (L6)
2. **Backward Compatibility via Transition Packages / Aliases:**
   - Ensure external scripts and dependents compile without breaking existing import structures.
3. **Workspace Configuration & Dependency Graph:**
   - Update `Cargo.toml` workspace members and `[workspace.dependencies]`.
   - Update `scripts/dependency_graph.py` to enforce the new canonical names and allowed dependency layers.

## Consequences
- Concise and standard crate naming conforming to the target layered architecture.
- Clear separation between the execution engine (`honba-engine`) and strategies (`honba-strategy`).
