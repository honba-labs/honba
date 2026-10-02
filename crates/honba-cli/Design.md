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
2. Metric catalog and presets, with tests.
3. Evaluator and the local Parquet source, with the contract test.
4. `honba screener` commands (`scan`, `explain`, `metrics`, `presets`).
5. `save`/`run`, journal integration, MCP tools.
6. Follow-ups in sibling repos, flagged but not done here: `honba-docs` (CLI reference and filter language),
   `honba-frontend` (shares the request model and the same `scan` endpoint).

## 11. Open points

- Data source for v1: the local Parquet catalog under `data/catalog` is assumed. A live feed is excluded because tests
  must not use the network.
- Whether the full expression form (`close <= 52 week low * 1.05`) should follow the structured English form. It is
  deferred; the grammar leaves room for it in `operand`.
- The wire models in `honba/entities/screener.py` and `honba-entities/src/screener.rs` are currently uncommitted work
  in this repo. This design depends on them and should land after, or together with, that work.
