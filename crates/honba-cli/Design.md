# Honba CLI: command surface and screener design

Status: proposed. Scope: the `honba` command-line interface, starting with the `screener` app.

## 1. Goals

1. Every option and filter maps directly onto a domain/wire type, so the CLI adds no semantics of its own.
2. Filters read like English, including Indian and international money units (`Lk`, `Cr`, `Mn`, `Bn`, `Tn`).
3. One uniform command shape across apps, usable by humans, scripts and LLM agents.
4. Deterministic and reproducible: a command can be reduced to a JSON request and replayed.

Non-goals: new broker connectivity, a general-purpose query language, FX/multi-currency support.

## 2. Where this lives

Two entry points exist today:

| Entry | Stack | Today |
|---|---|---|
| `honba` (`honba.cli.main:app`) | Python, typer | `indicators`, `schema`; `backtest`, `data`, `optimize`, `research`, `strategy` are empty stubs |
| `honba-cli` crate | Rust, clap | `backtest`, `calendars`, `data` |

The screener is **Python-first** (workspace principle). It is added to the Python `honba` entry as `honba screener`.
The Rust binary is unchanged. If profiling later shows that evaluation over the full universe needs Rust, the
evaluator moves behind the same Python API (exposed through `honba._honba`, with a `.pyi` stub). The CLI surface in
this document does not change when that happens.

## 3. Command shape

```
honba <app> <sub-command> [<sub-sub-command>] [options] [filters...]
```

- Options come first. Everything after the last option is the filter text (trailing positional words).
- Parsing stops at the first non-option word (`allow_interspersed_args=False` in click/typer). `--` forces the
  remainder to be filter text.
- Filter words may be passed as separate shell words or as one quoted string. Both are joined with single spaces
  before parsing.

### 3.1 Screener commands

| Command | Purpose |
|---|---|
| `honba screener scan [options] <filters>` | Run a scan and render results |
| `honba screener explain [options] <filters>` | Parse and validate; print the parse tree and the resolved request; do not run |
| `honba screener metrics list\|show` | Browse the metric catalog (`MetricDefinition`) |
| `honba screener presets list\|show` | Browse named criteria such as `52 week low` |
| `honba screener save\|run` | Persist a request as `configs/screener/*.toml` and rerun it |
| `honba screener ask [options] <free text>` | Translate free text to a validated request with an LLM, show it, then run (section 13) |
| `honba data coverage\|gaps\|fetch` | Inspect what the store holds, what is missing, and fill gaps (section 12) |
| `honba ai knowledge export\|check`, `honba ai eval screener` | Generate and drift-check the LLM knowledge pack; run the NL-to-query eval (section 13) |

### 3.2 Options of `scan`

Each option maps to a field of `ScreenerScanRequest` (`honba.entities.screener`; Rust mirror in `honba-entities`).

| Option | Request field | Notes |
|---|---|---|
| `--market` | `market` | e.g. `india` |
| `--type` (repeatable) | `types` | default `EQUITY` |
| `--universe` | symbol filter | resolved from `honba.india.universes`, e.g. `nifty50` |
| `--all-listings` | `primary_only=false` | default is primary listing only |
| `--timescale`, `-t` | default `timeframe` | `1d`, `1D`, `daily`, `D1` all normalise to `Timeframe.D1` |
| `--period` | default `period` | `snapshot`, `ttm`, `fy`, `fq`, `h1`, `current` |
| `--columns` | `columns[]` | list of `MetricKeySpec`, comma separated, accepts `key@timeframe` |
| `--column-set` | `column_set` | named column set |
| `--sort 'key[@tf]:asc\|desc'` | `sort` | `ScreenerSortSpec` |
| `--limit`, `--offset` | `range` | default `(0, 50)` |
| `--request FILE\|-` | whole request | bypasses filter text; mutually exclusive with filters |
| `--print-request` | n/a | emit the resolved request as JSON and exit |
| `--format` | n/a | `table` (default on a TTY), `json`, `ndjson`, `csv`, `parquet` |
| `--out PATH` | n/a | write output to a file |
| `--units auto\|indian\|intl\|raw` | n/a | display units, see 6.4 |
| `--asof DATE` | n/a | evaluate as of a date; never the wall clock |
| `--journal` | n/a | log request, response hash and `asof` to `data/journals` |
| `--fetch auto\|never\|force` | n/a | missing-data policy, default `auto` (section 12.5) |
| `--source NAME` | n/a | restrict gap fill to one provider |
| `--on-missing error\|skip\|warn` | n/a | what to do for instruments whose gap could not be filled |
| `--max-fetch N` | n/a | confirm before fetching for more than N instruments |

`json` output is the `ScreenerScanResponse` unchanged, with raw numbers.

### 3.3 Exit codes

| Code | Meaning |
|---|---|
| 0 | Success (zero rows is still success) |
| 1 | Invalid input: parse error, unknown metric, unit not valid for metric |
| 2 | Data or source error: missing catalog, unreadable data |

Errors go to stderr. A parse error shows the filter text with a caret under the offending token and a
"did you mean" list from the catalog.

## 4. Examples

```
honba screener scan --market india --timescale 1d --sort market_cap:desc --limit 20 \
  market cap between 5000 Cr and 2 Tn and rsi below 30 and close near 52 week low

honba screener scan --market india sector in IT, Banks and pe ratio under 25 \
  and volume at least 5 Lk and 50 day sma crosses above 200 day sma

honba screener scan --timescale 1w close at 52 week low or rsi on 1d above 70

honba screener explain --market india market cap above 10000 Cr
honba screener scan --market india --print-request market cap above 10000 Cr > req.json
honba screener scan --request req.json --format json
```

## 5. Filter language

The language lives in a reusable package, `honba/query/`, so any app can accept trailing English filters.
It has no I/O and no dependency on typer.

### 5.1 Grammar

```
filters    := or_expr
or_expr    := and_expr { "or" and_expr }
and_expr   := term { "and" term }
term       := "either" or_expr "end"            -- explicit grouping
            | filter
filter     := metric [ "on" TIMEFRAME ] [ "for" PERIOD ] predicate
predicate  := cmp quantity
            | "between" quantity "and" quantity
            | ("in" | "not in") value { "," value }
            | ("contains" | "like") text
            | ("crosses above" | "crosses below") operand
            | ("at" | "near") preset_target
            | "within" quantity "of" preset_target
operand    := quantity | metric
```

- `and` binds tighter than `or`. `either … or … end` groups explicitly; parentheses work when quoted.
- The `and` inside `between X and Y` belongs to `between`; it is never read as a conjunction.
- The filler words `is` and `are` are accepted and ignored before a comparison.
- Keywords are case-insensitive. Values in `in (…)` keep their case.

### 5.2 Comparison phrases

| Phrase | `FilterOp` |
|---|---|
| `above`, `over`, `greater than`, `more than`, `>` | `GT` |
| `at least`, `no less than`, `>=` | `GTE` |
| `below`, `under`, `less than`, `<` | `LT` |
| `at most`, `no more than`, `<=` | `LTE` |
| `equals`, `is`, `=` | `EQ` |
| `is not`, `not equal to`, `!=` | `NEQ` |
| `between X and Y` | `BETWEEN` |
| `in …` / `not in …` | `IN` / `NOT_IN` |
| `like`, `contains` | `LIKE` / `HAS` |
| `crosses above`, `crosses below` | `CROSSES_ABOVE` / `CROSSES_BELOW` |

Words are preferred over symbols because `<` and `>` are redirects in bash and fish, and `&&` and `||` are command
separators. The symbol forms parse only when quoted.

### 5.3 Metric names

Metrics are matched against the catalog by **longest alias**, case-insensitively. Aliases are catalog data, not parser
code:

- `market cap`, `mcap`, `market_cap_basic` resolve to one key.
- `52 week low`, `52w low`, `52w-low` resolve to one key.
- Ambiguous matches are an error that lists the candidates.

### 5.4 Timeframe and period

- `--timescale` sets the default; `on <timeframe>` overrides it for one filter (`on 1d`, `on weekly`, `on 15 min`).
- A timeframe on a metric whose `has_timeframe` is false is an error.
- `for <period>` (`ttm`, `fy`, `last quarter`) maps to `MetricPeriod` and is checked against `has_period`.

### 5.5 Presets

A preset is a named criterion that expands into one or more predicates, defined as data:

| Phrase | Expansion |
|---|---|
| `at 52 week low` | `close <= min(low, 252 bars)` |
| `near 52 week low` | `close <= min(low, 252 bars) * (1 + tol)` (default `tol` 5%) |
| `within N% of 52 week low` | same with `tol = N%` |
| `at` / `near` / `within` `52 week high` | symmetric, using `max(high, 252 bars)` |

Fewer than 252 bars of history means insufficient data: the instrument is excluded and counted in a warning, not
treated as a match.

## 6. Quantities and units

### 6.1 Syntax

`NUMBER [SUFFIX]`, with or without a space (`10Cr`, `10 cr`, `1.5 Tn`). Suffixes are case-insensitive and have long
forms.

| Suffix | Multiplier | Long forms | Defined in |
|---|---|---|---|
| `K` | 1e3 | thousand | shared |
| `Lk`, `L` | 1e5 | lakh, lakhs, lac | `honba.india` |
| `Mn` | 1e6 | million | shared |
| `Cr` | 1e7 | crore, crores | `honba.india` |
| `Bn` | 1e9 | billion | shared |
| `Tn` | 1e12 | trillion | shared |
| `%` | 1e-2 | percent | shared |

### 6.2 Rules

- Conversion happens at parse time. `market cap between 5000 Cr and 2 Tn` becomes plain numbers (`5e10`, `2e12`) in
  `ScreenerFilterPredicate`. The wire contract stays unit-free and numeric.
- A suffix is valid only for metrics whose `unit` is money (`CURRENCY`, `PRICE`) or `SHARES`. `%` is valid for `PCT`
  and `RATIO`. Any other pairing, such as `rsi below 30 Cr`, is an exit-1 error.
- Currency markers `₹`, `Rs`, `INR` are accepted and checked against the market's currency (india is INR). Other
  currencies are rejected; FX is out of scope.
- Values are parsed as decimals and converted to `f64` once, so `1.1 Cr` is exactly `11000000.0`.

### 6.3 Market packs own the unit table

The language asks the active market pack for its suffix table (ADR 005). The India pack registers `Lk` and `Cr` in
addition to the shared set; a market without such conventions registers only the shared suffixes. Indian-market rules
therefore stay in the `india` module and the filter language stays generic.

### 6.4 Display

`--units auto` (default) formats by the market: Indian digit grouping (`₹12,34,567`) and `Cr`/`Lk` for large values
in India, `Mn`/`Bn`/`Tn` otherwise. `indian`, `intl` and `raw` force a choice. Machine formats (`json`, `ndjson`,
`parquet`) always emit raw numbers.

## 7. Architecture (DDD layering)

| Layer | Package | Contents | I/O |
|---|---|---|---|
| Domain | `honba/query/` | tokenizer, grammar, AST, quantity parser, alias resolution | none |
| Domain | `honba/screener/` | metric catalog, presets, predicate evaluation over bar series | none |
| Domain | `honba/india/` | `Lk`/`Cr` units, universes, calendar | none |
| Wire model | `honba/entities/screener.py` | `ScreenerScanRequest`, `ScreenerFilterPredicate`, `FilterOp`, `Timeframe`, … | none |
| Port | `honba/screener/ports.py` | `ScreenerSource.scan(request) -> ScreenerScanResponse` | defined here |
| Infrastructure | `honba/screener/sources/` | local Parquet catalog source; later an HTTP source for `POST /api/v1/screener/scan` | yes |
| Edge | `honba/cli/screener.py` | typer layer: parse options, call parser and port, render | yes |
| Edge | `honba/ai/mcp` | `screener_scan`, `screener_metrics`, `screener_presets` taking the wire model | yes |

The CLI does only: arguments → `ScreenerScanRequest` → port → render. The same request goes through the MCP tools,
so humans and agents share one contract. Evaluation reuses the indicator bank in `honba.strategies.indicators`
instead of reimplementing indicators.

The metric catalog is currently empty: the `MetricDefinition` model exists but no instances do. The v1 seed covers
price, volume, market cap, PE ratio, RSI, SMA, 52-week high and low, and sector, each with aliases, unit,
`filterable`, `sortable`, `has_timeframe` and `has_period`.

## 8. Determinism and the research loop

- `--asof` is required for historical scans and defaults to the latest bar date in the catalog, never the wall clock.
- `--print-request` makes any command a replayable artifact; `--journal` records request, response hash and `asof`
  in `data/journals` for the LLM research loop.
- Output order is stable: ties on the sort key break by `full_symbol`.

## 9. Testing plan

Written first (red), per the workspace TDD rule.

Unit (`python/tests/unit/`, no I/O):
- Quantities: every suffix, case, spacing, decimals, invalid pairing with a non-money metric, currency markers.
- Grammar: every comparison phrase, `between … and` versus conjunction, `and`/`or` precedence, `either … end`,
  alias longest-match and ambiguity, timeframe and period overrides, error carets and suggestions.
- Presets: 52-week boundaries at exactly 252 bars, fewer than 252, and ties.
- Catalog: `filterable`, `has_timeframe`, `has_period` enforcement.
- Evaluator: hand-built bar series per operator, including crossover edge cases.

Integration (`python/tests/integration/`, fixture Parquet catalog, no network):
- `CliRunner`: the English form, `--request`, and `--print-request` output agree.
- Source contract test, run against every `ScreenerSource` implementation.
- Golden vector in `schema/golden` for a CLI-built request, run through the Rust round-trip.
- Shell-safety: the documented example commands parse from separate words and from one quoted string.

Rust: if the evaluator is ever moved to Rust, unit tests go in `crates/<crate>/src/tests/` and integration tests in
`crates/<crate>/tests/`, and the Python and Rust results are compared on shared golden vectors.

## 10. Rollout

1. `honba/query/` quantity parser and grammar, with tests.
2. Metric catalog (with per-metric lookback) and presets, with tests.
3. Coverage ledger, interval algebra and gap planner (pure), then `BarStore`/`MarketDataProvider` ports with the
   in-memory and local implementations and the contract tests (section 12).
4. Evaluator and the `ScreenerSource`, wired through `DataService.ensure`.
5. `honba screener` commands (`scan`, `explain`, `metrics`, `presets`) and `honba data coverage|gaps|fetch`.
6. Knowledge pack generation and drift check, `LlmPort`, the `ask` pipeline with a scripted fake LLM (section 13).
7. Local-model and remote `LlmPort` implementations, eval harness, `save`/`run`, journal integration, MCP tools.
8. Follow-ups in sibling repos, flagged but not done here: `honba-docs` (CLI reference and filter language),
   `honba-frontend` (shares the request model and the same `scan` endpoint).

## 11. Open points

- Data source: superseded by section 12. The store is append-only and coverage-aware, and missing ranges are fetched
  from providers on demand. Tests never use the network (fake provider).
- Whether the full expression form (`close <= 52 week low * 1.05`) should follow the structured English form. It is
  deferred; the grammar leaves room for it in `operand`.
- The wire models in `honba/entities/screener.py` and `honba-entities/src/screener.rs` are currently uncommitted work
  in this repo. This design depends on them and should land after, or together with, that work.
- "In-process LLM" is read here as a model running inside the same process (a local model in Python, WebGPU in the
  browser), with remote APIs as an opt-in alternative behind the same port. Confirm that reading and pick the local
  runtime.
- Corporate-action handling (raw bars plus adjustment factors) needs its own ADR before step 3.
- The restricted-Python tier (13.2) ships after tier 1 is accurate on the eval set, behind an opt-in flag.

## 12. Data coverage and gap fill

### 12.1 Problem

The store holds RELIANCE 1d bars for 2022-01-01 to 2025-01-01. A request that reaches past either end, or into an
interior hole, must fetch only the missing range from known sources, append it, and then run the action. A request
fully inside the covered range must make no external call.

### 12.2 Storage

Behind a `BarStore` port, so the engine is an implementation detail. Recommended default:

- **Bars:** the Parquet catalog already in `honba-data`, as append-only immutable segments partitioned by
  `timeframe/symbol/year`. Reads dedupe on `(instrument_id, timeframe, ts)`.
- **Coverage ledger:** a small transactional table (SQLite, no new dependency) in `data/catalog/coverage.db`. One row
  per fetched range: `instrument_id`, `timeframe`, `adjustment`, `source`, `start_session`, `end_session`
  (half-open), `status`, `row_count`, `checksum`, `fetched_at`.
- `status` is `final`, `provisional` or `empty`. `empty` records a range that was fetched and has no data (before the
  listing date, suspension, holiday-only), so it is never fetched again. `provisional` marks the current session
  before the close; it is replaced, not appended, on the next request.
- **Adjustments:** bars are stored unadjusted. Splits, bonuses and dividends are a separate adjustment-factor series
  and `adjustment` is a ledger dimension. A new corporate action invalidates adjusted views only.

### 12.3 Required range and gap planning (pure domain)

1. Each catalog metric declares its lookback in bars (`52 week low` needs 252 sessions). The planner walks the filter
   AST and computes, per instrument, `required = [asof - max lookback, asof]` in **sessions** using the India
   calendar, so weekends and exchange holidays are never gaps.
2. `gaps = required - covered`, with interval arithmetic on half-open session ranges. Results are zero, one or two
   edge gaps plus any interior holes. Adjacent gaps merge, and gaps smaller than a configurable number of sessions
   merge into a neighbouring one to save requests.
3. Example: covered `[2022-01-01, 2025-01-01)`, requested `[2021-06-01, 2025-06-30]` gives gaps
   `[2021-06-01, 2022-01-01)` and `[2025-01-01, 2025-06-30]`. Only these two are fetched.

### 12.4 Ports and flow

- `MarketDataProvider.fetch(instrument, timeframe, range) -> list[Bar]`. Implementations: the NSE, BSE and AMFI
  loaders in `honba.research.data_loader`, and broker adapters through `honba.adapters.base`. Providers are an ordered
  list from config with fallback. Each provider declares its limits (for example intraday history depth), so an
  impossible request fails fast with a clear message.
- `BarStore`: `coverage()`, `read()`, `append()`.
- Application service `DataService.ensure(plan)`:
  1. fetch each gap with bounded concurrency, retry with backoff and rate limiting;
  2. validate every bar against the `Bar` invariants (finite, `low <= open, close <= high`, `volume >= 0`) and reject
     bad rows;
  3. write the segment to a temporary file, rename it, and update the ledger in one transaction;
  4. return a read view over the now-complete range.
- Re-running is idempotent. A partial failure keeps the gaps that succeeded and reports the ones that failed;
  `--on-missing` decides whether the action proceeds.
- The interval algebra and planner are domain code with no I/O. Providers and the store are infrastructure at the
  edge. The CLI only calls `DataService`.

### 12.5 CLI behaviour

- `--fetch auto` (default) fills gaps; `never` is offline mode and reports the shortfall (exit 2, or a warning with
  `--on-missing skip`); `force` refetches the requested range.
- `--max-fetch N` prompts before a universe scan triggers fetches for more than N instruments.
- `explain` prints the fetch plan, for example `RELIANCE 1d: fetch 2021-06-01..2021-12-31 and 2025-01-02..2025-06-30
  from nse`.
- `honba data coverage [SYMBOL]` shows covered ranges, `gaps` shows what a request would fetch, and `fetch` fills a
  range without running a scan.

### 12.6 Determinism

Fetched data is stored, so a rerun is offline-reproducible. The journal records a hash of the ledger rows the run
depended on. Tests use a fake provider, an in-memory store and a simulated clock; none touch the network.

## 13. Natural language to query (in-process LLM)

### 13.1 Principle

The LLM is a translator from free text into representations that already have validators. It never executes anything
and never replaces the parser. Treat its output as untrusted input (workspace rule for AI-generated artifacts).

### 13.2 Output tiers

| Tier | Output | Validated by | Status |
|---|---|---|---|
| 1 (default) | an English filter sentence in the section 5 grammar, or a `ScreenerScanRequest` JSON | the parser and the wire model | v1 |
| 2 (opt-in) | a restricted Python predicate for conditions the grammar cannot express | an AST allow-list, then the same evaluator | after tier 1 is accurate |
| 3 | a full strategy | the existing `ai/verification` and strategy tests | out of scope here |

Tier 2 allows only: column names (`open high low close volume`), whitelisted indicator-bank functions, arithmetic,
comparisons, boolean operators and numeric literals. It rejects imports, attribute access beyond the whitelist,
dunder names, comprehensions with unbounded size, and any call outside the whitelist. It has size and time limits and
no network, file or process access.

### 13.3 Pipeline for `honba screener ask`

```
free text -> select knowledge slice -> LLM (constrained) -> parse & validate
          -> on error: feed the caret message back (at most 2 repairs)
          -> show translation: English echo, resolved request, fetch plan
          -> confirm (--yes for scripts) -> DataService.ensure -> scan
```

Nothing runs on unvalidated output. The translation is also printed as the equivalent
`honba screener scan ...` command, so users learn the grammar and can replay it without the LLM.

### 13.4 LLM port and runtimes

`LlmPort.complete(messages, grammar | schema, seed, temperature=0) -> str`.

| Implementation | Where | Notes |
|---|---|---|
| Local in-process (Python) | the CLI/backend process | for example llama-cpp-python or transformers; grammar-constrained decoding makes syntactically invalid output impossible; default, so queries stay on the machine |
| Remote API | the CLI/backend | existing `ai` extra (litellm, anthropic, openai); opt-in |
| Browser in-process | the frontend | WebLLM or transformers.js on WebGPU, using the same knowledge pack and grammar |
| Scripted fake | tests | deterministic responses |

Model choice is config (`configs/ai/*.toml`). No model is bundled.

### 13.5 Knowledge pack: how the LLM knows honba internals

No fine-tuning and no reading source at runtime. A **generated, versioned, content-hashed artifact** built from the
single sources of truth:

- the grammar (EBNF, converted to a GBNF grammar and a JSON schema for constrained decoding);
- enums from the wire contract (`FilterOp`, `Timeframe`, `MetricPeriod`);
- the metric catalog: key, aliases, unit, value type, timeframe and period flags, lookback bars;
- presets, the unit table of each market pack, universe names, indicator specs from `indicator_spec`;
- the JSON Schema of the request and response (`schema/domain`);
- golden examples: free text, the English filter, and the resolved request. These are the same fixtures the tests and
  the eval use, so documentation cannot rot;
- short internals notes: the data flow (query, request, `DataService`, evaluator) and what is and is not supported, so
  the model can answer "cannot express that" instead of guessing.

Delivery: small local models have small context windows, so a retrieval step picks the relevant catalog entries and
the nearest few-shot examples for the query, always including the grammar and enums. Larger models can take more of
the pack. The same artifact is exposed three ways: a Python module `honba.ai.knowledge`, MCP resources in the gateway
for external agents, and generated TypeScript for the frontend (same workflow as `make schema`). `honba ai knowledge
check` fails CI on drift, like `make check-schema`.

### 13.6 Safety

- The parser, unit rules and catalog checks gate everything. Tier 2 code runs only inside the allow-list sandbox.
- User text and instrument names enter prompts as quoted data. Data-source strings (company names) can contain
  instructions and are never treated as instructions.
- `ask` is read-only: it can screen and fetch data, never place orders.
- The journal records the knowledge-pack hash, model id, seed, raw output and the validated request.

### 13.7 Frontend

The frontend uses the same pack and grammar. For v1 it validates by calling the backend
(`POST /api/v1/screener/explain`), so there is one parser and no drift. A generated TypeScript parser for offline use
is a later option, checked against the same golden vectors. Frontend changes are a follow-up in `honba-frontend`,
flagged but not made here.

### 13.8 Tests

- Unit: knowledge-pack generation is deterministic and contains every catalog key, alias and enum value (a drift
  test); retrieval picks the expected slice; the repair loop with a scripted fake LLM (fail twice then pass, and fail
  past the limit gives exit 1); the tier 2 validator rejects imports, dunder access, attribute escapes and
  non-whitelisted calls.
- Integration: `ask` end to end with the fake LLM through parser, `DataService` with the fake provider and the gap
  fill, and the scan; the `LlmPort` contract test across implementations; the eval harness over recorded responses.
- Real-model runs are marked `slow` and run in a separate CI step. Model accuracy (exact match and
  execution-equivalence rates on the golden set) is reported by `honba ai eval screener`, not asserted in unit tests.

### 13.9 External-agent integration (skill pack)

Reference: HydraTrade's `agent/` folder (`HydraLabs-RF/HydraTrade`). It takes the opposite route from 13.4: the LLM is an
external coding assistant (Cursor, Claude Code) that drives a thin CLI, and the app's knowledge is a markdown skill file.
This was read from the repo's README and plugin spec; the code itself was not inspected. We adopt its good parts
alongside the in-process path, so one knowledge pack serves both:

- **Skill files generated from the pack.** `honba ai skills install --target claude|cursor|both` writes
  `.claude/skills/honba-screener/SKILL.md` and the Cursor equivalent. The content comes from the knowledge pack
  (grammar, catalog, units, examples), not hand-written prose, and the drift check covers it.
- **The CLI is the tool surface.** `--format json`, `--print-request`, `explain` and the exit codes in 3.3 are what let
  an external agent drive Honba reliably. Commands that produce reports return their file paths.
- **Thin commands.** No screening or trading logic in the skill or CLI layer; they call the library, as in section 7.
- **Safety split.** The screener and data commands are read-only and safe for model invocation. Any future live or
  order command is excluded from model invocation (`disable-model-invocation`) and requires an explicit `--yes`.
- **Public and private layers.** The public skill ships the grammar and catalog. A git-ignored private overlay
  (`~/.config/honba/skills/`) holds the user's own universes, naming conventions and risk notes.

What we add beyond a prose skill: generated content, parser validation of whatever the model produces, and
grammar-constrained decoding on the local path.

