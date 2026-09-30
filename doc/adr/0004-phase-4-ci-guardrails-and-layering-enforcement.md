# ADR 004: Phase 4 Inward Dependency Guardrails and CI Enforcement

## Status
Accepted / Completed

## Context
Per [ROADMAP-borrowed-ideas.md](../../ROADMAP-borrowed-ideas.md) Section 2b, the workspace requires strict inward dependency layering:
```text
L0  honba-messages
L1  honba-entities
L2  honba-market
L3  honba-engine
    honba-indicators
L4  honba-sim
    honba-strategy
L5  honba-analytics
    honba-data
L6  honba-testing (dev fixtures)
L7  honba-py (cdylib)
    honba-cli (binary)
```
Any crate may only depend on crates in strictly lower (or peer within defined rules) layers. Dependencies must never point outward or create cycles.

## Decisions
1. **Layer Hierarchy Map:**
   - Standardize the `ALLOWED` map in `scripts/dependency_graph.py` to enforce the canonical layers.
   - Run layer hierarchy enforcement automatically in CI.
2. **CI Pipeline Integration:**
   - Verified that `.github/workflows/ci.yml` runs `scripts/dependency_graph.py` on all pushes and pull requests.
3. **Repository Documentation Synchronized:**
   - Synchronized `CLAUDE.md` and repository guidelines to reflect the current, canonical crate structure and test commands.

## Consequences
- Guaranteed architectural integrity through automated CI gates.
- Compile-time and CI-level prevention of cyclical or layer-violating dependencies.
