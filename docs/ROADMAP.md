# Honba roadmap (single forward-looking plan)

Status: v8, 2026-10-14 (closes two architecture-level items in §7; v7 below was a status refresh against `main`). Supersedes `docs/archive/ROADMAP-borrowed-ideas.md` (v4, 2026-10-01) and merges `docs/archive/plan.md`
(2026-10-03) phases and epics E10 and E11 into one numbering-preserving structure. Story IDs (E0..E11) are unchanged so old references
still resolve. Nothing here has been filed as an issue. This file lives in the `honba` git repo at `docs/ROADMAP.md`; paths are relative
to the `honba/` root unless they name a sibling repo. Evidence is from the `honba/` tree and its git log, read-only; test baselines are
from `honba/.claude/HANDOFF.md` (cargo ~1045, python ~2413 passed, 8 skipped); they were not re-measured for v6, v7 or v8.

v8 changes (2026-10-14): two open architecture-level items (§7) are closed on the `honba` side.
Run determinism now matches the sweep shape: `crates/honba-async/tests/shell.rs` drives the same
seeded run through `EngineHandle` (a `tokio::spawn`, so the engine runs on the runtime's worker
threads) from runtimes with 1, 4 and 16 `worker_threads` and asserts a byte-identical audit trail
(`determinism_under_threads_{one,four,sixteen}_workers`); the run service's own pool is already
covered by `runs_determinism.rs` `determinism_under_workers`. The TypeScript codegen drift check is now blocking: the new `codegen-drift-frontend` CI job
checks out `honba-frontend` beside `honba` and runs `make check-schema-ts check-client` (E9-S8 /
E11-S6). This also corrects the v7 "still open" list below: E5-S6, E5-S9, E10-S8 and E11-S6 have
shipped and are marked DONE.

v7 changes (2026-10-14): a status refresh against `main` (HEAD 102110b) after the v6 snapshot and the R1-R6 critical path
were found stale. **E2 Safe engine is now complete** — E2-S1 rest (`2fadc9f`), E2-S2 risk stage (`3455467`..`dcd2a22`,
`b397eff`), E2-S3 rest (`c90dd92`), E2-S4 rest (`17bce15`), E2-S6 (`cd32017`..`084841d`, `9dc5998`), E2-S7 (`9977f87`),
E2-S9 (`efdd717`), E2-S10 (`508316f`), E2-S11 (`51e5650`) — and **E3-S2/S3/S4/S5/S6/S7 are shipped**, so the
`E2-S6 -> E2-S7 -> E3-S7` critical path is done. E11-S3 rest is complete (`NOT_IMPLEMENTED_ENDPOINTS` is empty; `7298373`).
Also shipped since v6: E1-S4 (`9a7731a`), E1-S5 (`0df5b75`, `40ae1ec`), and wire/simulator trailing-stop support (`6e01d81`,
`4e2fbb4`). Still open: the pull-based and optional epics (E7, E8,
E10-S8, E11-S6) and the deferred agent stories (E5-S6, E5-S9). v6 changes are kept below for history.

v6 changes: E10-S7 DONE (commits edb4921, 91bff5b, 2c10630; ADR 0015 implemented); E3-S1a DONE, all three chunks (ADR 0016, commits
b12e5ba..c71424e: chunk 1 b12e5ba..1b2f6ce, chunk 2 d1299d3..c61d578, chunk 3a 9ced8d8..a6ffef5, chunk 3b 2c02b74..c71424e). Also done since v5 (see 1.1): cancel-time rule, rejection-queue hardening, IST timestamps, typed registry, SDK
capabilities/schema, WASM hardening, ADR 0014/0015, golden vectors validated against the schema, honba-docs chunks.
E5-S5 done (2026-10-11): AI strategy verifier (ADR 0022).
E11-S8 done (2026-10-11): Minimal auth (Bearer token, scope map, file token store, audit log) (ADR 0023).
Runtime trading state done (2026-10-15): `GET`/`PUT /trading-state` change the operator state of a running server (ADR 0018 decision 7), scope `operator` (ADR 0023, fifth scope); a persisted operator journal remains open.
E5-S10, E5-S11 added (2026-10-11): In-process Llama runtime (GBNF constraints) and financial domain alignment (SFT + RLVR compiler rewards).
E5-S10/S11 done (2026-10-12): GBNF→LLM grammar, local llama-cpp runner, synthetic dataset, QLoRA scaffold, GRPO compiler reward (`c9579d2`, `827627f`).
E5-S3 done (2026-10-13): canonical route→scope map (`honba_api_rest::ROUTE_SCOPES`) with the drift guard
(`scope_for_every_endpoint`, `drift_test_fails_on_new_route`) and a Python mirror in `honba.api.auth`.
E5-S2 done (2026-10-13): trust envelope (`honba.ai.trust.wrap_untrusted` / `render_for_llm` /
`extract_from_llm_rendered`) wraps broker/LLM/instrument text with an origin, content hash and
`untrusted=True`; the MCP gateway marks free-text result fields
(`wraps_untrusted_text` / `mcp_tool_output_marked`, commit `d6eb37c`).
E6-S5 done (2026-10-14): indicator render contract (`honba_indicators::render` — pane, one plot per
output, theme colour roles, zones, sparse flip signals) embedded in the WASM catalog
(`honba_api_wasm::indicators::list_indicators_json`) so the chart consumes it with one source of
truth.
E4-S6, E4-S7, E4-S8 done (2026-10-11): Overfitting validation gates (DSR, PBO, CSCV, holdout, MC, cost-stress), chunked streaming datasets, and session MTF aggregation (ADR 0024, commit e7da809).
E1-S2 done (2026-10-11): F&O instruments & grammar (india::fno), OptionType, right-to-left resolution, acceptance suite, Python bindings and stubs.
E6 batch 4, E1-S3, E3-S3 done (2026-10-12): plain-sum indicators O(1) (`00c75af`, `6f23282`); InstrumentMaster dated snapshots over a Parquet
catalog (`a3d09c7`, `a208daf`); market CostSchedule wired into simulator fills with effective-dated schedules and `edge_after_costs` (`9b8b64f`, `cf69d81`).
E1-S4/S5/S6, E3-S2/S4/S5/S6/S7, E5-S10/S11 done (2026-10-12): product/TIF enums + adapter mappings; session/rate/secret store; feed connector
contract; seeded fill/latency; margin + MIS square-off; target-position smart order + serialisation; lot/freeze splitter; sandbox paper-on-live;
GBNF + local LLM runner; synthetic dataset + QLoRA + GRPO compiler reward (commits `9a7731a`, `0df5b75`, `bc3b20b`, `695a549`, `1ffa180`, `599fb22`,
`6f8d509`, `71de25f`, `c9579d2`, `827627f`).
The two superseded documents are kept in `docs/archive/` for history and are not updated.

Story verdicts come from the 2026-10-06 audit (a scratch file, `ROADMAP_AUDIT.md`, model output, not kept in the repo) after a spot-check
against code. The audit's table
had 74 story rows (its prose said 70, its summary 69) and about 20 verdicts or evidence claims were corrected; see Appendix A. Treat the audit as a lead, this file
as the record.

---

## 1. Status snapshot (refreshed 2026-10-14; original as of 2026-10-06)

Counts are over the 74 stories the audit tracked (E0-S1..S8, E1-S1..S6, E2-S1..S11, E3-S1..S7, E4-S1..S8, E5-S1..S9, E6-S5, E9-S1..S8,
E10-S1..S8, E11-S1..S8). E6 batches 1-2, E7 and E8 are tracked separately below. The v7 refresh re-verifies every row against the
`main` tree and git log; the total is over the 74 tracked stories only (E7/E8 rows are extra).

| Epic | Done | Partial | Not started | One-line state |
|---|---|---|---|---|
| E0 Contract spine | 8 | 0 | 0 | Complete: wire types, codegen, versions, money, market packs, manifest, `honba verify` |
| E1 Instruments and adapters | 6 | 0 | 0 | Adapter Protocol, contract suite, F&O grammar, dated master, session/rate/secret, feed connector, product/TIF tables shipped |
| E2 Safe engine | 10 | 0 | 1 | Risk stage, order FSM, reconciliation, cache, staleness breaker, durable ledger and kill switch all shipped; E2-S8 dropped |
| E3 Execution realism | 7 | 0 | 0 | Bar-fill (L1), Rust/Python next-open, cost-schedule fills, fill/latency, margin/MIS, target-position, lot/freeze, sandbox shipped; **L2 core, Python parity, conformance (E3-S1b-1/2/3) and differential oracle (E3-S1d) shipped per ADR 0030** |
| E4 Research pipeline | 8 | 0 | 0 | Complete: journal v1, cache, hyperparameters, CLI/REST runner, look-ahead, sweep fitness, overfitting gates, streaming datasets, MTF aggregation |
| E5 Agent and API | 9 | 0 | 0 | Complete: verifier, MCP server, scopes, approval queue, typed decisions, webhook ingestion (S6), report builder (S9), GBNF+LLM, synthetic+QLoRA+RLVR — no deferred stories |
| E6 Indicators | 1 | 0 | 0 | Batches 1-4 (O(1) rolling, plain sums) and the render contract (S5) done |
| E7 Scale and ops | 0 | 0 | 5 | Pull-based; E7-S4 is superseded by `honba-sweep` (E10-S5); tracked separately from the 74 |
| E8 Multi-leg | 0 | 0 | 4 | Optional; not scheduled (open decision D13); tracked separately from the 74 |
| E9 Frontend and AlgoDesigner | 8 | 0 | 0 | Complete: StrategyGraph v1 + node catalogue, graph<->code, designer verify/run, AI assistant hook, ops console, result views, generated client + types (commit `4e15416`, frontend `7050c49`, ADR 0029, D16) |
| E10 Async core | 8 | 0 | 0 | Complete: ports, shell, engine output, Arc dataset, sweep, determinism test, runtime bridge (ADR 0015) and the bhavcopy Rust connector (E10-S8) |
| E11 Versioned API | 8 | 0 | 0 | Complete: read API, SDK, WASM, SSE streaming, write endpoints, auth, and the generated typed client (E11-S6) |
| **Total (74)** | **73** | **0** | **1** | |

### 1.1 What is shipped (evidence)

| Area | Evidence |
|---|---|
| Layering and renames, PyO3 isolated in `honba-py` | ADRs 0001-0004, `scripts/dependency_graph.py` (also enforces async isolation and WASM purity), CI job |
| E0-S1 `honba._honba` crate + stubs | `crates/honba-py`, CI wheel/stubtest (handoff: stubtest 0 errors) |
| E0-S2 canonical wire types, golden vectors | ADR 0006 (decision 1 superseded by 0014), `schema/golden/`, commit 78f98f6 (128 vectors validated against the generated schema) |
| E0-S3 strategy contract parity | ADR 0008, `schema/conformance/strategy_contract.json`, commits 3d04774..0c888ac |
| E0-S4 config schema, E0-S5 versioning, codegen | ADR 0012, ADR 0014, `crates/honba-codegen`, `crates/honba-config`, `make check-codegen-ci` (CI blocking) |
| E0-S6 integer money | ADR 0011 (per-currency minor units, 2^63 guard), `entities` money |
| E0-S7 market packs | ADR 0005, `crates/honba-market` (`india` feature, `null` pack, shared contract suite) |
| E0-S8 manifest, IR, `honba verify` | `crates/honba-strategy/src/{manifest,ir}.rs`, `python/src/honba/strategies/{manifest,verify}.py`, `POST /strategies/verify`, `POST/GET /strategies` (commits 849fe80, f8ad58a) |
| E1-S1 adapter contract | ADR 0010, `python/src/honba/adapters/{contract,testing,registry}.py`, commit b0f4f0e (capability-typed registry). A second real adapter passing the suite is still open |
| E2-S5 command channel | `EngineOutput` applied by `Engine`, fills fed back as `OrderFilled` events (commit daf12d5, `crates/honba-engine/src/engine.rs`) |
| E10-S1 ports | `crates/honba-ports` (feed, execution, master, clock, sink, secret, snapshot, bar/quote/depth readers), commit 1ed5eb8 |
| E10-S2 async shell | `crates/honba-async` (`EngineHandle`, `LiveClock`, `HistoricClock`), commit 49ffb32 |
| E10-S3 engine output | commit daf12d5 |
| E10-S4 shared dataset (DONE; streaming half is E4-S7) | `honba-feed` `Dataset`, `ColumnarSlice`, `DatasetFeed` (commit c7ef86b); chunked streaming shipped as E4-S7 |
| E10-S5 sweep | `crates/honba-sweep` (one engine per trial, `spawn_blocking`, results in trial order), commit fb64146 |
| E10-S7 Python runtime bridge | ADR 0015 implemented: `honba.event_loop`, `honba.async_run`; commits edb4921, 91bff5b, 2c10630 |
| E3-S1a next-open Rust port (chunks 1-3) | ADR 0016; chunk 1 b12e5ba..1b2f6ce, chunk 2 d1299d3..c61d578, chunk 3a 9ced8d8..a6ffef5, chunk 3b 2c02b74..c71424e. Rust core, PyO3 binding and Python delegation shipped |
| Cancel-time rule | ADR 0008 decision 13 addendum, commit 1c50fc7 |
| Rejection-queue hardening | commits 2620b12, e1f6b76, 081fd12, 53a3ef7 |
| IST timestamps | commit 31510cb |
| SDK capabilities and schema | commit f19a301 |
| WASM hardening | commit 101f81f |
| ADR 0014 (schema/codegen from Rust), ADR 0015 (explicit runtime) | `docs/adr/` |
|---|---|
| E11-S4 SSE streaming | ADR 0021, `crates/honba-api-rest/src/streams.rs`, `crates/honba-api/src/responses.rs::StreamFrame`, `crates/honba-messages/src/endpoints.rs` `GET /backtests/{id}/events`, `honba._honba.api_request_stream`, `python/honba/client/transport.py` `stream()`, `honba.client.Client.stream_backtest()`, `schema/conformance/stream_frames.json`, commits 523bca5..eac57f6 |
| honba-docs chunks | documentation chunks in the `honba-docs` repo (not itemised here) |
| E10-S6 determinism under threads | `crates/honba-sweep/tests/sweep.rs` `determinism_under_threads_{one,four,sixteen}_worker(s)` compare journal and ranking across thread counts |
| E10-S6 extended to runs | `crates/honba-async/tests/shell.rs` `determinism_under_threads_{one,four,sixteen}_workers` compare the audit trail byte-for-byte at tokio `worker_threads` 1/4/16 (`EngineHandle` runs the engine as a task, so it really uses those threads); `crates/honba-api-rest/tests/runs_determinism.rs` `determinism_under_workers` covers the run-service pool |
| Stable codes on every surface (§7 gap (d)) | WASM first: `honba-api-wasm` screener + replay refusals are `reason: message` pairs (ADR 0012 rule 4) reusing the REST `ErrorCode` for invalid input and the evaluator's own reasons; typed `IndicatorError`/`WasmScreenerError`/`ReplayError` `reason()` tables + `wasm_error_codes.rs` |
| Blocking TS codegen drift | `.github/workflows/ci.yml` `codegen-drift-frontend` checks out `honba-frontend` beside `honba` and runs `make check-schema-ts check-client`; wired into `ci-ok` |
| E11-S1 envelope and registry | `crates/honba-api`, `crates/honba-messages/src/endpoints.rs` (22 routes, `WRITE_PATHS`), `GET /capabilities` (cf1366e) |
| E11-S2 timestamps as strings | ADR 0012 rule 4, commits 4006ff1, 9530251 |
| E10-S8 NSE bhavcopy Rust connector (DONE) | `honba-feed::bhavcopy::BhavcopyConnector` (D10 default, ADR 0028): parses the three NSE CSV shapes to 1-day bars at 09:15 IST, filters to the subscriber's instruments, drives an engine run; tests `bhavcopy_connector_parses_fixture`, `bar_ts_is_ist_open_independent_of_local_tz` / `bhavcopy_feed_into_engine_run` (commit `3b230b9`) | honba |
| E9-S8 / E11-S6 generated typed TS client (DONE) | `honba-codegen` renders `client.ts` (`HonbaClient`, `request<T>`, SSE helper) from the registry (`Artifact::Client`, `make client`/`make check-client`); frontend pins it with `client.test.ts` and ships lint/typecheck/test scripts; `domain.ts` refreshed (frontend `ee2e1cb`, honba `60373f0`) | honba + honba-frontend |
| E11-S3 REST read API | ADR 0013; `instruments`, `quotes` (derived from bars), `bars` (cap 100k rows), `depth` (none), `strategies`, `screener/scan`; `honba serve`; error envelope for unknown routes (e24b806); `POST/GET /backtests`, `GET /backtests/{id}/journal`, `GET /journals/{id}` (ADR 0017, q2c; sweeps and trading routes built, `NOT_IMPLEMENTED_ENDPOINTS` empty); `honba serve --journals-dir` |
| Python SDK | `honba.client` (`HttpTransport`, `InprocTransport` via `honba._honba.api_request`), parity tests `python/tests/integration/test_client_parity.py` |
| E4-S1 journal schema v1 and result cache | ADR 0020, `JournalHeader`/`JournalV1` NDJSON roundtrip (`honba-engine`), `CacheKey`/`ResultCache` (`honba-api`), `BacktestExecutor` caching with unit tests `journal_schema_roundtrip`, `cache_key_changes_with_costs` and integration test `rerun_hits_cache` |
| E4-S4 look-ahead truncation property | `crates/honba-strategy/src/tests/look_ahead.rs` `truncation_property` and `truncation_property_detects_a_peeking_strategy`; Python `test_prefilter_vs_event_driven_fixture.py` |
| E4-S6 overfitting gates | ADR 0024, `crates/honba-analytics/src/validation.rs`: DSR, PBO (CSCV), walk-forward, holdout, block-bootstrap MC, cost-stress; `ValidationBlock` in backtest/sweep results; unit + integration tests `dsr_known_values`, `bootstrap_seeded`, `result_carries_validation_block` |
| E4-S7 streaming datasets | ADR 0024, `crates/honba-feed/src/streaming.rs`: `StreamDataset`, `StreamingChunkIterator`, chunk manifests, dataset IDs; unit + integration tests `chunks_cover_slice`, `sweep_over_streamed_dataset` |
| E4-S8 session MTF aggregation | ADR 0024, `crates/honba-indicators/src/mtf.rs`: `MtfAggregator`, `CalendarSession`, HTF bar emission on period completion; unit + integration tests `htf_bar_only_when_complete`, `mtf_strategy_run` |
| E6 batch 4 plain-sum indicators | `python/src/honba/strategies/indicators/{moving_average/vwma,volume/chaikin_money_flow,volume/money_flow_index,trend/vortex,momentum/chande_momentum,momentum/ultimate_oscillator,volatility/chaikin_volatility}.py` now O(1) via `_rolling.RollingSum`; `python/tests/unit/test_indicators_rolling_batch4.py` (old-oracle + Fraction), hot-path guard extended; commits 00c75af, 6f23282 |
| E6-S5 indicator render contract | `crates/honba-indicators/src/render.rs` (`Presentation`/`Plot`/`Zone`/`ColorRole`, `catalog_names`, `presentation`, `validate`); serialized into the WASM catalog `honba_api_wasm::indicators::list_indicators_json`; unit `output_len_matches_input` etc. (`crates/honba-indicators/src/tests/render.rs`), integration `chart_consumes_a_valid_presentation_for_every_indicator` (`crates/honba-api-wasm/tests/render_conformance.rs`) |
| E2-S6 OrderState FSM, single event drain, adapter + sim conformance (DONE 2026-10-09) | ADR 0019. (a) `honba-messages` `OrderState`/`OrderEvent`/`IllegalTransition`/`VenueOrderId`/`cancel_requested` + `SCHEMA_VERSION` 4 (`OrderPartiallyFilled`/`OrderCancelRequested`/`OrderExpired`, `OrderAccepted.venue_order_id`); (b) `honba-engine::ExecutionEngine::drain_events`, `Engine::acknowledge_events`, order store, `working_exposure`, `LegacyDrains`/`LegacyPortEvents` shims, every sim in `honba-sim`/`honba-py` and `StrategyRunner`; (c) `OrderReport.venue_order_id` + `honba.adapters.order_events.reduce_order_report` (`ReportReduction`) + `FakeAdapter` conformance; (d) `schema/conformance/order_state.json` (12 scenarios, 2 profiles) replayed on `BarFillEngine`/`PaperExecution`/`NextOpenSim`/`ScriptedExecution` (`crates/honba-sim/tests/order_state_conformance.rs`), `NativeSim` (`crates/honba-py/tests/order_state_conformance.rs`), `NextOpenExecution`/FakeAdapter (`python/tests/integration/test_order_state_conformance.py::test_gateway_normalised_stream_replay`) | honba (+adapters) |
| E2-S2 risk stage (DONE) | ADR 0018; `crates/honba-risk` (`RiskStage`, `RiskCheck`, typed `RiskRefusal`, `RulesSource`, `RiskLimits`, `PriceField`), config `[risk]` section + live guard, golden `schema/conformance/risk_decisions.json`; unit `max_notional_refused`, `price_band_from_profile`, order-rate and property tests / integration `strategy_engine_risk_fills_refusal_in_audit`, `POST /orders` risk gate (`crates/honba-api-rest/src/tests/orders_risk.rs`) (commits `3455467`, `879eb9f`, `67e92ea`, `605b177`, `b397eff`, `dcd2a22`) | honba |
| E2-S7 reconciliation (DONE) | `crates/honba-engine/src/reconciliation.rs`; unit `missed_fill_detected` (`crates/honba-engine/src/tests/reconciliation.rs`), integration `crates/honba-engine/tests/reconciliation.rs` (commit `9977f87`) | honba |
| E2-S9 state cache (DONE) | `crates/honba-engine/src/cache/mod.rs` (`StateCache`, `CacheQuery`, `TrackedOrder`); unit `cache_apply_event` (`crates/honba-engine/src/tests/cache.rs`), integration `context_reads_from_cache` (`crates/honba-strategy/src/tests/context.rs`) (commit `efdd717`) | honba |
| E2-S10 feed-staleness breaker (DONE) | `honba-risk` `RiskRefusal::FeedStale` on the simulated clock; unit `stale_after_blocks_orders` (`crates/honba-risk/src/tests/staleness.rs`), integration `stale_feed_scenario` (`crates/honba-engine/tests/risk_gate.rs`) (commit `508316f`) | honba |
| E2-S11 durable risk state and idempotent fill ledger (DONE) | `crates/honba-risk/src/{durable,ledger}.rs` (`DurableRiskState`, `InFlightOrder`, `FillFingerprint`, `FillLedger`); unit `fingerprint_dedup`, `atomic_snapshot` (`crates/honba-risk/src/tests/durable_ledger.rs`), integration `reconnect_replay_no_double_count` (`crates/honba-engine/tests/durable_reconnect.rs`) (commit `51e5650`) | honba |
| E2-S1 rest / E2-S3 rest / E2-S4 rest (DONE) | Reducing enforced as reduce-only; `TradingState`/`Command::State` exposed in Python and CLI (commit `2fadc9f`; `python/tests/unit/test_trading_state_enforcement.py`, `test_reduce_only_two_orders_in_flight.py`, `test_risk_check_state.py`; `crates/honba-cli/src/tests/trading_state.rs`). Wall-clock-free domain check `no_system_time_in_domain_crates` (`crates/honba-testing/tests/purity.rs`) and `UnixNanos::now` removed (commit `c90dd92`). Audit replay `audit_replay_matches_state`, `run_then_replay_identical_positions` and `journal_writer_roundtrip` (commit `17bce15`) | honba |
| E3-S2/S4/S5/S6/S7 (DONE) | Seeded `FillModel`/`LatencyModel` (`seeded_slippage_reproducible`, `same_seed_byte_identical_fills`, `crates/honba-sim/tests/fill_latency_models.rs`); margin model + MIS square-off (`mis_run_flat_by_close`, `crates/honba-sim/tests/mis_square_off.rs`); target-position smart order (`target_delta_computed`, `target_position_run`); lot/freeze splitter (`slice_above_freeze`, `order_sliced_in_sim`); `SandboxAdapter` (`virtual_funds_isolated`, `sandbox_vs_backtest_same_fills`) (commits `695a549`, `1ffa180`, `599fb22`, `6f8d509`, `71de25f`) | honba |
| E1-S4/S5 (DONE) | Canonical `Product` enum + per-adapter mapping tables (`9a7731a`); session provider, shared token-bucket rate limiter and encrypted secret store (`0df5b75`, `40ae1ec`) | honba (+adapters) |
| E5-S6 webhook ingestion (DONE) | ADR 0026; `python/src/honba/api/webhooks.py` (`WebhookIngestor`, `WebhookConfig`, `TtlCache`, ordered stages); tests `python/tests/unit/test_webhooks.py`, `python/tests/integration/test_webhook_ingest.py` | honba |
| E5-S9 report builder with a fact snapshot and quality gate (DONE) | ADR 0027; `python/src/honba/ai/report.py` (`FactSnapshot`, `build_snapshot`, `QualityViolation`/`QualityReport`, `quality_gate`, `Report`/`build_report`/`as_markdown()`); quality gate rejects any number not in the snapshot; narrative is the only input wrapped as untrusted (`honba.ai.trust`); tests `python/tests/unit/test_report_builder.py`, `python/tests/integration/test_report_snapshot.py` | honba |
| E5-S10/S11 (DONE) | GBNF grammar and local llama-cpp GGUF runner (`c9579d2`) — `python/tests/unit/test_ai_gbnf.py::test_grammar_masks_invalid_tokens`, `python/tests/integration/test_ai_local_llm.py::test_ask_runs_offline_zero_syntax_errors`; synthetic financial dataset + QLoRA SFT + GRPO compiler reward (`827627f`) — `python/tests/unit/test_ai_alignment.py::test_benchmark_screener_accuracy_gt_90pct`, `::test_rlvr_rewards_compiler_pass` | honba |
| E11-S3 rest (DONE) | `NOT_IMPLEMENTED_ENDPOINTS` is empty (`crates/honba-api-rest/src/lib.rs`); no route returns 501. `POST/GET /backtests`, journal routes and `honba serve` run flags (commit `7298373`); sweeps (E4-S5); orders write ledger (E11-S7, commit `91ab361`); read routes, screener and strategies as before | honba |
| Trailing stop (wire + sim) | `OrderType::TrailingStop` with `trail_amount`/`trail_percent`, opt-in via `AdapterCapabilities.order_types` (`6e01d81`); full Rust/wire/simulator support — `BarFillSim` trails (high/low-water mark), `NextOpenSim` rejects it explicitly (`4e2fbb4`, `2568dab`, `381bbc4`) | honba (+adapters) |
| E1-S3 instrument master snapshots | `crates/honba-ports/src/master.rs` `DatedInstrument`, provided-default `snapshot(as_of)`/`resolve_as_of`; `crates/honba-feed/src/instruments.rs` `CatalogInstrumentMaster` over `{root}/instruments/YYYY-MM-DD.parquet`; tests `resolve_as_of_old_symbol`, `backtest_resolves_expired_contract` |
| E3-S3 cost schedule in fills | `honba-market::DatedCostScheduleSource`, `edge_after_costs`, `FeeBreakdown::total_bps`; `honba-sim::cost::ScheduleFillCost`, `NextOpenSim::with_schedule`, `BarFillEngine::with_schedule`; tests `charges_effective_dated`, `india_costs_in_backtest_match_python_costmodel` (golden) |
| E4-S5 sweep fitness & REST | `honba-sweep` `CalmarFitness`, `SortinoFitness`, `OmegaFitness`, `fitness_ranking_stable`; `POST /sweeps`, `GET /sweeps/{id}`, `GET /sweeps/{id}/journal` (422 per-trial); `crates/honba-api-rest/tests/rest_sweep.rs` |
| E11-S5 part 1 | `crates/honba-api-wasm`: `indicator_series`, `ohlc_indicator_series` (ATR, Williams %R, Stochastic), conformance vectors (d1c9db3, e9fd98e) |
| E6 batches 1-2 | commits 6372528, 510e0d4, efca4bd, 7c9a325, 6a91432 |
| Backtest foundation (Python) | `python/src/honba/session.py` `BacktestSession`/`BacktestResult`, `backtest/simulated.py` `NextOpenExecution`, date-aware India settlement (9cc171e), single-round costs (d38fe82) |
| E5-S5 AI strategy verifier | ADR 0022; `honba.ai.verification`: static AST scan (import allowlist, forbidden names/dunders), rlimit sandbox with audit hook, deterministic smoke, look-ahead truncation property; 45 unit + 37 integration tests; red-team corpus |
| E11-S8 Minimal auth | ADR 0023; `honba-api-rest::auth`: Bearer token + scope map (read:market, read:account, write:orders, research, operator); FileTokenStore (TOML, SHA-256 hashes, hot-reload); audit log (NDJSON); auth middleware; CLI `--tokens-file` / `--audit-log-file`; non-loopback warning suppressed when auth enabled |

Not shipped despite appearing in earlier drafts: any streaming route beyond `GET /backtests/{id}/events`. (E2-S6's `OrderState` FSM, client order ids, `FillModel`/`LatencyModel`, DSR/PBO/MC/walk-forward, `honba.event_loop` and `Instrument` option metadata have since shipped as their own entries above.)

---

## 2. Principles carried forward

1. **Contract first.** Every story starts with the artifact (Rust trait or message, schema, `.pyi`, ADR), then contract tests and golden
   vectors, then the reference implementation. Rust `honba-codegen` is the single generator of JSON Schema, OpenAPI, TS, `.pyi`, MCP
   schemas (ADR 0014); generated files are never edited; drift is a blocking CI check.
2. **Rust core, Python-first surface.** Rust owns deterministic hot paths (engine, simulator, risk, analytics, sweeps, indicators); Python
   is the user, SDK, adapter and research surface. Anything user-facing added in Rust ships a Python binding and stub in the same story.
   The Python API does not change when logic moves to Rust (next-open port is the live example).
3. **Sync kernel, async shell.** The event loop is synchronous and single-threaded; async and threads live at the edges and across runs
   (`docs/archive/plan.md` section 1, `honba-async`, `honba-sweep`). Same seed and data give a byte-identical journal at any thread count.
4. **Determinism.** Fixed seeds, simulated clock, recorded fixtures, no network or wall clock in tests; shared golden vectors for
   Rust/Python parity; backtest, paper and live share one event flow and one order-state machine (E2-S6 hard requirement).
5. **Workspace rules R1-R3 (root `CLAUDE.md`).** R1 TDD (failing test first, bug fixes start with a regression test, never weaken a test).
   R2 DDD (domain pure; infrastructure behind ports; adapters translate at the boundary; deps point inward, enforced by
   `scripts/dependency_graph.py`). R3 every change ships a named unit test and a named integration test (Rust `src/tests/` and `tests/`;
   Python `tests/unit/` and `tests/integration/`), plus the shared contract test for any port change.
6. **Research and AI first.** Typed, serializable, seeded, journaled outputs; reachable by library, CLI, REST and MCP with the same
   envelope and stable error codes; LLM and broker text is untrusted input, verified before use; AI-generated strategies pass the same
   validation as hand-written ones.
7. **Market-neutral core.** India rules live only in the `india` pack of `honba-market`; no core crate names a pack.
8. **Honesty over breadth.** Unbuilt routes answer 501 `not_implemented`; known gaps are `#[ignore = "known gap: ..."]` tests with reasons.

Licences: OpenAlgo is AGPL-3.0 (clean-room only, behaviour not code); QuantDinger backend Apache-2.0, its frontends have separate
licences (ideas only); Nautilus LGPL-3.0, barter MIT, arbkit Apache-2.0 or MIT (verify before any reuse). A clean-room log for
OpenAlgo-inspired stories is still unwritten (D15).

---

## 3. Re-evaluated borrowed ideas

Decisions: **ADOPT** (build, scheduled), **KEEP** (already adopted and shipped), **DEFER** (premise holds, not now), **DROP** (premise no
longer holds). What changed since v4: the core is async-ported with a sync kernel; REST and WASM exist; the Python SDK has two
transports; schemas and MCP tool metadata are generated from Rust; adapters stay Python (ADR 0010); the simulator and costs are still
thin; no live feed or broker exists in Rust.

### 3.1 OpenAlgo (AGPL, behaviour only)

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Adapter Protocol, capability manifest, lazy registry, contract suite | KEEP | Built (ADR 0010, typed registry b0f4f0e); second-adapter proof still owed | E1-S1 |
| Canonical typed models, shared golden vectors | KEEP | Core of E0; 128 vectors validated against generated schema | E0-S2 |
| Strict symbol grammar with round-trip tests | ADOPT | Trait exists but `india::fno` is an 8-byte stub and `Instrument` has no expiry/strike; needed before options and the instrument master | E1-S2 |
| Instrument master with dated snapshots | ADOPT | Only the `InstrumentMaster` port exists; backtests need resolve-as-of; build over the Parquet catalog, not a broker table | E1-S3 |
| Table-driven product and price-type mapping | DONE | Canonical `Product` enum + per-adapter JSON mappings in `honba-messages`; `mapping_round_trip_property` / `adapter_rejects_unsupported_combo` | E1-S4 |
| Smart order (target position, per-symbol lock) | DONE | Target-position delta + per-instrument serialisation in `honba-sim` | E3-S5 |
| Lot and freeze-quantity splitter | DONE | Lot/freeze splitter in `honba-sim::lot` | E3-S6 |
| Sandbox behind the same interface (no global mode flag) | DONE | `SandboxAdapter` paper on live data, same matching core and FSM | E3-S7 |
| Webhook pipeline (stages, idempotency, cool-off) | DONE | Built (ADR 0026): ordered fail-closed pipeline, hashed token, opaque auth failures, size cap, idempotency + cool-off TTL caches; premise (auth E11-S8 + risk E2-S2) met | E5-S6 |
| MCP metadata, toolsets, read-only kill switch, scope map | ADOPT (changed) | Tool schemas are now generated from the endpoint registry (`schema/mcp/mcp_tools.json`, `readOnlyHint` from `WRITE_PATHS`); remaining work is scope map plus drift test and a thin Python MCP server | E5-S1, E5-S3 |
| Trust envelope for untrusted broker text | ADOPT | Nothing built; do with MCP server | E5-S2 |
| Approval queue for placements | ADOPT | Gated on risk stage; cancel/modify bypass | E5-S4 |
| Market-data normaliser and silent-feed watchdog | DEFER | No feed exists; ports only | E1-S6 |
| Visual flow builder with code twin (clean-room) | DONE | Shipped (ADR 0029, commit `4e15416` + frontend `7050c49`); graph editor model, codegen, recogniser, verify/run, assistant hook, ops console, result views | E9-S1..S7 |
| 36-broker breadth, Flask/ZeroMQ, key in body, global analyzer flag | DROP | Unchanged | none |

### 3.2 Nautilus (LGPL, ideas only)

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Order request/ack state machine, client order ids | ADOPT | Only `OrderStatus` enum and an `OrderRejection` queue exist (ADR 0008 decision 13); no FSM, no ack events | E2-S6 |
| Reconciliation on start/reconnect | DEFER | Needs live adapter, cache (E2-S9), FSM | E2-S7 |
| Component lifecycle FSM | DROP | `EngineHandle` supervisor and `Command::Stop` in `honba-async` cover start/stop/health; no second consumer | was E2-S8 |
| Pluggable seeded fill/latency/margin models | DONE | `FillModel`/`LatencyModel` traits + margin model in `honba-sim` | E3-S2, E3-S4 |
| Data catalog with streaming Parquet batches | ADOPT (smaller) | `Dataset`/`ColumnarSlice` cover shared reads; remaining value is chunked streaming and dataset ids | E4-S7 |
| Cache persistence, crash-only restart | DEFER | No live sessions yet | E7-S3 |
| Message bus and engine replacement | DROP | Unchanged; recorder tap is `AuditLog` plus streaming (E11-S4) | E7-S5 |

### 3.3 barter-rs

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Audit stream with replicable state | KEEP | `AuditLog` records every dispatch, fill, state change; replay test still missing | E2-S4 |
| `Clock` trait live vs historic | KEEP | `honba-ports::Clock`, `LiveClock`, `HistoricClock`; wall-clock-in-domain check still missing | E2-S3 |
| Trading-state kill switch | KEEP | `TradingState`, Halted refuses orders and is audited; Reducing not enforced, no Python/CLI exposure | E2-S1 |
| Risk stage with typed refusals | ADOPT | Not built; top priority, gates every write path | E2-S2 |
| Command channel and `EngineOutput` | KEEP | Shipped (daf12d5) | E2-S5 |
| Terminal/Unrecoverable errors, reconnecting stream | ADOPT | Typed port error taxonomy exists (1ed5eb8); reconnect/backoff waits for first feed | E1-S6 |
| Indexed instrument state | DEFER | No profile justifies it | E7-S2 |

### 3.4 QuantDinger (Apache backend; frontend ideas only)

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Strategy manifest compiled by `verify` | KEEP | Shipped; `CompiledStrategy.id` is `sha256` of canonical manifest | E0-S8 |
| One-line param declarations, `hyperparameters()` | ADOPT | Manifest has no range/sweep space yet; sweep takes hand-built `TrialParams` | E4-S2 |
| Target-position intents with reason, client order id | ADOPT | With the FSM | E2-S6, E3-S5 |
| `not_modelled` assumptions list | ADOPT | Field exists untyped on `BacktestResponse`; Python `BacktestResult` has none | E4-S3 |
| Cost-stress rerun, block-bootstrap Monte Carlo, DSR, PBO, holdout | ADOPT | Nothing compiled; build in Rust `honba-analytics` over the journal | E4-S6 |
| Backtest cache keyed by hash | ADOPT | Hash pattern proven in strategy catalog; needs journal and cost-model version | E4-S1 |
| Evaluation never submits orders (intents only) | KEEP | True of `Strategy` contract; enforced end to end once risk stage lands | E2-S2 |
| Computed vs broker PnL reconciliation, drift tracker | DEFER | Needs live | E2-S7 |
| AI decision filter (veto/size-down), report builder | DEFER | Needs verifier, journal and trust envelope first | E5-S7, E5-S9 |
| Per-source circuit breaker, rate limiter | DEFER | No remote sources yet; Python adapters own it when built | E1-S5 |
| AST/allowlist/subprocess verification of AI code | ADOPT | `ai/verification/` is empty; blocks any AI authoring path | E5-S5 |
| Indicator render contract | KEEP | Built (E6-S5): `honba_indicators::render` descriptor embedded in the WASM catalog; consumed by WASM browsers, MCP and alerts | E6-S5 |
| Ops console, per-run mode | DONE | Shipped with E9 (frontend ops panel + core helpers, `7050c49`) | E9-S6 |
| Celery/Redis/Kafka, billing, multi-tenant SaaS, crypto breadth, Vue | DROP | Unchanged | none |

### 3.5 Jesse

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Declarative `hyperparameters()` + seeded sweep + fitness | ADOPT | Sweep runner exists with `SharpeFitness` only (Calmar/Sortino/Omega absent) | E4-S2, E4-S5 |
| Look-ahead protection by construction + property test | ADOPT | No such test; matters most for AI-generated strategies | E4-S4 |
| Optuna as dependency | DEFER | Rust seeded sampler fits the sync-kernel design; Optuna only as optional Python extra (D9) | E4-S5 |
| Multi-timeframe aggregation | DEFER | No demand; needs calendar bucketing | E4-S8 |

### 3.6 arbkit

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Integer money, conservative rounding | KEEP | ADR 0011 shipped | E0-S6 |
| Costs netted into price first (`edge_after_costs`) | ADOPT | Rust `CostSchedule` is not used by `honba-sim`; Python path uses `CostModel` via `fill_costs_from_model` | E3-S3 |
| Depth survival discount shared by signal and fill | DEFER | Needs L2 matching and depth data (`/depth` returns none) | E3-S2 |
| Feed staleness breaker | DEFER | Needs feed and risk stage | E2-S10 |
| Durable risk state, idempotent fill ledger | DEFER | Needs live and FSM | E2-S11 |
| Opportunity contract, multi-leg sizing/execution, reference arb strategies | DEFER | Arbitrage is not a decided product goal (D13); only E3-S3 and E2-S10 ideas stay in scope | E8 |

### 3.7 Matching-core study (differential oracle, L3 book)

| Idea | Decision | Reason | Lands |
|---|---|---|---|
| Naive reference matcher vs optimised sim | ADOPT (DONE) | `crates/honba-sim/tests/l2_oracle.rs`: independent O(n^2) matcher vs `L2Sim` on seeded streams | E3-S1d |
| L3 full-book replay | DROP | No full-depth data source; broker feeds give a few levels; would add false precision | none |

---

## 4. Milestones re-sequenced

Done work is removed. Sizes: S (<=1 day), M (2-4 days), L (split before start). Owner repo is `honba` unless stated.

| Milestone | Theme | Stories | Exit criteria |
|---|---|---|---|
| R1 Safe writes | Nothing can place an order without a gate | E2-S2, E2-S1 rest, E2-S3 rest, E2-S6(a) | Risk stage with typed refusals, Reducing enforced, wall-clock lint, `OrderState` FSM in `honba-messages` |
| R2 Runs | A backtest is a first-class server object | ADR-Run, E3-S1a (next-open Rust port), E4-S3, E4-S1, E11-S3 rest (backtests, sweeps, journals), E4-S5 | `POST /backtests` returns `run_id`; result carries `assumptions.not_modelled`; sweep over REST; journal v1 |
| R3 Live-shaped runtime | Python and Rust share one runtime; push updates | E11-S4 (E10-S7 done) | `honba.event_loop` owns one runtime (done); SSE streams run progress with resume |
| R4 Trustworthy research | Results that resist self-deception | E4-S2, E4-S4, E4-S6, E4-S7, E6 batches 3-4 | Look-ahead property test; DSR/PBO/holdout/MC/cost-stress gates in result |
| R5 Agent surfaces | Safe LLM and automation access | E5-S5, E5-S2, E5-S1 server, E5-S3, E5-S4, E5-S7, E5-S10, E5-S11, E11-S7 | MCP server from generated schemas; scope drift test; verifier; in-process tiny LLM with GBNF & financial alignment; write routes gated |
| R6 Live-ready | Broker, paper on live data | E1-S2..S6, E10-S8, E2-S7, E2-S9..S11, E3-S2..S7 | Instrument master, feed connector, reconciliation, L2 simulator, sandbox adapter |
| R7 Frontend | Capability-driven apps | E11-S5/S6 rest, E9 | WASM replay, generated client, AlgoDesigner |
| R8 Optional | Scale and arbitrage | E7, E8 | Only if profiling or product decision demands |

R6 and R7 can run in parallel with R4 once R2 exits. R5 needs R1 for write tools and R2 for runs.

**Milestone status (2026-10-14):** R1 (Safe writes), R2 (Runs), R3 (Live-shaped runtime), R4 (Trustworthy research) and R5 (Agent surfaces) are **complete** (E5-S6 webhook ingestion and E5-S9 report builder shipped). R6 (Live-ready) is **complete** (the L2 simulator,
E3-S1b/S1d, shipped per ADR 0030); E10-S8 shipped the bhavcopy Rust connector. R7 (Frontend) is **complete**
(E9-S1..S7 shipped: core graph/code/verify/assistant in `4e15416`, React Flow canvas and views in frontend `7050c49`).
R8 (Optional) stays unscheduled.
Follow-up (2026-10-15): the ADR 0018 price collar now sees the quote touch in the Rust `Engine` and `StrategyRunner`. Since then: `BacktestSession` replays ticks with `fill="l2"`, and the wire `execution.fill_model` has an `l2` variant (schema regenerated). Still open: a tick source for the REST `BacktestExecutor` (a wire run with `fill_model=l2` is refused with reason `l2_requires_ticks` until then).

**Critical path:** E2-S2 -> E11-S7 (write endpoints) and E5-S4 (all DONE); ADR-Run -> E4-S3 -> `POST /backtests` -> E4-S5 -> E11-S4 -> E9-S7
(all DONE in `honba`, E9-S7 in `honba-frontend`); E3-S1a (next-open Rust) -> E3-S2 -> E3-S7 (all DONE; E3-S1b/S1d DONE per ADR 0030);
E10-S7 (done) gates Python callers of any async Rust API; E2-S6(a) -> E2-S6(b..d) -> E2-S7 -> E3-S7 (all DONE). What remains is off the
old critical path: the pull-based E7 and optional E8 epics.

### E1 Instruments and adapters

| ID | Scope | Size | Deps | Acceptance (unit / integration) | Owner |
|---|---|---|---|---|---|
| E1-S1b (DONE) | Second adapter passes the contract suite on recorded fixtures; migration note per adapter. dhan + zerodha certified (`honba-adapters` `ea61c47`, `test_contract_suite[dhan]`, `test_contract_suite[zerodha]`, `test_adapter_wiring_recorded_fixture`, `MIGRATION.md`); the six remaining adapters are scaffold-only future work | M | E1-S1 | `test_contract_suite[dhan]` / `test_adapter_wiring_recorded_fixture` | honba-adapters |
| E1-S2 (DONE) | `Instrument` gains optional expiry, strike, option type, underlying; `india::fno` grammar parse/format (equity, futures, options, index) | M | E0-S7 | `symbol_round_trip_property`, `ce_suffix_ambiguity`, `weekly_vs_monthly` / `catalog_resolves_fno_symbol` through market registry; Python binding + stub | honba |
| E1-S3 (DONE) | `InstrumentMaster` impl over Parquet catalog: `snapshot(as_of)`, effective-dated lot/tick/freeze | M | E1-S2 | `resolve_as_of_old_symbol` / `backtest_resolves_expired_contract` | honba |
| E1-S4 (DONE) | Product/price-type/TIF table mapping per adapter. Canonical `Product` enum + per-adapter JSON mappings in `honba-messages` (commit `9a7731a`) | S | first real adapter | `mapping_round_trip_property` / `adapter_rejects_unsupported_combo` | honba (+adapters) |
| E1-S5 (DONE) | `SessionProvider`, token-bucket rate limiter, encrypted secret store (Python side) (commits `0df5b75`, `40ae1ec`) | M | E1-S1b | `token_expiry_fake_clock`, `bucket_refill` / `limits_hold_across_workers`, `no_secret_in_logs` | honba (+adapters) |
| E1-S6 (DONE) | Feed connector contract: reconnect, backoff, gap markers, watchdog, Terminal/Unrecoverable | M | E10-S1, E2-S3 | `backoff_schedule`, `terminal_error_stops` / `simulated_feed_drop_emits_gap_marker`, `feed_into_engine_run` (commit `bc3b20b`) | honba |

### E2 Safe engine

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E2-S1 rest (DONE) | Enforce Reducing as reduce-only; expose `TradingState` and `Command::State` in Python and CLI; state change visible in audit (commits `19e7a1c`, `2fadc9f`) | S | none | `reduce_only_two_orders_in_flight` (`crates/honba-engine/tests/risk_gate.rs`, `python/tests/integration/test_reduce_only_two_orders_in_flight.py`), `test_trading_state_enforcement.py`, `state_command_halts_the_engine` (`crates/honba-async/tests/shell.rs`) | honba |
| E2-S2 (DONE) | New `honba-risk` crate (pure): `RiskCheck` -> approved plus typed refusals; rules max notional, order rate, lot size, price band from `MarketProfile`; refusals are events and audited; config schema; spec: ADR 0018 (accepted), incl. `RulesSource` port, `TickSize`/typed lot rules, working-exposure reduce-only (commits `3455467`..`dcd2a22`, `b397eff`) | M | E2-S1, E0-S7 | `max_notional_refused`, `price_band_from_profile` (+ property tests) / `strategy_engine_risk_fills_refusal_in_audit`; golden `schema/conformance/risk_decisions.json` | honba |
| E2-S3 rest (DONE) | Wall-clock-free domain check (lint or test) in CI; `UnixNanos::now` removed (commit `c90dd92`) | S | none | `no_system_time_in_domain_crates` (`crates/honba-testing/tests/purity.rs`) / dependency-graph job | honba |
| E2-S4 rest (DONE) | Replay: rebuild final state from `AuditLog`; journal writer (commit `17bce15`) | M | E2-S3 | `audit_replay_matches_state`, `journal_writer_roundtrip` / `run_then_replay_identical_positions` | honba |
| E2-S6 (DONE 2026-10-09) | (a) `OrderState` FSM and client order ids in `honba-messages`; (b) `ExecutionEngine` emits ack/reject/fill/cancel-ack events replacing `drain_*`; (c) adapter contract emits same (event form beside the query form, snapshot-diff reducer — Appendix A); (d) sim and paper identical. ADR 0019 accepted. Evidence: (a) `crates/honba-messages/src/orders/state.rs`, `SCHEMA_VERSION` 4, `crates/honba-entities/src/execution.rs`, `python/src/honba/entities/order_state.py` / `python/src/honba/strategies/execution.py` (commits `cd32017, bb325ba, 11d693b, 209cba8`); (b) `crates/honba-engine/src/execution.rs:104` / `crates/honba-engine/src/engine.rs:490`, every engine in `honba-sim`/`honba-py`/`honba-sweep`/`honba-strategy` + `StrategyRunner` + shims (`089ea11, 3504b01, ad9c3a5`); (c) `python/src/honba/adapters/models.py:303` `venue_order_id`, `FakeAdapter` conformance `python/tests/integration/test_fake_adapter_conformance.py` + `python/tests/unit/test_fake_adapter_order_state.py` + `honba.adapters.order_events.reduce_order_report` (`50cd136, c079288, 084841d`); (d) `crates/honba-sim/tests/order_state_conformance.rs` (BarFill/Paper/NextOpen/Scripted) + `crates/honba-py/tests/order_state_conformance.rs` (NativeSim, `9dc5998`) + `python/tests/integration/test_order_state_conformance.py::test_gateway_normalised_stream_replay` (NextOpen python/native, FakeAdapter) | L | E0-S2 | `illegal_transition_rejected`, `partial_fill_sequence` / one conformance scenario set (market, limit, partial, reject, cancel race, expiry) run on every gateway: `crates/honba-sim/tests/order_state_conformance.rs`, `crates/honba-py/tests/order_state_conformance.rs`, `python/tests/integration/test_order_state_conformance.py`, `python/tests/integration/test_fake_adapter_conformance.py` | honba (+adapters) |
| E2-S7 (DONE) | Reconciliation on startup/reconnect (`crates/honba-engine/src/reconciliation.rs`, commit `9977f87`) | M | E2-S4, E2-S6, E2-S9 | `missed_fill_detected` / `crates/honba-engine/tests/reconciliation.rs` | honba |
| E2-S9 (DONE) | State cache (`crates/honba-engine/src/cache/mod.rs`, no longer a stub): orders, positions, instruments, last quotes; `StateCache`/`CacheQuery`/`TrackedOrder` (commit `efdd717`) | M | E2-S6(a) | `cache_apply_event` / `context_reads_from_cache` | honba |
| E2-S10 (DONE) | Feed staleness breaker (`RiskRefusal::FeedStale`) on the simulated clock (commit `508316f`) | S | E2-S2, E1-S6 | `stale_after_blocks_orders` / `stale_feed_scenario` | honba |
| E2-S11 (DONE) | Durable risk state, idempotent fill ledger (`crates/honba-risk/src/{durable,ledger}.rs`, commit `51e5650`) | M | E2-S2, E2-S6, E2-S7 | `fingerprint_dedup`, `atomic_snapshot` / `reconnect_replay_no_double_count` | honba |
| E2-S8 | DROPPED (see 3.2) | | | | |

### E3 Execution realism

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E3-S1a (DONE) | Rust port of the next-open simulator behind `ExecutionEngine`, Python `NextOpenExecution` API unchanged. **All chunks shipped** (ADR 0016): chunk 1 port, chunk 2 settlement + costs + `SessionOpen`, chunk 3a PyO3 binding, chunk 3b Python delegation (native backend with a pure-Python fallback). Note: the Python reference never fills limit or stop orders (they are rejected `unsupported_order_type`), so "harden L1 stop/limit" needs a product decision before it can be scheduled | L -> split (port, order types, partial by volume) | ADR D1 | `crates/honba-sim/tests/next_open_conformance.rs::next_open_vectors_match_the_python_reference` (Rust), `crates/honba-py/tests/next_open_native.rs::every_chunk_one_and_two_vector_matches_the_python_reference` (`honba._honba` core), `python/tests/integration/test_next_open_sim_conformance.py::test_next_open_vector_matches_the_python_reference` (Python + native backends) replay `schema/conformance/next_open_sim.json` and assert equal | honba |
| E3-S1b-1 (DONE) | L2 matching core (`crates/honba-sim/src/l2/`, `L2Sim`; unit tests `crates/honba-sim/src/tests/l2.rs`): top-of-book book, marketable walks, resting limits trade-through, stops from TradeTick, IOC/FOK/day, L2 cancel timing (ADR 0019) | M | E2-S6, E3-S2, ADR 0030 | `limit_fills_on_trade_through` / L1 vs L2 conformance on same stream | honba |
| E3-S1b-2 (DONE) | Python parity: `make_simulator(fill="l2")`, native binding via `honba._honba`, fallback reference | M | E3-S1b-1 | parity on `l2_sim.json` vectors | honba |
| E3-S1b-3 (DONE) | Conformance vectors: `l2_sim.json`, `gen_l2_vectors.py`, replay (Rust + Python); Rust replay `crates/honba-sim/tests/l2_conformance.rs`, `make check-l2-vectors` | M | E3-S1b-1 | `l2_sim.json` replayed byte-identically | honba |
| E3-S1d (DONE) | Differential oracle: naive reference matcher vs optimised sim on seeded streams; `crates/honba-sim/tests/l2_oracle.rs` (600 seeds per run, 20k checked once; no L2Sim divergence found) | M | E3-S1b-3 | `oracle_agrees_seeded_streams` | honba |
| E3-S2 (DONE) | `FillModel`, `LatencyModel` traits, seeded; slippage, ack delay, queue position (commit `695a549`, `crates/honba-sim/tests/fill_latency_models.rs`); depth-survival discount deferred to L2 (ADR 0025) | M | E3-S1a | `seeded_slippage_reproducible` / `same_seed_byte_identical_fills` | honba |
| E3-S3 (DONE) | Wire Rust `CostSchedule` into fills; `edge_after_costs` (named charges on `Trade` deferred) | M | E0-S7 | `charges_effective_dated` / `india_costs_in_backtest_match_python_costmodel` golden | honba |
| E3-S4 (DONE) | Margin model and MIS square-off from `MarketProfile` (commit `1ffa180`) | M | E3-S3, E2-S3 | `mis_run_flat_by_close` (`crates/honba-sim/tests/mis_square_off.rs`) | honba |
| E3-S5 (DONE) | Target-position (smart order) with per-instrument serialisation (commit `599fb22`) | M | E3-S6, E2-S6 | `target_delta_computed` / `target_position_run` | honba |
| E3-S6 (DONE) | Lot and freeze-quantity splitter, lot-aware sizing (commit `6f8d509`) | S | E1-S3 | `slice_above_freeze` / `order_sliced_in_sim` | honba |
| E3-S7 (DONE; REST path pluggable, no real venue yet) | `SandboxAdapter`: paper on live data, same matching core and FSM (commit `71de25f`). REST order routes submit/cancel through `ExecutionVenue` (`AppState::with_execution_gateway`, default `NextOpenSim`; tests `orders_gateway`). `AppState::spawn_bar_pump(mpsc::Receiver<Bar>)` feeds a bar source to `advance_market` (test `bar_pump`); the pump also writes each bar into `LiveQuoteStore` (`honba-feed`), so `GET /quotes` and the risk gate's latest quote and `ts_event` follow pumped bars (`as_of` queries stay on the dataset). `POST /bars` (write scope `write:orders`, batch of 1..=1000 bars) sends into a bounded channel owned by `AppState` (`start_bar_ingest`/`stop_bar_ingest`): 429 `rate_limited`/`bar_ingest_full` when the queue is full, 503 `market_data_unavailable`/`bar_ingest_disabled` without a pump (test `bar_ingest`); `honba serve --bar-ingest [--bar-ingest-capacity N]` (off by default) starts the pump and drains it on shutdown; `honba serve --risk-config <path.toml>` sets the REST risk limits (`[risk]`, ADR 0018). Open: no real venue gateway wired, no outbound bar source (a broker/bhavcopy feed posting or sending into the channel), no bridge from the async `honba_ports::ExecutionGateway` | M | E1-S6, E2-S6, E3-S1..S4 | `virtual_funds_isolated` / `sandbox_vs_backtest_same_fills` | honba |

### E4 Research pipeline (core in Rust analytics/sweep; orchestration in Python)

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E4-S1 (DONE) | Journal schema v1 (seed, config hash, dataset id, git rev, trial count, schema_version) from `AuditLog`; result cache keyed by hash(manifest, params, dataset id, cost-model version) | M | E2-S4, ADR D3 | `journal_schema_roundtrip`, `cache_key_changes_with_costs` / `rerun_hits_cache` | honba |
| E4-S2 (DONE) | `hyperparameters()` and one-line param declarations drive sweep space; defaults checked by `verify` | S | E0-S8 | `param_range_to_schema` / `sweep_space_from_manifest` | honba (+strategies) |
| E4-S3 (DONE) | Finish runner: `honba backtest` CLI, `POST/GET /backtests` with `run_id`, `assumptions.not_modelled` + timing rule, `--wait`/`--stream`, MCP run tools; manifest-driven | M | ADR 0017 (accepted), E3-S1a | `result_has_not_modelled`, `cli_exit_codes` / `rest_backtest_roundtrip_matches_session` (parity defined on the Rust-registered strategy subset; other strategies are 422) | honba |
| E4-S4 (DONE) | Look-ahead guarantee: truncating future data never changes earlier decisions; vectorized vs event-driven parity | M | E4-S3 | `truncation_property` / `prefilter_vs_event_driven_fixture` | honba |
| E4-S5 (DONE) | REST `POST /sweeps`, `GET /sweeps/{id}`; more fitness (Calmar, Sortino, Omega); journal per trial | M | E4-S1, E4-S3 | `fitness_ranking_stable` / `sweep_rest_equals_library` | honba |
| E4-S6 (DONE) | Gates in Rust `honba-analytics`: DSR, PBO, walk-forward efficiency, one-shot holdout (reads trial count), block-bootstrap MC, 2x cost-stress | L -> split | E4-S1, E4-S5, E3-S3 | `dsr_known_values`, `bootstrap_seeded` / `result_carries_validation_block` | honba |
| E4-S7 (DONE) | Chunked streaming batches and dataset ids | M | E10-S4 | `chunks_cover_slice` / `sweep_over_streamed_dataset` | honba |
| E4-S8 (DONE) | Multi-timeframe aggregation on session calendar | M | E4-S4 | `htf_bar_only_when_complete` / `mtf_strategy_run` | honba |
| E10-S4 rest | Streaming Parquet half of E10-S4 is E4-S7 | | | | |

### E5 Agent and API surfaces

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E5-S5 (DONE) | AI strategy verifier: AST, import allowlist, subprocess with rlimits, no network, deterministic smoke, look-ahead check, red-team tests; ADR D7 | M | E4-S4, ADR | `ast_rejects_forbidden_import` / `generated_strategy_passes_same_suite_as_handwritten` | honba |
| E5-S2 (DONE) | Trust envelope: broker/LLM/instrument text wrapped and marked untrusted | S | none | `wraps_untrusted_text` / `mcp_tool_output_marked` | honba |
| E5-S1 (DONE) | Thin Python MCP server over generated `mcp_tools.json` (11 tools: backtest, sweep, verify_strategy, compile_strategy, list_strategies, screen, get_instruments, get_bars, get_backtest, get_backtest_journal, get_sweep); toolset filter; read-only kill switch | M | E5-S2 | `toolset_filter` / `mcp_call_matches_rest_response` | honba |
| E5-S3 (DONE) | Scope map (read:market, read:account, write:orders, research, operator) with CI drift test; audit by token id | S | E5-S1, E11-S8 min | `scope_for_every_endpoint` / `drift_test_fails_on_new_route` | honba |
| E5-S4 (DONE) | Approval queue for placements; cancel/modify bypass | M | E2-S2, E5-S3 | `placement_held`, `cancel_bypasses` / `approve_flows_through_risk_to_sim` | honba |
| E5-S7 (DONE) | Typed LLM decisions (veto/size-down only), journaled | S | E5-S5, E4-S1 | `schema_rejects_increase` / `decision_in_journal` | honba |
| E5-S8 rest (DONE) | Write routes gated (E11-S7) | S | E2-S2, E5-S4 | `order_refused_by_risk` / `post_order_flows_to_sim_only` (commit `91ab361`) | honba |
| E5-S10 (DONE) | Local in-process LLM runtime (llama-cpp / GGUF runner) and EBNF->GBNF grammar constraint for zero-syntax-hallucination screening (Design.md §13) (commit `c9579d2`) | M | E5-S1, ADR 0009 | `grammar_masks_invalid_tokens` (`python/tests/unit/test_ai_gbnf.py`) / `ask_runs_offline_zero_syntax_errors` (`python/tests/integration/test_ai_local_llm.py`) | honba |
| E5-S11 (DONE) | Financial domain alignment for tiny models (1B-3B): synthetic financial dataset (3k Indian market NL queries), QLoRA SFT, and compiler-reward RLVR (GRPO via parser & screener evaluator) (commit `827627f`) | M | E5-S10 | `benchmark_screener_accuracy_gt_90pct` / `rlvr_rewards_compiler_pass` (`python/tests/unit/test_ai_alignment.py`) | honba |
| E5-S6 (DONE) | Webhook order ingestion: ordered fail-closed pipeline (kill switch, IP allowlist, size cap, hashed token, parse, live gate, idempotency, cool-off, risk, journal); opaque auth failures so the endpoint is not a token oracle; orders pass the risk stage and are journaled; ADR 0026 | M | E2-S2, E11-S8, E5-S3 | `stage_order_blocks_before_parse`, `unknown_and_malformed_tokens_are_indistinguishable`, `idempotent_replay_returns_cached_response`, `idempotency_caches_the_refusal_not_a_premature_accept` / `webhook_order_passes_risk_and_journals`, `webhook_risk_refusal_is_audited` (`python/tests/unit/test_webhooks.py`, `python/tests/integration/test_webhook_ingest.py`) | honba |
| E5-S9 (DONE) | Report builder with a fact snapshot and quality gate: an immutable snapshot of run facts (metrics, journal ids, assumptions) is the only input to the LLM narrative; a quality check rejects any number in the narrative not in the snapshot; narrative is wrapped as untrusted (ADR 0027) | M | E4-S1, E5-S7 | `narrative_number_outside_snapshot_rejected`, `quality_gate_allows_snapshot_numbers`, `percentage_and_currency_forms_are_matched` / `report_uses_only_snapshot_facts`, `llm_narrative_is_wrapped_untrusted` (`python/tests/unit/test_report_builder.py`, `python/tests/integration/test_report_snapshot.py`) | honba |

### E6, E7, E8, E9

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E6 b3 (DONE) | Monotonic-deque indicators (donchian, williams_r, stochastic, ...) | M each | none | oracle vs old O(n) plus Fraction reference / shared vectors | honba |
| E6 b4 (DONE) | Plain-sum indicators O(1): vwma, chaikin_money_flow, money_flow_index, vortex, cmo, ultimate_oscillator, chaikin_volatility | M | none | old-oracle + Fraction reference / `test_indicators_rolling_batch4.py` | honba |
| E6-S5 (DONE) | Indicator render contract (pane, plots, zones): `honba_indicators::render` `Presentation`/`Plot`/`Zone`/`ColorRole`, embedded in the WASM catalog | S/M | E0-S4 | `output_len_matches_input` / chart consumes via WASM (`render_conformance.rs`, `indicator_conformance.mjs`) | honba |
| E7-S1 | Docs from schemas | S | continuous | docs build | honba-docs |
| E7-S2/S3/S5 | Indexed state, cache persistence, recorder tap | M | pull-based | n/a | honba |
| E8-S1..S4 | Multi-leg; not scheduled | | D13 | n/a | honba |
| E9-S8 (DONE) | Generated TS client from OpenAPI. `honba-codegen` renders `client.ts` (`HonbaClient` + `request<T>` + `apiUrl` + SSE helper) from the same `ENDPOINTS` registry as OpenAPI (`Artifact::Client`, `make client`, `make check-client`); the frontend pins it with `client.test.ts` and now has `lint`/`typecheck`/`test` scripts, and `domain.ts` was refreshed (`honba-frontend ee2e1cb`, honba `60373f0`) | S | E11-S6 | `client_types_compile` / contract test against `honba serve` | honba + honba-frontend |
| E9-S1..S7 (DONE) | StrategyGraph editor, graph to code, code to graph, verify/run, AI assistant, ops console, result views. Core: `strategies/graph.py`, `graph_codegen.py`, `graph_recognize.py`, `designer.py`, `ai/designer_assistant.py` + manifest `graph_hash`/`sidecar` (commit `4e15416`); frontend: React Flow canvas, code/verify/run/assistant/ops/results panels (`7050c49`); ADR 0029, D16 | M each | E0-S8, E4-S3, E5-S5, E11-S4 | core `test_strategy_graph`/`test_graph_codegen`/`test_graph_recognize`/`test_designer`/`test_designer_assistant` + `test_designer_verify_run`; frontend `graph.test.ts`/`flows.test.ts`/`ops.test.ts` (23 vitest) | honba + honba-frontend |

### E10 Async core (complete; E10-S7 done)

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E10-S8 (DONE) | Rust-native NSE bhavcopy connector as a `honba-ports` `MarketDataConnector` impl in `honba-feed::bhavcopy`. Parses the three NSE CSV shapes to 1-day bars at 09:15 IST, filters to the subscriber's instruments, and drives an engine run. D10 decided as a default (ADR 0028): Rust owns market-data connectors, Python adapters keep broker execution; the full bridge stays open | L -> split | E10-S1, D10 | `bhavcopy_connector_parses_fixture` / `bhavcopy_feed_into_engine_run` (`crates/honba-feed/src/tests/bhavcopy.rs`, `crates/honba-feed/tests/bhavcopy_feed_into_engine.rs`, commit `3b230b9`) | honba |

### E11 Versioned API (complete)

| ID | Scope | Size | Deps | Acceptance | Owner |
|---|---|---|---|---|---|
| E11-S3 rest (DONE) | Backtests, sweeps, journals, orders routes built in order of R2; `NOT_IMPLEMENTED_ENDPOINTS` is now empty, so no route returns 501. Backtests and journals built by ADR 0017 q2c (`7298373`), sweeps (E4-S5), orders write ledger (E11-S7, `91ab361`, `GET /orders`); ADR 0013 limits (no real bid/ask, no pagination) stand | M each | E4-S3, E4-S5 | route unit tests / `test_client_parity` extended | honba |
| E11-S4 | SSE for run progress and journals (resume-from-sequence), WS for events later; schema for streamed events | M | ADR D6, E4-S3 | `resume_from_seq`, `event_schema` / `sse_backtest_progress_inproc_and_http` | honba | **DONE** (ADR 0021, commits 523bca5) |
| E11-S5 rest **(DONE)** | WASM: screener eval + backtest replay (screener eval had existed in `honba-indicators`, replay over `JournalV1`; both exported via `honba-api-wasm`; wasm-pack exercised via `make test-wasm`) | M | E4-S3 | conformance vectors `screener_scan.json` + journal replay / browser-free node run | honba |
| E11-S6 (DONE) | Frontend typed client from the OpenAPI/export registry (`Artifact::Client`, `HonbaClient`, `make client`/`make check-client`, frontend `ee2e1cb`, honba `60373f0`) | S | none | `ts_matches_openapi` / frontend build | honba + honba-frontend |
| E11-S7 (DONE) | Write endpoints behind risk and approval queue | M | E2-S2, E5-S4 | `order_refused_by_risk` / `post_order_flows_to_sim_only` / approval queue tests | honba |
| E11-S8 (DONE) | Auth, scopes, secrets, audit (minimum viable before non-loopback `serve`) | L | ADR D11 | `token_scope_enforced` / `write_needs_second_factor` | honba |

---

## 5. Open decisions and ADRs to write

| # | Question | Needed for | Recommendation |
|---|---|---|---|
| D1 | **Run lifecycle and `run_id`**: a `Run` is a server-side object (`pending/running/completed/failed/cancelled`), journal addressable by id, retention, restart behaviour — **decided and accepted 2026-10-07 (ADR 0017)** and shipped (E4-S3, E11-S3); accepted ADRs 0017, 0018 and 0019 unblocked E2-S2, E4-S3 and E11-S3 | E4-S3, E11-S3/S4, E9-S6 | ADR before R2; in-memory plus on-disk journal first |
| D2 | **Cancel timestamp rule**: DECIDED 2026-10-06 (ADR 0008 decision 13 addendum, commit 1c50fc7): a cancel is stamped with the time of the cancel (`ExecutionEngine::cancel(order_id, now)`), identical in Rust and Python. L2/live per-fidelity rule: **decided 2026-10-07 (ADR 0019 decision 6)** — `cancel_requested` at `now`, `cancelled` at `now` (L1/L2-v1) or venue-ack time (live) | E2-S6, E3-S1b | State the per-fidelity rule for L2/live in the FSM ADR |
| D3 | Journal schema v1 and cache key — **decided (ADR 0020)**; E4-S1 shipped | E4-S1 | ADR with golden journal |
| D4 | Risk stage: crate placement (`honba-risk` at L2), Reducing semantics, rule config schema — **decided 2026-10-07 (ADR 0018)** | E2-S2 | ADR before code |
| D5 | `OrderState` FSM, event set, `drain_*` replacement (breaking for adapters, Python ports, bindings) — **decided 2026-10-07 (ADR 0019)** | E2-S6 | ADR, then split stories |
| D6 | Streaming protocol: SSE vs WS, sequence and resume — **decided (ADR 0021)**; SSE shipped (E11-S4) | E11-S4 | SSE first |
| D7 | Verifier sandbox strength: subprocess rlimits vs container — **decided (ADR 0022)**: subprocess with rlimits/audit hook; revisit for hosted | E5-S5 | Subprocess; revisit for hosted |
| D8 | Holdout policy: fixed ranges, who resets consumption — **decided (ADR 0024)**; gates shipped (E4-S6) | E4-S6 | ADR with E4-S6 |
| D9 | Sampler: Rust seeded sampler vs Optuna extra; pinned version — **decided**: Rust seeded sampler shipped (E4-S5); Optuna stays an optional extra | E4-S5 | Rust sampler; Optuna optional |
| D10 | Python adapters (ADR 0010) vs Rust `ExecutionGateway`/`MarketDataFeed` ports: who bridges, which side owns the loop — **decided as a default (ADR 0028)**: Rust owns market-data connectors (bhavcopy first), Python adapters keep broker execution; the full bridge stays open | E10-S8, E3-S7 | ADR before E10-S8 |
| D11 | Minimal auth before non-loopback serve — **decided (ADR 0023)**; shipped (E11-S8) | E11-S8, E5-S3 | Bearer token plus scope map |
| D12 | ADR 0009 (agentic MCP and local LLM) reconciliation: accept local in-process Llama runtime and the layered alignment strategy (GBNF -> SFT -> RLVR) per Design.md §13 — **decided 2026-10-12**; E5-S10/S11 shipped | E5, E5-S10, E5-S11 | Accept with GBNF + RLVR |
| D13 | Is arbitrage a product goal (E8)? | E8 | Unscheduled unless yes |
| D14 | Tick/depth data source for L2 (bars only today; `/depth` empty) — **decided (ADR 0030)**: L2 event-stream contract (QuoteTick/TradeTick, top-of-book, ns granularity, optional DepthSnapshot input); E3-S1b/S1d schedulable | E3-S1b | ADR 0030 defines stream/book/vectors; no tape required |
| D15 | Root CLAUDE.md wording for the pack pattern; clean-room log for OpenAlgo/QuantDinger-inspired work; `honba-python` split re-evaluation (needs `honba-py` on crates.io) | housekeeping | Owner approval |
| D16 | `StrategyGraph` stored form — **decided (ADR 0029)**: Python file + optional `.strategy.json` sidecar; `graph_hash` + `source_hash` on the manifest | E9 | Shipped |
| D17 | NSE settlement default and intraday cycle (counts bars, not sessions) in simulators — **decided (ADR 0016)**; shipped (E3-S1a) | E3-S1a | Settle in the port ADR |

Already decided: pyo3 single cdylib (ADR 0014 decision, `honba-py`), Strategy ABC with shim (ADR 0008), schema from Rust (ADR 0014),
integer money (0011), versioning and nanosecond strings (0012), REST read ports (0013), explicit runtime (0015, implemented in E10-S7), next-open Rust port (0016, all three chunks done, D17),
run lifecycle and `run_id` (0017, D1), risk stage (0018, D4), `OrderState` FSM and client order ids (0019, D5/D2), journal schema v1 and result cache (0020, D3),
SSE streaming (0021, D6), AI strategy verifier (0022, D7), minimal auth (0023, D11), overfitting gates / streaming datasets / session MTF (0024, D8),
tick/depth scope (0025, D14), bhavcopy connector + D10 default (0028), StrategyGraph + AlgoDesigner (0029, D16), L2 matching + differential oracle (0030, D14). Still open: D13 (arbitrage), D15 (housekeeping).

---

## 6. Cross-repo impact register

| Repo | Impact and action | Triggered by |
|---|---|---|
| `honba-adapters` | dhan + zerodha certified (E1-S1b, `MIGRATION.md`); six adapters scaffold-only (future). Will need FSM events (E2-S6), reconcile reports (E2-S7), session/rate-limit (E1-S5) when they land | E1-S1b, E2-S6, D10 |
| `honba-strategies` | Catalog passes `verify` and the loader; add `hyperparameters()` (E4-S2); `backtest_result.json` are placeholders with no regen script; alpha strategies excluded from the next-open replay test; 72 pre-existing ruff errors; `ai_generated/` must route through E5-S5 | E4-S2, E4-S3, E5-S5 |
| `honba-examples` | **Another agent owns it: ask first, new files only below.** Existing follow-ups (use core `NextOpenExecution`/loader, `*_minor` names, settlement default T+1) are theirs, not part of this roadmap | R2, R4, R5 |
| `honba-frontend` | Generated client + types (E9-S8/E11-S6) and test/lint/typecheck scripts shipped (`ee2e1cb`); still needs SSE consumer (E11-S4) and WASM loader (E11-S5), then E9. TS drift check runs in CI (`codegen-drift-frontend`: `make check-schema-ts`/`check-client`) | E11-S6, E9 |
| `honba-docs` | `docs/architecture/money.md` never built (`make build`); missing pages: reject/cancel contract, quotes/depth/serve, WASM, SDK; strategy pages still describe APIs that never existed (`on_stop` intents) | every story; docs ticket per story |

### 6.1 honba-examples E4/E5 demonstrations (new example files only; do not edit existing)

Existing numbering: `backtesting/01..06`, `ai_research/01..06` (several are one-line stubs); `basic/`, `candles/`, `options/` also exist.

| New file (proposed) | Demonstrates | Lands with |
|---|---|---|
| `backtesting/07_run_via_client.py` | Same strategy over `InprocTransport` and `HttpTransport`, `run_id`, assumptions | E4-S3, E11-S3 |
| `backtesting/08_journal_and_cache.py` | Write/read journal v1; cache hit on rerun | E4-S1 |
| `backtesting/09_seeded_sweep.py` | Sweep from manifest params, fitness ranking, determinism | E4-S2, E4-S5 |
| `backtesting/10_lookahead_guard.py` | Truncation parity and prefilter-vs-event check | E4-S4 |
| `backtesting/11_overfitting_gates.py` | Read DSR/PBO/holdout/cost-stress from result | E4-S6 |
| `ai_research/07_verify_generated_strategy.py` | LLM-written strategy through verifier then backtest | E5-S5 |
| `ai_research/08_mcp_tools_from_schema.py` | List and call MCP tools generated from `mcp_tools.json` | E5-S1 |
| `ai_research/09_typed_llm_decision.py` | Veto/size-down schema, decision journaled | E5-S7 |
| `ai_research/03_nl_screener.py` | Natural language screener with Tier 1 grammar and Tier 2 Python predicate execution | E5-S10 |
| `ai_research/10_gated_order_flow.py` | Approval queue plus risk refusal on simulated orders | E5-S4, E2-S2 |

Each example ships with its own test under `honba-examples/tests/` (unit for helpers, integration running the script on synthetic data).

---

## 7. Definition of done

A story is done only when all of the following hold (merges roadmap v4 DoD, `docs/archive/plan.md` section 10, root R1-R3):

1. Contract merged first (trait, ABC, schema, `.pyi`, ADR if `needs-adr` or breaking) with docs.
2. Failing test seen first (R1); bug fixes start with a regression test; no test deleted, skipped or loosened without a recorded reason.
3. Unit tests and integration tests present and named in the story (R3), plus the shared contract test for any port change; or an explicit
   note why a level does not apply (docs-only, config-only, generated code, pure rename).
4. Deterministic: seeds, recorded fixtures, no network, no wall clock; cross-language behavior covered by shared golden vectors.
5. Reference implementation merged; Python binding and `.pyi` for any user-facing Rust behavior; Python API unchanged when logic moves to Rust.
6. Codegen artifacts regenerated, no drift (`make check-codegen-ci`); `schema_version` or `api_version` bumped per ADR 0012 when the wire changes;
   every error crossing a surface carries a stable code.
7. `cargo fmt`, `cargo clippy --workspace --all-targets -D warnings`, `cargo test --workspace`, `cargo doc -D warnings`, `ruff check` and
   `ruff format --check`, `pytest -m "not slow"`, stubtest, and `scripts/dependency_graph.py` (layering, async isolation, WASM purity,
   pyo3 containment, no `india` in core) all green.
8. For research-affecting changes: journal output defined, seeds recorded, and same seed and data give a byte-identical journal at 1, 4 and 16 worker threads.
9. Independent review done (one reviewer subagent per area, read-only); CHANGELOG entry for behavior changes.
10. Affected consumers flagged or updated per section 6 (adapters, strategies, examples, frontend, docs); docs ticket for new public behavior.
11. Commits: by explicit path, no trailer, one logical step each, tests committed with the code they drive.

Architecture-level done (`docs/archive/plan.md` section 10), still open: (a) one strategy runs unchanged in-process, over REST and in `honba-examples`
(section 6.1); (b) frontend computes indicators, screener and replay in WASM with no Python (replay and screener export open); (c) five
codegen artifacts with blocking drift checks (met; the TS check now runs in CI via `codegen-drift-frontend`); (d) every surface error carries a stable code (met for
REST and WASM: `honba-api-wasm` refusals are `reason: message` pairs reusing the REST `ErrorCode` for input validation and the evaluator's own reasons); (e) determinism at 1/4/16 threads (met for sweeps and runs — `honba-async` `determinism_under_threads_*`).

---

## Appendix A: Corrections to the audit (spot-check, 2026-10-06)

This appendix is a historical snapshot of the 2026-10-06 spot-check. Several verdicts below (for example E2-S9, E3-S2, E2-S1, E11-S3)
have since been superseded by shipped work; see §1.1 and §4 for the current status, not this table.

Checked about 25 verdicts against code, git log and ls/grep (read-only). Counts: the audit's table holds 74 rows (8 DONE, 37 PARTIAL,
29 NOT STARTED) while its prose said 70 and its summary 10/31/28 = 69. Corrected totals were 18/18/38 at v5; v6 moved E10-S7 to done (19/17/38, see section 1).

| Story | Audit said | Code shows | Corrected |
|---|---|---|---|
| E10-S6 | NOT STARTED | `crates/honba-sweep/tests/sweep.rs` runs `determinism_under_threads` at 1, 4, 16 workers comparing journal and ranking | DONE |
| E2-S2 | PARTIAL ("`honba-risk` skeleton, `RiskCheck` stub") | `honba-risk` crate with `RiskCheck`, `RiskStage`, typed refusals (`TradingHalted`, `ReduceOnly`, `InstrumentUnknown`, `QuantityBelowMin`, `QuantityOverFreeze`, `LotMultiple`, `TickSize`, `PriceBand`, `MaxParticipation`, `MaxNotional`, `OrderRate`, `FeedStale`), rules source port, India profile; unit + property + conformance tests | DONE |
| E4-S3 | PARTIAL, "`not_modelled` list exists (CHANGELOG)" | `not_modelled` + timing in `assumptions: Option<Value>`; `honba backtest` CLI with `--wait`/`--stream`; `InprocTransport`/`HttpTransport`; `POST/GET /backtests` + journal; MCP `get_backtest`, `get_backtest_journal`, `get_sweep`; parity test passes | DONE |
| E11-S4 | NOT STARTED, "WS/SSE routes return 501" | Verdict right; no stream route is registered at all (22 routes in `endpoints.rs`, none streaming) | NOT STARTED (evidence fixed) |
| E10-S3 | PARTIAL, "feedback unverified" | `Engine::apply_output` applies orders, cancels, state; fills re-enter queue as `OrderFilled`; commit daf12d5 | DONE (E2-S5 likewise DONE) |
| E10-S2 | PARTIAL | `honba-async` `EngineHandle`, clocks, command channel, tests, commit 49ffb32 | DONE |
| E10-S4 | PARTIAL, "not implemented" | `Dataset`, `ColumnarSlice`, `DatasetFeed` (c7ef86b); only chunked streaming missing | PARTIAL (narrower, moved to E4-S7) |
| E10-S5 | PARTIAL | Runner, report, tests complete; only REST route missing, tracked in E4-S5 | DONE |
| E10-S7 | PARTIAL, "bridges visible" | No `honba.event_loop`; `auto-initialize` already absent and `pyo3-async-runtimes` present | PARTIAL (ADR 0015 plus dependency only) |
| E2-S6 | PARTIAL, "(a) OrderState FSM and client ids done; simulator emits ack events" | Only `OrderStatus` enum and `drain_rejections`/`drain_fills`; no FSM, no `client_order_id`, no ack events | PARTIAL (only reject/cancel queue) |
| E1-S2 | DONE | `Instrument` has derivative fields; `india/fno` parse/format and acceptance tests shipped | DONE |
| E3-S2 | PARTIAL, "FillModel/LatencyModel traits" | Only a config enum in `honba-config`; `sim/{latency,cost,simulator}` are stubs | NOT STARTED |
| E2-S9 | PARTIAL, "StateCache stub in execution.rs" | `engine/src/cache/mod.rs` is a 10-byte stub; `execution.rs` has no cache | NOT STARTED |
| E4-S2 | PARTIAL, "manifest params with `range=a:b:step`" | One-line Parameter declarations, `hyperparameters()`, manifest validation and grid generation | DONE |
| E4-S6 | PARTIAL, "DSR/PBO/MC in CHANGELOG" | `monte_carlo.rs` and `walk_forward.rs` are 1-line uncompiled files; no DSR/PBO anywhere | NOT STARTED |
| E4-S1 | PARTIAL, "BacktestResult wire model (commit 166c0bd)" | Journal v1 schema + NDJSON (`honba-engine`), ResultCache + CacheKey (`honba-api`), integration in `BacktestExecutor` | DONE |
| E4-S5 | PARTIAL, "fitness enum Sharpe, Calmar, Sortino, Omega" | Only `SharpeFitness` implements the `Fitness` trait | PARTIAL (corrected) |
| E2-S1 | PARTIAL, "enforcement unknown" | Halted refuses and audits (`engine.rs:261`); Reducing accepted without restriction; no Python or CLI exposure found | PARTIAL (clarified) |
| E0-S4/S5/S6/S8 | PARTIAL | Handoff and commits: schema export, versioning, money, manifest+IR+`verify` all shipped with tests | DONE |
| E11-S3 | PARTIAL | Read routes, screener and strategies built and tested; remaining 501 routes belong to E4/E5 | DONE |
| ADR 0009 | "Approved" | File says Status: Proposed | Open decision D12 |
| E3-S3 | PARTIAL, "cost model wired to fills" | Rust `CostSchedule` unused by `honba-sim`; only Python `fill_costs_from_model` path | PARTIAL (Python only) |

Verdicts confirmed by the spot-check: E3-S1 (L1 only; sim stubs), E11-S5 (indicators, ATR, Williams %R and Stochastic only; screener evaluator not exported to WASM),
E5-S1 (DONE) (11 tool schemas, MCP server with CLI `honba mcp serve`); E1-S1, E0-S7, E0-S1..S3, E11-S1/S2, E10-S1, E9 (all not started), E5-S4 (DONE), E5-S5/S6/S7.
