# ADR 0014: JSON Schema and Every Other Contract Artifact Are Generated From Rust

Date: 2026-10-06. Status: accepted. Supersedes ADR 0006 decision 1 (in part). Roadmap story: E0-S2 follow-up (plan.md
§4.1, §9 row 6).

## Context

ADR 0006 decision 1 made Rust serde the source of truth for the wire contract but chose not to add `schemars`. It
placed JSON Schema *downstream of the Python models*: a later story would export it from the pydantic wire models
(`model_json_schema()`) and diff it in CI. The Rust-to-Python link was kept honest by convention plus tests (golden
vectors, `wire_enum_values()` parity, the `canonical_json` round trip).

plan.md §4.1 needs more than JSON Schema: OpenAPI, TypeScript, `.pyi` stubs and MCP tool schemas must all describe the
same types. With pydantic as the schema source, the REST, TypeScript and MCP artifacts would each need a second
generator reading a Python re-implementation of the Rust types, and a field added in Rust would reach them only after
someone mirrored it by hand. One generator reading the Rust types removes that step.

State today: `schemars` (0.8, `derive`) is a workspace dependency, derived by `honba-messages`, `honba-entities`,
`honba-strategy`, `honba-config`, `honba-api` and `honba-api-rest`. `honba-codegen` (L5) walks one registry of those
types (`registry.rs`: `WIRE_TYPES`, `WIRE_ENUMS`, `API_TYPES`, `CONFIG_TYPES`, `MANIFEST_TYPES`, `SCREENER_TYPES`) and
renders JSON Schema (`schemas.rs`), OpenAPI 3.1 with real `paths` from the endpoint registry (`endpoints.rs`),
TypeScript (`typescript.rs`), `.pyi` stubs (`typings.rs`) and MCP tool schemas (`mcp.rs`). It is driven by
`honba schema export`.

## Decision

1. **Rust is the single generator input.** JSON Schema, OpenAPI, TypeScript, `.pyi` stubs and MCP tool schemas are all
   generated from the Rust types by `honba-codegen` using `schemars`. This **supersedes ADR 0006 decision 1**: the
   "no `schemars` dependency" clause and the plan to export schema from pydantic are withdrawn. The rest of ADR 0006
   stands (serde is the wire form, representation rules, invariants, golden vectors, the `canonical_json` round trip,
   duplicate-key rejection).
2. **Generated artifacts are committed and drift-checked.** The outputs live in `schema/domain` (JSON Schema),
   `schema/openapi`, `schema/mcp` and `python/src/honba/wire/generated` (`.pyi`); the frontend TypeScript is written to
   `../honba-frontend/src/core/types/generated`. `make codegen` regenerates them. `make check-codegen-ci` (the checks that
   need only this repo: `check-schema`, `check-openapi`, `check-pyi`, `check-mcp`) regenerates and fails on any
   modified, deleted or untracked file under the output directory. `make check-codegen` adds `check-schema-ts`, which
   writes into the sibling frontend checkout and so runs locally only.
3. **Generated files are never edited by hand.** A change to a wire type is a Rust change plus regenerated, committed
   artifacts in the same commit.
4. **Python consumes the contract; it does not define it.** The pydantic models in `honba.entities.wire` and
   `honba.wire` (`base.py`, `wire.py`, `screener.py`), and the pydantic request/manifest/config models elsewhere in
   `honba` (`strategies`, `client`), are consumers. They are still checked against the Rust contract by the golden
   vectors and the `canonical_json` round trip (ADR 0006 decision 2, unchanged). They are not a schema source, and no
   code path calls `model_json_schema()` to produce a committed artifact.

## Consequences

- Adding or changing a wire field touches Rust first; every downstream artifact changes by regeneration and CI blocks a
  forgotten regeneration.
- `schemars` is now a normal dependency of the domain crates that carry wire types. It adds derive-time code only and
  no I/O, so ADR 0006's purity rule for domain crates is unaffected.
- Where the generated schema and a hand-written pydantic model disagree, the Rust-generated schema is right and the
  pydantic model is the bug.
- ADR 0006 decision 3 (representation rules) is enforced by serde; the schema has to describe the serde form
  faithfully. Today that is guarded only by the drift checks and the renderer goldens (`honba-codegen/tests`), which
  pin the generated output; nothing yet validates the golden wire vectors against the generated schema (see Known
  limits).

## Known limits

- The frontend TypeScript drift check is local only, because it needs the `honba-frontend` sibling checkout. CI does not
  guard that artifact.
- No test validates the golden wire vectors (`schema/golden`, `schema/conformance`) against `domain_schema.json`, so a
  schema that drifts from the serde form in a way the snapshots accept would go unnoticed. Adding that test is the
  follow-up that closes the gap.
- The Python wire models are still hand-maintained mirrors. Generating them (plan.md §4.1 "Python types are generated,
  then hand-extended") is not done; `python/src/honba/wire/generated` holds `.pyi` stubs only. This ADR does not
  decide that step.
