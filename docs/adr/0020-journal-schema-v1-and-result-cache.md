# ADR 0020: Journal Schema v1 and Result Cache

Date: 2026-10-10. Status: **accepted**. Roadmap story: decides D3; implements E4-S1.

## Context

A backtest or sweep produces an execution trace and summary metrics. Prior to this ADR:

- ADR 0017 established the `Run` lifecycle (`pending`, `running`, `completed`, `failed`, `cancelled`),
  directory structure (`data/journals/<run_id>/manifest.json` and `events.ndjson`), and basic
  executor interfaces.
- `AuditLog` in `honba-engine` records an ordered account of dispatches, orders, risk refusals,
  transitions, and fills. `AuditJournalWriter` and `AuditJournalReader` read and write plain
  records, but lacked a formal schema header with reproducibility metadata (`seed`, `config_hash`,
  `dataset_id`, `git_rev`, `trial_count`, `schema_version`).
- Rerunning identical backtests repeated all computation because there was no result cache
  keyed by the canonical configuration.
- The roadmap (E4-S1, D3) requires:
  1. Journal schema v1 (`seed`, `config_hash`, `dataset_id`, `git_rev`, `trial_count`, `schema_version`)
     derived from `AuditLog`.
  2. Result cache keyed by `hash(manifest, params, dataset_id, cost_model_version)`.
  3. Acceptance tests: `journal_schema_roundtrip`, `cache_key_changes_with_costs`, and `rerun_hits_cache`.

## Decision

### 1. Journal Schema v1 Header and Wire Layout

A Journal v1 (`JournalV1`) is stored as newline-delimited JSON (NDJSON):
- **Line 0 (Header):** A JSON object with `"type": "header"`, carrying the run provenance:
  ```json
  {
    "type": "header",
    "journal_version": 1,
    "schema_version": 4,
    "seed": 42,
    "config_hash": "sha256:...",
    "dataset_id": "NSE:TCS:1d:2024-01-01:2024-12-31",
    "git_rev": "a06d5d9",
    "trial_count": 1
  }
  ```
- **Lines 1..N (Audit Records):** The sequential [`AuditRecord`] entries recorded by the engine's
  [`AuditLog`].
- `JournalV1::from_audit_log(header, log)` creates the journal from an audit log.
- `write_ndjson` and `read_ndjson` write and parse this layout. The reader validates `journal_version == 1`
  and rejects unsupported versions.

### 2. Deterministic Cache Key

The cache key (`CacheKey`) uniquely identifies the inputs to a backtest:
```rust
CacheKey::compute(
    manifest_id: &str,
    params: &serde_json::Value,
    dataset_id: &str,
    cost_model_version: &str,
) -> CacheKey
```

- Computation:
  1. Format parameters into canonical JSON (with keys sorted alphabetically).
  2. Compute SHA-256 over:
     `{manifest_id}\n{canonical_params}\n{dataset_id}\n{cost_model_version}`.
  3. Prefix with `"sha256:"` and format as hex.
- Changing the strategy, parameters, dataset, or cost model produces a different cache key.
- Unit test `cache_key_changes_with_costs` confirms that varying `cost_model_version` produces distinct keys.

### 3. Result Cache (`ResultCache`)

A result cache maps `CacheKey` to [`CachedResult`]:
- Carries:
  - `metrics: BacktestMetrics`
  - `assumptions: serde_json::Value`
  - `journal_path: Option<String>`
- Implementations:
  - In-memory cache (`Arc<Mutex<HashMap<CacheKey, CachedResult>>>`).
  - Persistent on-disk cache under a cache directory (`<cache_dir>/<hash>.json`).
- If an entry exists for the `CacheKey`, the execution returns the cached result without re-running.
- Integration test `rerun_hits_cache` asserts that identical reruns hit the cache.

## Consequences

- Provenance metadata is preserved in all v1 journals.
- Identical backtest runs can be skipped when results are cached.
- Layering is respected: Journal types live in `honba-engine` (or `honba-api`), `CacheKey` and
  `ResultCache` live in `honba-api` / `honba-api-rest`.
