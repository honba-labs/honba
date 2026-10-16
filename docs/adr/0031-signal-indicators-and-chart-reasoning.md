# ADR 0031: Chart Signals — Buy/Sell Detectors with Inspectable Reasons

Date: 2026-10-16. Status: **proposed**. Roadmap stories: E12-S0..E12-S8. Owner: `honba` (+ `honba-frontend`, `honba-strategies`).

## Context

The product goal is a chart that marks buy/sell points from common-knowledge signals — price action, volume, RSI and the like — and shows the user *why* each one fired, for both **day trading (intraday)** and **swing trading (positional)**. Several pieces already exist and shape the design:

- **`honba-indicators` (L3)** holds the streaming `Indicator` trait and the indicator set (`sma`, `ema`, `rsi`, `macd`, `bollinger`, `atr`, `williams_r`, `stochastic`, `donchian`, plus the `momentum/`, `trend/`, `volatility/`, `volume/` families). Its **render contract** (`render.rs`: `Presentation`, `Plot`, `PlotKind::{Line,Histogram,Marker}`, `ColorRole`, `Zone`) already has a sparse **marker** that fires when a condition flips, and states the boundary plainly: *"nothing here can place an order: an indicator never emits execution columns, and a signal becomes an order only inside a strategy."* But that contract has a single `SignalKind` (`Flip`), no direction, no strength and no reason text.
- **`honba-api-wasm`** computes indicators, screener predicates and journal replay over a whole series, free of any browser or wasm-bindgen type, and `list_indicators_json` embeds the serialized render contracts so the chart consumes one source of truth. This is the template for a signal surface.
- **`honba-indicators::screener.rs`** is a predicate evaluator over bars with shared golden vectors (`schema/conformance/screener_scan.json`) and a Python twin — the basis for *searching* signals across a universe.
- **The frontend (`honba-frontend`)** draws candles with the vendored `openalgo-charts`, which already has a full **series-marker** API: `SeriesMarker` (shapes `arrowUp`/`arrowDown`/`labelUp`/`labelDown`, positions `aboveBar`/`belowBar`/`atPrice`, `text`, hit-testing and an `id`), plus `IndicatorMarker` / `IndicatorDrawing` / `IndicatorBackgroundSpec` that a study returns, and `createMarkers()`. 502 vendored study descriptors (`src/indicators/**`, GPL-3.0/MPL-2.0/MIT) are auto-discovered by `core/indicators/loader.ts`; many are "setup"/"scanner" studies that already paint buy/sell markers, but without structured reasons.
- **`honba-strategies`** already separates `intraday/` (gap_fill, opening_range_breakout, pivot_bounce, vwap_scalp) from `swing/` (earnings_momentum, relative_strength, sector_leadership), with step manifests and a registry — the natural home for horizon presets that consume signals.
- **Codegen** (`honba-codegen` registry → `schema/domain`, `schema/openapi`, `schema/mcp`, the Python `.pyi`, the frontend TS; ADR 0014) is how a new wire type reaches every surface.

The gap: there is no first-class, **explainable signal** — one that carries its direction, strength and the **reasons** it fired, is computed identically for a backtest, the server and the browser, and is presented as chart markers with an inspection panel. The vendored studies render markers but not reasons; the render contract marks a flip but says nothing about why.

## Decision

### 1. A `Signal` is a pure, bar-anchored value with mandatory reasons

```text
Signal {
  instrument: InstrumentId,
  timeframe: String,                     // e.g. "5m", "1d"
  horizon: Intraday | Swing,
  t: UnixNanos,                          // close time of the confirming bar
  direction: Buy | Sell,
  strength: f64,                         // 0..1, normalized confluence
  anchor_price: f64,                     // bar close, or the trigger level for a level break
  ruleset: String,                       // "signals/day:v1" | "signals/swing:v1"
  reasons: [Reason],                     // never empty
  tags: [String],                        // detector codes, e.g. "orb", "vwap_reclaim"
  evidence: Value,                       // numbers the reason text cites, for inspection
}
Reason {
  code: String,                          // stable, from the frozen ontology (§4)
  group: Trend | Momentum | PriceAction | Volume | Volatility | Level | Session | RelativeStrength,
  polarity: Bullish | Bearish,
  weight: f64,                           // the term this detector contributed to `strength`
  label: String,                         // short human text, e.g. "20-EMA above 50-EMA"
  detail: String,                        // one sentence with the numbers
}
```

- **Reasons are mandatory.** A detector that fires contributes exactly one `Reason`; the UI shows them verbatim. A signal with no reason is a bug, not a weaker signal.
- **Deterministic and pure.** No clock, no network, no allocation beyond the series (the `honba-indicators` rule). Same bars and params give byte-identical signals, so a signal is reproducible and testable.

### 2. Confirmation on close: no lookahead, no repaint

A signal at bar *N* uses only bars `<= N` and is emitted only when bar *N* has **closed**. This is the same look-ahead guarantee E4-S4 already pins for strategies (`truncation_property`), now applied to signals: truncating future bars never changes an earlier signal. Intraday session cutoffs communicate "no new signal after *T*" by emitting nothing, not by retracting. A signal is never revised in place.

### 3. New crate `honba-signals` (L4); detectors compose `honba-indicators`

- A pure crate above `honba-indicators` (and `honba-entities`), with a `catalog()` of detector descriptors and rulesets declared once, mirroring `honba-indicators::render`. Detectors are small functions over a rolling window of bars and indicator values (`Option<Reason>`); the **confluence scorer** aggregates fired reasons into `strength`.
- **Two rulesets, one engine.** `signals/day:v1` (intraday) and `signals/swing:v1` (positional) differ in timeframe, detector set, parameters and session rules, not in machinery (§5). Weights live in the catalog and are auditable.
- No new dependency edge beyond `honba-signals -> {honba-indicators, honba-entities}`; the layering script (`scripts/dependency_graph.py`) is updated.

### 4. A frozen reason ontology

`code` values are a fixed, versioned list declared in the catalog (e.g. `ema_cross_up`, `rsi_oversold_turn`, `bullish_engulfing`, `break_of_structure`, `rel_volume_spike`, `bb_squeeze_breakout`, `reclaim_prior_day_high`, `orb_break_up`, `vwap_reclaim`, `rs_new_high`, `weekly_confirmation`). The list is frozen in E12-S0 so goldens and UI labels are stable; adding a code is an additive change, renaming or reusing one is not.

### 5. Horizons: Day (intraday) and Swing (positional)

| | Day | Swing |
|---|---|---|
| Bars | 1m/3m/5m/15m (IST session) | 1d (+ 1w higher timeframe) |
| Filters | session VWAP, ATR, rel-volume vs time of day | price vs 50/200-DMA, weekly trend, RS vs index |
| Session rules | skip the first 1–2 bars and the last ~15 min; no signal after the cutoff | none |
| Anchors | OR break, VWAP reclaim, gap fill, pivot bounce | MA cross, base/52w breakout, RSI-divergence reversal, pullback to MA |
| Noise control | min rel-volume, min ATR, per-side cooldown of K bars | require ≥1 trend and ≥1 momentum/volume reason; weekly confirmation |

Multi-timeframe signals (e.g. a 5m trigger gated by the 60m trend) reuse the session-calendar aggregation from ADR 0024 (E4-S8), not a second bar model.

### 6. Surfaces: WASM, wire, REST, scanner (one engine, three callers)

- **`honba-api-wasm::signals`** (next to `indicators.rs`/`screener.rs`): compute signals over a series, output length == input, absent before warm-up; plus `list_signal_presets_json()` embedding the catalog, like `list_indicators_json`.
- **Wire DTOs** (`SignalDto`, `ReasonDto`, `SignalScanRequest/Response`) are Serde types registered in `honba-codegen` so `schema/domain`, `schema/openapi`, `schema/mcp`, the Python `.pyi` and the frontend TS are regenerated (ADR 0014). This is an **additive** change (ADR 0012).
- **`GET /signals?instrument&timeframe&horizon=<day\|swing>&min_strength=&rules=`** in `honba-api-rest`, evaluated by the same crate, for the chart and for scanning. Optional SSE streaming of new signals reuses ADR 0021.
- **Scanner**: a `signal` metric added to `honba-indicators::screener` (and the Python twin), so "search buy/sell signals across the universe" is one call, pinned by a golden vector.

### 7. Rendering and the reasons panel (frontend)

- A **"Signals" study** returns `IndicatorMarker`s (`arrowUp`/`arrowDown`, or `labelUp`/`labelDown` "Buy"/"Sell", `id` = the signal id) plus `IndicatorBackgroundSpec` (regime shading) and `IndicatorDrawing` (support/resistance zones). Client-side via the WASM loader (E11-S5); identical output to the REST path.
- A **Signals panel/drawer** lists signals with reason cards; a click on a marker (the API hit-tests) focuses the card. The card shows direction, bar time, strength, and one row per `Reason` (`group · label`, `detail`, the cited numbers), grouped by detector family, with weights.
- A **Day/Swing preset** switch in the toolbar (`core/workbench/store.ts`) binds timeframe + detector toggles.
- This layer is named **"Signals"** to stay distinct from the existing `BuySellButtons`, which is an order-ticket widget, not a signal.

### 8. Clean-room and licensing

Detectors are common-knowledge, written clean-room. No vendored study's source (`GPL-3.0`/`MPL-2.0`/`MIT` under `honba-frontend/src/indicators/**`) is copied or mechanically translated. A vendored study may be used only as an **optional agreement signal** (a count of which studies flagged the same bar), never as an input to a reason's numbers.

### 9. Scope boundary (unchanged from the render contract)

A `Signal` is research and presentation only. It never places an order, never emits execution columns, and is not a strategy. A signal becomes an order only inside a strategy, and any strategy that consumes signals goes through the same risk stage and journal (ADR 0018, E5-S7).

### 10. Conformance vectors

New files `schema/conformance/signals_day.json` and `schema/conformance/signals_swing.json`, shaped like `screener_scan.json`/`risk_decisions.json`: `{ fixture_version, type, description, cases: [{ name, bars[], params, expect: { signals[] } }] }`, where each expected signal pins `t`, `direction`, `tags`, reason `code`s (order-insensitive) and `strength` within a tolerance. They are replayed by the Rust crate, the WASM surface and the Python twin, and they assert confirmation-on-close (a signal at *N* is unchanged when bars after *N* are appended).

## Consequences

- **A reason is a first-class output**, so the detector set stays small and explainable rather than growing to match the vendored 502. New detectors must produce a `code`, `label`, `detail` and `weight`.
- **One engine, parity by construction**: the same crate runs the backtest, the server scan and the browser overlay, so a signal on the chart is the signal a strategy would have seen.
- **Additive wire/schema change** only; existing indicator and screener contracts are untouched. The render contract's `SignalKind` may later gain a `Signal`-driven marker kind, but v1 keeps signals in their own study.
- **Day trading depends on intraday bars**, which today come from `--data-dir` or a feed; without an intraday source the day preset has nothing to run on (see Open items).
- **Frontend work is bounded** because markers, drawings and shading already exist; the new UI is a study, a reasons panel and a preset switch.
- **`honba-strategies` gains per-horizon strategies** that consume the same signals, keeping chart and backtest in agreement.

## Open items for code review

- Intraday data source: which feed/bars supply 1m–15m data (ADR 0028 D10 territory) before the day preset is usable end to end.
- Multi-timeframe trigger/trend pairing: exactly which higher timeframe gates which detector, per horizon.
- Strength formula: the group weights and the normalization, kept declarative in the catalog.
- Whether the render contract's `SignalKind` is widened or signals stay a separate study type.
- Alerting (email/webhook, ADR 0026) and strategy consumption share the engine but are separate stories.
- Intraday session boundaries and holiday calendar; "no overnight" enforcement point.

## Decision log

- 2026-10-16: proposed.
