# Changelog

## [Unreleased]

### Strategy contract (E0-S3, ADR 008)

- Python `Strategy` is an ABC with `on_start`, `on_bar`, `on_quote`, `on_trade`, `on_fill` and
  `on_stop` (the new hooks default to no-ops) and acts through `self.ctx`, a `StrategyContext`
  (clock, positions, cash, busy, instrument lookup, submit). New: `LedgerContext`,
  `StrategyRunner`, `honba.strategies.reference`, `honba.strategies.testing.BarCloseFills`,
  domain `QuoteTick` / `TradeTick` / `Instrument`, `wire.loads_many`.
- Compatibility (until 0.3): existing subclasses work unchanged, with or without
  `super().__init__()`; overriding `drain_intents`, `handle_fill` or `handle_rejected` emits a
  `DeprecationWarning` and is still honoured.
- Breaking (Rust): `Strategy` hooks take `ctx: &mut dyn StrategyContext`, market-data hooks lose
  `ts_init`, and `drain_intents` is gone (use `ctx.submit`). New: `LedgerContext`,
  `ContractProbe`, `StrategyRunner::{with_context, context, submitted}`.
- `honba._honba.run_strategy` runs a Rust reference strategy over JSON wire messages.
- `schema/conformance/strategy_contract.json` is run by the Rust, Python and cross-language suites.

### Performance

- Streaming indicators are now O(1) per update instead of O(period): Bollinger bands, z-score,
  `RollingStd` (volatility helpers), WMA, correlation, beta, covariance, LSMA and linear
  regression. They share the helpers in `honba.strategies.indicators._rolling`
  (`RollingSum`, `RollingMoments`, `RollingPairMoments`, `RollingLinReg`): shifted Welford with
  removal, an exactly carried position moment for regressions, and exact `fsum` rebuilds (every
  `max(1000, 4 * period)` updates, and on guard conditions such as an outlier leaving the window
  or drift of the newest value). Guard rebuilds are rate limited so adversarial input cannot
  force O(period) work on every update. Public APIs, warm-up lengths, `reset()` and degenerate
  results (zero variance gives 0.0) are unchanged.
- The steady-state update path of these helpers is inlined (plain-tuple `update_raw`, lazily
  topped-up rebuild credit, closed-form period-2 paths); at periods >= 20 every converted
  indicator is faster than the old O(period) code, and several times faster at period 100-300.

### Changed (behavior)

- A non-finite value (NaN or infinity), or a value with `|x| > 1e150`, in the window now makes
  the affected indicator report NaN until it has left the window; the running state is then
  rebuilt exactly, so a bad bar never poisons later results.
- The old correlation returned 1.0, and the old beta 0.0 (when only the benchmark window held the
  bad value), for some windows containing NaN; both now return NaN.
- Indicators that previously raised `OverflowError` on huge values (`|x| > 1e150` squares or
  sums overflowing) now return NaN instead.
- A window of identical values is exactly degenerate (zero variance, zero covariance, exactly flat
  regression line). The old two-pass code returned float noise when the mean of the constant
  window was not exactly representable (for example a constant 0.1).
- Linear regression residual std is computed from running sums, so a near-perfect fit is accurate
  to about 3e-8 window standard deviations (was exact two-pass); lsma, slope and value keep a
  relative error around 1e-12.

### Tests

- New `slow` pytest marker (registered in `python/pyproject.toml`) for long accuracy and
  amortised-cost sweeps; `pytest -m "not slow"` is the fast set.
