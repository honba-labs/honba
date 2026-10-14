# ADR 0027: Report builder with a fact snapshot and quality gate

Date: 2026-10-14. Status: **accepted**. Roadmap story: implements E5-S9 ("Report builder with a
fact snapshot and quality gate", `docs/archive/ROADMAP-borrowed-ideas.md:209,541`;
`docs/ROADMAP.md:363`). Depends on E4-S1 (journal schema v1 and result cache, ADR 0020) and E5-S7
(typed LLM decisions, ADR — shipped in `honba.ai.decision`). Feeds the tearsheet and E9-S5.

## Context

The research loop can already produce a result, a journal and typed LLM decisions, but any
"narrative" over a run is written by an LLM and is therefore untrusted text that can invent numbers.
There is no report builder in the tree: `crates/honba-analytics/src/tearsheet.rs` and
`crates/honba-feed/src/export/tearsheet.rs` are one-line stubs; `python/src/honba/report.py` renders
a `BacktestResult` for a human and does not constrain any narrative.

The pieces this story must reuse:

- `honba.session.BacktestResult` (metrics, fills, rejections, equity curve) and the REST
  `BacktestResponse` (`assumptions`, `run_id`) are the run facts.
- `honba.ai.decision` (E5-S7) — typed `LlmDecision` (veto/size-down), the only LLM output with a
  schema today.
- `honba.ai.trust` (E5-S2) — `wrap_untrusted`, the envelope that marks LLM text untrusted with an
  origin and a content hash.

## Decision

### 1. Placement and shape

Python-only, in `python/src/honba/ai/report.py`, exported from `honba.ai`. Public surface:

```
FactSnapshot          # frozen: run_id, strategy, metrics, journal_ids, assumptions, schema_version
build_snapshot(       # from a BacktestResult / mapping / object-with-attrs
    source, *, run_id=None, strategy=None, schema_version=1
) -> FactSnapshot
QualityViolation      # frozen: number, context
QualityReport         # frozen: ok, violations
quality_gate(narrative, snapshot, *, extra_allowed=()) -> QualityReport
Report                # frozen: snapshot, narrative, untrusted (TrustEnvelope), quality; .as_markdown()
build_report(snapshot, narrator, *, title=None, extra_allowed=(), origin="llm:narrative") -> Report
```

`narrator` is a callable `(FactSnapshot) -> str`. It is the **only** function that produces the
narrative, and the snapshot is its **only** argument — the report builder never hands the model the
raw result, fills or journal.

### 2. The fact snapshot is immutable and self-contained

`FactSnapshot` is a frozen slotted dataclass. `metrics` is copied into a private tuple of pairs (a
`MappingProxyType` view is exposed), and `journal_ids`/`assumptions` are tuples, so a snapshot cannot
be mutated after construction and a caller's dict cannot leak in. `schema_version` records the
snapshot shape (bumped per ADR 0012 when the fields change).

The snapshot exposes `allowed_numbers() -> frozenset[str]`: the normalised textual forms of every
number the snapshot contains — each metric **value**, plus any numeric token inside `run_id`,
`journal_ids` and `assumptions`. This is the whole vocabulary a narrative may cite.

### 3. The quality gate rejects any number not in the snapshot

`quality_gate` extracts every numeric token from the narrative (integers, decimals, thousands
separators, currency symbols, a trailing `%`, and scientific notation), normalises it
(strip separators/currency/`%`, parse as `Decimal`), and accepts it only if a normalised form or a
value-equivalent form is in `allowed_numbers()` across a small set of representations (raw, 2-dp,
comma-grouped, `×100` with `%`, `÷100`). The first unmatched token is a `QualityViolation` naming
the token and a short surrounding context. `ok` is true only when there are no violations.

`extra_allowed` lets a caller whitelist specific tokens (e.g. a year the report template adds); it is
empty by default so the gate is strict.

### 4. The narrative is wrapped as untrusted

`build_report` runs the narrator, wraps the result with `honba.ai.trust.wrap_untrusted(origin=...)`,
runs the quality gate, and returns a `Report`. `Report.as_markdown()` renders a heading, the
snapshot facts as a table, and the narrative **inside the trust envelope delimiters**, so any
downstream consumer (the tearsheet, E9-S5, the MCP/LLM surfaces) sees the provenance.

### 5. Determinism

`build_report` is a pure function of `(snapshot, narrator, title, extra_allowed, origin)`: no clock,
no I/O, no randomness. A deterministic narrator makes a byte-identical report, which the integration
test relies on.

## Consequences

- An LLM-written narrative can no longer silently invent numbers: the quality gate is the single
  chokepoint, and it returns structured violations rather than a boolean.
- The snapshot is the contract between the run and the narrative; adding a citable fact means adding
  it to the snapshot, not loosening the gate.
- The report is presentation plus provenance; the tearsheet (E9-S5) renders the same `Report`.

## Acceptance (unit / integration)

Unit (`python/tests/unit/test_report_builder.py`):

- `snapshot_is_immutable` — frozen; a caller's dict is copied; mutation is impossible.
- `narrator_receives_only_the_snapshot` — the narrator is called once with the snapshot.
- `quality_gate_allows_snapshot_numbers` — a narrative citing snapshot metrics passes.
- `narrative_number_outside_snapshot_rejected` — a fabricated number yields a violation naming it.
- `percentage_and_currency_forms_are_matched` — `12.5%` and `₹1,234.56` match `12.5`/`1234.56`.

Integration (`python/tests/integration/test_report_snapshot.py`):

- `report_uses_only_snapshot_facts` — a `FactSnapshot` built from a `BacktestResult` drives
  `build_report`; a good narrator yields `quality.ok` and a markdown body, a bad one is rejected.
- `llm_narrative_is_wrapped_untrusted` — the report carries an `untrusted` envelope with an origin.

## Deferred

| Item | Disposition |
|---|---|
| REST/MCP `POST /reports` route and tool | Separate story; needs codegen and an ADR 0013 route decision. |
| Persisting the report alongside the journal | Owns a `ReportV1` schema; not needed to gate a narrative. |
| LLM-authored *sections* beyond a single narrative (tables, charts) | The gate is written for free text; structured output is E9-S5. |
| Wiring the E5-S7 decision list into the snapshot | Add `llm_decisions` to the snapshot when a caller needs it (schema_version bump). |

## Known limits

- The gate matches numbers, not claims: a narrative can be wrong about *direction* (up vs down) or
  attribution while citing only real numbers. It is a numeric hallucination gate, not a fact checker.
- Value-equivalent forms (`×100`, `÷100`) mean a metric stored as `0.125` also allows `12.5%`; this
  is intentional and loose by one scale, not arbitrary.
- Narratives that legitimately need a number absent from the snapshot must pass it via
  `extra_allowed`; the default is strict.

## References

- `docs/archive/ROADMAP-borrowed-ideas.md:209` (E5-S9 scope), `:541` (snapshot + quality gate),
  `docs/ROADMAP.md:363` (E5-S9).
- `docs/adr/0020-journal-schema-v1-and-result-cache.md`, `docs/adr/0012-versioning-policy.md`.
- `python/src/honba/ai/decision.py` (E5-S7), `python/src/honba/ai/trust.py` (E5-S2),
  `python/src/honba/session.py` (`BacktestResult`), `python/src/honba/report.py` (human presentation).
