# ADR 0012: Versioning Policy — Three Axes, One Owner Each

Date: 2026-10-05. Status: accepted. Roadmap story: E0-S5.

## Context

docs/archive/plan.md §4.2 names three version axes but never pins who owns what, and the
code grew two owners for `SCHEMA_VERSION` (`honba_messages::SCHEMA_VERSION`
and the Python `honba.entities.wire.SCHEMA_VERSION`). Duplicate owners are how
a field becomes a liar: one bumps, the other does not, and readers disagree.

## Decision

| Axis | Type | Owned by | Bumped when |
|---|---|---|---|
| `schema_version` | `u32` integer | `honba-messages::SCHEMA_VERSION`, re-exported (not redeclared) by `honba-codegen` | any wire-shape change, additive or breaking |
| `api_version` | semver string | `honba-messages::API_VERSION` (it rides the envelope), served at `/api/v1` | endpoint added/removed/reshaped |
| `strategy_api_version` | semver string | `honba-strategy::STRATEGY_API_VERSION` (stamped into every `StrategyManifest.api_version`) | a strategy hook or context signature changes |
| crate version | semver | the release process (`Cargo.toml`, PyPI) | releases |

`strategy_api_version` is a fourth axis, not an alias of `api_version`: the
REST surface and the strategy contract move independently (a new endpoint does
not invalidate compiled strategies, and a new strategy hook does not change
`/api/v1`). Folding them into one constant would force a bump of one whenever
the other moves. It has one owner like the others.

Python never declares a version literal. `honba.wire.SCHEMA_VERSION`,
`honba.wire.API_VERSION` and `honba.strategies.manifest.STRATEGY_API_VERSION`
are read from `honba._honba` at import time, and
`python/tests/unit/test_versioning.py` fails on any literal assignment to
those names anywhere under `python/src/honba` (generated code excepted).

Rules (docs/archive/plan.md §4.2, restated as the binding version):

1. **Wire additive changes do not bump `schema_version`.** A new optional
   field is additive. Readers must reject an unknown `schema_version` and must
   ignore unknown fields, so a v1 reader consumes v1-with-extras safely.
2. **`/api/v1` is frozen the day the REST surface lands.** Additive-only within
   a major: new endpoints, new optional fields, new enum variants are fine;
   removing or retyping anything is `/api/v2`.
3. **Enum-variant addition is breaking for exhaustive decoders.** OpenAPI
   consumers get `additionalProperties`-tolerant types, not closed unions; Rust
   and Python treat unknown enum spellings as a decode error on strict paths.
4. **Timestamps cross HTTP/WASM as ISO-8601 strings** with a separate integer
   `unix_nanos` string, never as a JSON number (`Number.MAX_SAFE_INTEGER`
   cannot hold u64 nanos). This is E11-S2; it lands before any external
   consumer ships. The value is `unix_nanos`: plain ASCII decimal digits of
   a `u64` (no sign, space or exponent; leading zeros are read and dropped).
   `iso` is RFC 3339 UTC with nine fractional digits, always derived from
   `unix_nanos` by writers and informational on read (both readers recompute
   it). Pre-epoch instants are not representable and are rejected.
   `schema/golden/unix_nanos.json` pins this for Rust, Python and (through
   the generated `UnixNanos { iso: string; unix_nanos: string }`) TypeScript,
   including 2^53 + 1 and `u64::MAX`.
5. **Every breaking wire change gets a migration note** in the crate's
   `CHANGELOG.md` at the same time as the code, not in a follow-up.

## Unknown fields: records tolerate, inputs reject

Rule 1 ("readers ignore unknown fields") and the `deny_unknown_fields` on the
config and manifest types pull in opposite directions. They are resolved by
what the payload *is*, not where it travels:

| Kind | Examples | Unknown field | Why |
|---|---|---|---|
| **Record** — data a newer producer may extend | `Message` (envelope), every `Event` variant, `Bar`, `QuoteTick`, `TradeTick`, `Order`, `Trade`, `Position`, `Money`, `InstrumentId`, `UnixNanos`, `StrategyIr` | ignored (dropped on parse) | forward compatibility: a v3-with-extras stream from a newer producer must not kill an older reader mid-run |
| **Input** — authored by a person, an agent or a strategy | `BacktestRunConfig` and its sections, `StrategyManifest`, `OrderIntent`, `ScreenerFilterPredicate` and the screener request types, `honba-api` request bodies | rejected | a dropped field silently changes what the author asked for (`slipage_multiplier = 2` would run at 1.0; an intent's misspelt `trigger_price` would become a market order), so a typo is an error |

What still protects records is the version: an unknown `schema_version` is
rejected (rule 1), and a field whose *meaning* changes is a reshape that bumps
it. Inputs gain fields only with defaults, so an older author's document still
parses.

Both languages implement the same split: Rust omits `deny_unknown_fields` on
records and keeps it on inputs; Python's `honba.wire.base._Wire` uses
`extra="ignore"` and `_Command` (inputs) `extra="forbid"`. The golden vectors
pin it: each record file has a `tolerated` section (`value` with extras,
`canonical` with them dropped) that Rust and Python must both reduce to the
same JSON, and each input file keeps an `unknown_field` case under `invalid`
(`python/tests/unit/test_wire_golden.py::test_golden_files_pin_the_unknown_field_policy`).

`honba-api` follows the same split: its response DTOs (and `Capabilities`) are
records and ignore unknown fields, its request bodies and query DTOs are inputs
and reject them; `schema/openapi` publishes `additionalProperties: false` only on
the latter (`crates/honba-api/src/tests/unknown_fields.rs`).

## Consequences

- The Python `SCHEMA_VERSION` constant becomes a runtime read of
  `honba._honba.SCHEMA_VERSION`, not a second literal. A test pins the two equal.
- The committed `schema/domain/domain_schema.json` is regenerated by
  `honba-codegen` and drift-checked in CI; hand edits are rejected by the
  no-edit guard test.
- Golden vectors assert the envelope's `schema_version` against the one owner.
