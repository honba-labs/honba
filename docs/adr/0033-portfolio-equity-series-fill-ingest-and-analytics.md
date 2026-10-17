# ADR 0033: Portfolio Equity Series, Fill Ingest and Analytics

Date: 2026-10-16. Status: **proposed**. Roadmap: D20; chunks P1..P6 (a parallel track to E13, not
folded into its slice order). Owner: `honba` (+ `honba-frontend`). Builds on ADR 0032; design
source: `honba-frontend/docs/Portfolio-Analytics-DESIGN.md` (section 7 holds the confirmed
decisions).

## Context

ADR 0032 gives each user a valued portfolio (E13-S0 contract and E13-S1 pure valuation are
committed; E13-S2 store and read routes are in progress). It leaves three things for later: how
fills reach the portfolio (E13-S3), what the portfolio was worth over time, and the analytics on
top (E13-S6, "value series, annualized yield, benchmark, Beta/Sharpe/Sortino"). A survey of the
code on 2026-10-16 found the gaps below, which this ADR closes.

- **No equity history anywhere.** The server keeps no value series for a portfolio or a run. REST
  `BacktestMetrics` (`honba-api::responses`) is computed in `honba-api-rest::executor` with a
  fixed `PERIODS_PER_YEAR = 252`, from round trips rather than marked bars, so it ignores open
  positions and costs; the Python per-bar curve differs in convention (percent vs fraction,
  round-trip vs bar-based). Two numbers called "Sharpe" disagree.
- **Fees never reach the journal.** `order_filled` / `order_partially_filled` carry no costs,
  `trades_from_journal` sets `Trade.costs` to zero, and `PortfolioPosition.realized_pnl` is gross.
  The simulators already price fills (`honba-sim::ScheduleFillCost`, itemised `FeeBreakdown`).
- **Fills are not attributable.** `Order` and the fill events have no account and no strategy; a
  run manifest has one `strategy_id`. ADR 0032 §4 says "an order names the account it belongs to",
  but nothing on the wire can.
- **Fill ingest has no write path.** `OrderLedger` is volatile, process-wide and quantity-only;
  `honba-risk`'s idempotent `FillLedger` (`FillFingerprint`, E2-S11) is not wired to any user.
- **One missing mark fails the portfolio.** E13-S1 makes a non-flat position with no mark a
  `PortfolioError::MissingMark`, and the E13-S2 routes answer 503 for the whole portfolio.
- **No day baseline.** Day P&L needs a session-open equity; nothing records one.
- **The frontend re-derives numbers.** The portfolio store revalues accounts in TypeScript with
  float math and a hard-coded margin, and shows a mock snapshot when offline.

Prior art (ideas only; OpenAlgo is AGPL and no code is taken): OpenAlgo buffers fills that arrive
before their order and keeps sandbox history in separate tables; TradingView shows value over time
against a benchmark. Neither compares a backtest with paper trading of the same strategy, which
honba can do because both modes emit the same fill vocabulary (ADR 0019).

## Decision

### 1. One pipeline for every mode; Rust owns the numbers

**fill events -> per-account ledger -> valued snapshot -> equity series -> analytics report.**
Backtest and simulator runs, paper (Sandbox) accounts and, later, live accounts all produce
`SCHEMA_VERSION` fill events, so one code path values, charts and compares them. Ledger and
valuation stay in `honba-entities` / `honba-portfolio`; analytics live in `honba-analytics`. The
frontend renders server payloads and never computes a P&L, a ratio or a mark itself. Rationale: two
implementations of one figure are how the current Sharpe divergence happened (the ADR 0031 §1
discipline, applied to money).

### 2. Account kinds: Live and Paper in the portfolio; runs stay run-scoped

| Kind | Fills from | Lives in |
|---|---|---|
| Live | broker (contract only today) | user portfolio document, `mode: live`, `source: broker` |
| Paper | `SandboxAdapter` / REST `/orders` on the simulated venue, virtual funds from `AccountConfig.starting_cash` | user portfolio document, `mode: sandbox`, `source: ledger` |
| Backtest / sim run | the run journal (`events.ndjson`, ADR 0017/0020) | the run directory, never the portfolio document |

A run is **read-only and valued on demand** by replaying its journal through the same ledger and
valuation code; it is addressed by `run_id`, not imported as an account. Rationale: runs are
numerous and throwaway; importing them would pollute the document and make the 100-account cap
(`MAX_ACCOUNTS`) meaningless. Paper accounts gain **reset** (decision 8) and, later, clone-from-run.

### 3. Additive schema change, `SCHEMA_VERSION` 4 -> 5

New optional fields (all `#[serde(default, skip_serializing_if = "Option::is_none")]`):

| Type / event | Field | Type |
|---|---|---|
| `Order` (record) and `OrderIntent` (input) | `account_id` | `Option<AccountId>` |
| `Order`, `OrderIntent` | `strategy_id` | `Option<StrategyTag>` |
| `order_partially_filled`, `order_filled` | `account_id`, `strategy_id` | as above (copied from the order) |
| `order_partially_filled`, `order_filled` | `costs` | `Option<FillCosts>` |

- `AccountId` and `StrategyTag` are new L0 string newtypes in `honba-messages::identifiers`,
  validated like user ids (`[A-Za-z0-9_-]`, 1..=64 bytes).
- `FillCosts { amount: i64, currency: String, charges: Vec<FillCharge> }`, `FillCharge { name:
  String, amount: i64 }`, in `honba-messages` (L0). `Money` and `Currency` are L1, so the L0 type
  keeps the **same JSON shape as `Money`** (`amount` in minor units, ISO-4217 `currency` code) and
  `honba-entities` converts it into `Trade.costs` at the boundary, rejecting an unknown currency.
  `charges` is optional and sums to `amount` when present (validated on parse).
- **Why bump although the change is additive.** ADR 0012 rule 1 lets an optional field ride without
  a bump, and E13-S0 did exactly that. `costs` is different: a v4 reader that ignores it would
  report gross P&L as net without noticing, which ADR 0012 calls a change of *meaning*. The bump
  makes old readers refuse a v5 stream instead of misreporting it.
- **Compatibility rules.** Writers always emit 5. Readers accept **4 and 5**:
  `SchemaVersion::try_from` changes from "only the current version" to
  `MIN_READ_SCHEMA_VERSION (4) ..= SCHEMA_VERSION (5)`, and a v4 record decodes with the new
  fields `None`. A v4 journal stays valid input to every route and to replay; its fills are
  valued with **zero recorded costs** and flagged `costs_recorded: false` in the analytics inputs,
  rather than being re-priced silently. Conformance and golden files at v4 are kept unchanged as
  the read-compat set; v5 files are added beside them with the new fields present and absent.
- Inputs (`OrderIntent`, request bodies) keep `deny_unknown_fields`; the new fields have defaults,
  so an older author's document still parses (ADR 0012, "inputs gain fields only with defaults").
- Codegen regenerates every artifact (`make codegen`); the Python wire models gain the optional
  fields; the `CHANGELOG.md` gets the migration note in the same commit (ADR 0012 rule 5).
  Adapters (`honba-adapters`) need only the regenerated stubs; they may start filling
  `account_id` later.

### 4. Fill ingest: idempotent, per user, costed

- A fill sink in `honba-api-rest` receives every fill of a user's paper (and later live) venue,
  keyed by the request's `Caller`. It appends through the user-document store's `update`, which
  bumps the revision **once per batch** of fills, not once per fill.
- **Idempotency** reuses `honba_risk::FillFingerprint` (SHA-256 over order id, trade id, venue
  order id, instrument, side, quantity, price, `ts_event`); the per-user set of seen fingerprints
  is persisted beside the document so a replayed or duplicated fill is a no-op across restarts.
- **Early fills are buffered**: a fill whose order metadata (account, product, strategy) has not
  arrived is held in a bounded per-user buffer and applied when the order arrives; an expired
  entry is reported, never guessed into a default account.
- Position math stays `Position::apply_fill` (weighted average, realise on flip). Costs come from
  the fill's `costs` when present, else from the account's `ScheduleFillCost`; they are stored on
  `Trade.costs` and accumulated per position.
- Realised P&L is reported **gross and net**: `realized_pnl_gross` and `costs` are separate fields
  and net is their difference, so no reader has to guess which one it got.
- A fill naming an account of the other mode is refused (ADR 0032 §4); a fill with no
  `account_id` goes to the user's default sandbox account.

### 5. Equity series, stored beside the document

- New L1 types in `honba-entities`: `EquityPoint { ts: UnixNanos, equity: Money, cash: Money,
  market_value: Money, realized: Money, unrealized: Money, costs_cum: Money }` and `EquitySeries
  { account_id, currency, points: Vec<EquityPoint>, quality: ValuationQuality }`.
- A pure `honba_portfolio::series(fills, marks, sessions) -> EquitySeries`: no clock, no I/O,
  byte-identical for equal input, pinned by golden vectors beside `portfolio_snapshot.json`.
- **Sources.** A run: replay the journal and emit a point at each bar close (marks from the run's
  bars). A paper/live account: append a point on every fill batch and on a mark-driven interval.
- **Storage beside the portfolio document**, as append-only NDJSON
  `<portfolios_dir>/<user_id>.equity/<account_id>.ndjson` (user decision; resolves ADR 0032's open
  item "in the document or beside it"). The document stays small and inside its 2 MiB cap; the
  series is append-only like the operator journal. A reset (decision 8) starts a new epoch file.
- **Day baseline** is the first point of the current session, or the previous session's closing
  mark when no point exists yet; day P&L is the latest point minus the baseline. Sessions come
  from the instrument calendar (`honba-market`), passed into `series` as an argument, never from a
  clock read.
- **Per-position valuation quality** replaces the all-or-nothing `MissingMark`: a position with
  an old mark is valued at it and flagged `stale` (with the mark's age), a position with no mark
  at all is flagged `missing_mark` and valued at its average price, and the snapshot carries a
  `valuation_quality` summary (`ok | degraded`, plus the flagged instruments). This amends the
  E13-S1 decision and the E13-S2 503; it is additive on the wire (decision 10 sequences it).

### 6. Analytics: one payload, show the working

- `POST /portfolio/analytics` with `{accounts[] | run_id, range, benchmark?, risk_free,
  periods_per_year?}` and `GET /backtests/{id}/analytics` (alias, `run_id` implied). The response
  `AnalyticsReport` is the **same type** for a paper account and a run, so tabs cannot disagree.
- Sections: `summary` (total and annualised return, volatility, Sharpe, Sortino, Calmar, max and
  current drawdown), `drawdown` (series and underwater episodes with duration), `returns` (daily,
  monthly, yearly tables), `trades` (round trips via `RoundTrip::from_fills`, win rate, profit
  factor, expectancy, holding time, gross vs net), `costs` (by charge name, share of gross P&L),
  `exposure` (gross/net, leverage, concentration: HHI, effective holdings, top-N weight),
  `attribution` (by instrument always, by strategy once tags exist; a selection / allocation /
  costs split that reconciles exactly to net P&L), `benchmark`, and `model_drift` (decision 7).
- **Benchmark is optional in v1** (user decision): the section is present only when benchmark bars
  are supplied; otherwise it is `status: undefined, reason: no_benchmark`.
- **Every figure is a `Figure { value, inputs, formula, n_obs, status }`**. `status` is `ok` or
  `undefined` with a reason (`too_few_observations`, `zero_variance`, `no_benchmark`,
  `costs_not_recorded`); never NaN, never a substituted number.
- **Conventions are explicit fields**: returns are **fractions** (0.012, not 1.2); the report
  echoes `periods_per_year` (from the request, else inferred from the bar interval and the
  calendar) and `risk_free` (per year, fraction). Money stays integer minor units (ADR 0011);
  ratios are `f64`.
- **`BacktestMetrics` reads from the same code path**: the executor's fixed 252 and round-trip
  Sharpe are replaced by the mark-to-market, cost-inclusive series of decision 5. This changes
  existing numbers (see Consequences).
- Scopes: `read:account` for accounts; `research` for `run_id` requests (the run's own scope).

### 7. Simulator vs paper comparison

For the same strategy and window, `model_drift` compares a run with a paper account:
implementation shortfall per fill (paper fill price vs the simulator's modelled fill and vs the
next bar's open), slippage and cost drift, fill rate and latency, and the equity divergence of
the two series. It is a section of the analytics payload and an **alert source** ("paper trails
its backtest by more than X"). Rationale: this is the only measurement of whether `FillModel`,
`SlippageModel` and the cost schedules are realistic, and only a shared fill vocabulary allows it.

### 8. REST and SSE additions

| Route | Scope | Notes |
|---|---|---|
| `GET /portfolio/equity?account=&range=` | `read:account` | `EquitySeries`, paginated by time |
| `POST /portfolio/analytics` | `read:account` (`research` with `run_id`) | decision 6 |
| `GET /backtests/{id}/analytics` | `research` | alias, `run_id` implied |
| `POST /portfolio/accounts` | `write:orders` | create a paper account; live accounts are not created here |
| `POST /portfolio/accounts/{id}/reset` | `write:orders` | paper only, 409 for live; new revision, starting cash restored, history archived under a new epoch |
| `GET /portfolio/events?resume_from_seq=N` | `read:account` | SSE (ADR 0021 framing and resume): `snapshot_changed`, `equity_point` |

All per user through `Caller`; no user id in any path; every route is in the scope map (drift
test). The SSE stream removes UI polling.

### 9. Out of scope for v1

As ADR 0032: multi-currency portfolios and FX, dividends and corporate actions, and tax lots. One
settlement currency per account. A portfolio **never places an order** (ADR 0032 §7); reset and
create change virtual funds only.

### 10. Relationship to ADR 0032 / E13 and ownership

The E13 session owns E13-S2..S6. Proposed split:

| Item (this ADR) | E13 story | Owner | Note |
|---|---|---|---|
| D3 schema bump (P1) | none | **new here** | prerequisite of E13-S3 (an order must name its account) |
| D4 fill ingest into the document | **E13-S3 (overlap)** | E13 session | S3 adopts D4's rules (fingerprint, buffer, costs, gross/net, one bump per batch); this track does not write a second ingest |
| `GET /positions` alias | **E13-S3 (overlap)** | E13 session | unchanged from ADR 0032 §6 |
| D5 `EquityPoint` / `series()` / NDJSON store / day baseline (P3) | E13-S6 "value series" (overlap) | **new here** | E13-S6 drops its value-series part |
| D5 valuation quality | amends E13-S1, E13-S2 | **new here** | lands after E13-S2 is committed; touches `honba-portfolio::value` and the `/portfolio` 503 path |
| D6 analytics payload + `BacktestMetrics` (P4) | **E13-S6 (overlap)** | **new here** | E13-S6 is delivered by P4 + P5 and closed |
| D6 benchmark, attribution; D7 model drift (P5) | E13-S6 benchmark/Beta | **new here** | |
| D8 create/reset, `/portfolio/events` (P6) | near E13-S4 (write path) | **new here** | different routes, same store `update`; sequence after S4 or agree on the route module |
| D2 runs not in the portfolio | consistent with E13-S4 "import-from-paper-trading" | both | E13-S4 must not import runs as accounts |
| Frontend F1 (drop mocks and TS revaluation) | **E13-S5 (overlap)** | E13 session (proposed) | F2..F4 build on it |

**Sequencing rules.** Commits are file-scoped (`git add <paths>`; never another session's files).
Only P1 bumps `SCHEMA_VERSION` (4 -> 5); E13-S2..S6 changes are additive and need no bump (ADR
0012 rule 1). P1 starts **after E13-S2 is committed**, so the two sessions never regenerate
`schema/` artifacts over each other's uncommitted work, and **before E13-S3**, which consumes
`account_id`. A schema bump is announced first (a ROADMAP line naming the new number) before any
code. If the E13 session must bump first, it takes 5 and P1 rebases onto it as 6: rerun `make
codegen` on top of the rebased tree (never hand-merge generated files), widen
`MIN_READ_SCHEMA_VERSION..=SCHEMA_VERSION` accordingly, and add the conformance set for the new
number.

## Chunks

| Chunk | Scope | Depends on |
|---|---|---|
| P1 | Schema v5: `AccountId`, `StrategyTag`, `FillCosts` on `Order` / `OrderIntent` / fill events; reader range 4..=5; codegen; v4 and v5 conformance and golden files; CHANGELOG migration note | ADR 0033 accepted, E13-S2 committed |
| P2 | Fill ingest per decision 4 and `GET /positions` (= E13-S3, E13 session) | P1, E13-S2 |
| P3 | `EquityPoint` / `EquitySeries`, pure `series()` + goldens, NDJSON store beside the document, day baseline, valuation quality, `GET /portfolio/equity` | P2 |
| P4 | `AnalyticsReport` in `honba-analytics` (summary, drawdown, returns, trades, costs, exposure), both analytics routes, `BacktestMetrics` on the same path | P3 |
| P5 | Attribution, benchmark/beta, `model_drift` | P4 |
| P6 | Paper account create/reset, `GET /portfolio/events` SSE | P2 (after E13-S4) |
| F1..F4 | Frontend: client regen and mock removal; analytics tabs; simulator app; layouts and alerts | E13-S2; P4; P4+P5; P6 |

Independent review after P2, P4 and F3. Acceptance per chunk: golden replay and byte-identical
determinism (P3, P4), a v4 journal still replays (P1), duplicate fills are no-ops across a restart
(P2), every `undefined` reason has a test (P4), a run and a paper account of the same fills give
the same report (P4).

## Consequences

- **One definition per number.** Server, run reports and the UI show the same figure for the same
  fills, with its inputs and formula; the TypeScript revaluation and mocks can be deleted.
- **Existing numbers change.** Moving `BacktestMetrics` to mark-to-market, cost-inclusive,
  explicit-period returns alters Sharpe, drawdown and total return of past runs. The analytics
  payload carries `analytics_version`, the change is changelogged, and the ADR 0020 cache key gains
  `analytics_version` so a stale `CachedResult` is not served.
- **Schema bump ripples.** Python wire models, adapters' stubs, strategies' fixtures and docs see
  v5; additive fields and the 4..=5 reader range keep old journals and vectors valid.
- **Replay cost.** Valuing a large run streams its NDJSON journal; series and reports are cached
  per `(run_id, config_hash, analytics_version)` in the ADR 0020 `ResultCache`.
- **Payload size.** Show-the-working payloads are larger: series are paginated by time, tables are
  summarised, and per-figure `inputs` hold references and counts, not raw arrays.
- **Degraded, not failed.** A missing quote marks one position instead of 503 for the portfolio;
  readers must show `valuation_quality`.
- **More files per user** (`.equity/` beside the document); still no database and the same "one
  `honba serve` per directory" caveat.

## Alternatives considered

- **Import runs into the portfolio as accounts.** Rejected (decision 2): throwaway accounts, a
  meaningless cap, and run data duplicated outside its journal.
- **Equity series inside the portfolio document.** Rejected (decision 5): grows without bound
  against a 2 MiB cap and rewrites the whole document on every point.
- **SQLite for series and fills.** Deferred: NDJSON matches the journal and operator log, needs no
  new dependency, and the per-user files stay inspectable; revisit if range queries get slow.
- **Analytics computed in the frontend.** Rejected (decision 1): float math, a second
  definition, and the source of the current divergence.
- **No schema bump (ADR 0012 rule 1).** Rejected for `costs` (decision 3); acceptable for the ids
  alone, but one bump for the set is simpler than splitting it.

## Open questions for the E13 owner

1. Does E13-S3 adopt decision 4 as written (fingerprint store beside the document, bounded early
   fill buffer, default sandbox account for an untagged fill)?
2. Agree that P1 lands between E13-S2 and E13-S3, and that E13 does not bump `SCHEMA_VERSION`.
3. Valuation quality changes the E13-S1 `MissingMark` error and the E13-S2 503: accept it as an
   ADR 0032 decision-log amendment, and who edits `honba-portfolio::value`?
4. Is E13-S6 closed in favour of P4 + P5?
5. Who owns frontend F1 versus E13-S5, and do create/reset (P6) share a route module with E13-S4?
6. `FillCosts` as an L0 mirror of `Money`, or move `Money` / `Currency` down to `honba-messages`?

## Decision log

- 2026-10-16: proposed (design `Portfolio-Analytics-DESIGN.md`, decisions confirmed by the user:
  parallel track; runs not in the portfolio; series beside the document; schema bump accepted;
  benchmark optional in v1).
