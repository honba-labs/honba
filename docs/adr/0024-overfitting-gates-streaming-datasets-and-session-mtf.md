# ADR 0024: Overfitting Gates, Chunked Streaming Datasets, and Session-Aware MTF Aggregation

Date: 2026-10-11. Status: **accepted**. Roadmap stories: implements E4-S6, E4-S7, and E4-S8.

## Context

Following the E4 Research Pipeline roadmap:
1. **Overfitting Gates (E4-S6)**:
   - Pure in-sample backtest Sharpe ratios suffer from data snooping and selection bias (multiple testing problem).
   - A strategy must pass empirical statistical validation gates before being trusted:
     - **Deflated Sharpe Ratio (DSR)** (Bailey & López de Prado, 2014): Adjusts observed Sharpe for non-normality (skewness, kurtosis) and multiple testing across $N$ trials.
     - **Probability of Backtest Overfitting (PBO)** (Bailey et al. CSCV): Combinatorially Symmetric Cross-Validation over time partitions.
     - **Walk-Forward Efficiency (WFE)**: Out-of-sample annualized return vs in-sample annualized return across rolling windows.
     - **One-Shot Holdout**: Strict final out-of-sample block with multiple-testing penalty.
     - **Block-Bootstrap Monte Carlo**: Resampling contiguous return blocks ($b \approx \sqrt[3]{T}$) to simulate empirical drawdown and ruin distributions while preserving serial correlation.
     - **Cost-Stress Test**: Evaluating performance sensitivity under $2\times$ and $3\times$ cost schedules.
   - The validation result must be carried in a structured `ValidationBlock` on `BacktestResponse` and `SweepReportResponse`.
2. **Chunked Streaming Datasets (E4-S7)**:
   - Sweeps and backtests over large multi-year multi-asset universes require memory-bounded streaming rather than loading all slices into memory simultaneously.
   - Datasets require a canonical, content-derived `DatasetId` string.
   - Slices must support chunked streaming batches (`DatasetChunk`, `iter_chunks`) with `chunks_cover_slice` invariant.
3. **Session-Aware Multi-Timeframe Aggregation (E4-S8)**:
   - Aggregating lower-timeframe bars (e.g. 1m, 5m, 1h) into higher-timeframe bars (e.g. 1D, 1W) must align strictly with the exchange session calendar (`MarketCalendar`).
   - Invariant: Zero look-ahead. A higher-timeframe bar must **only** be emitted when the session or interval is completely closed (`htf_bar_only_when_complete`).

## Decisions

### 1. Overfitting Gates in `honba-analytics` (E4-S6)
- Implement `Dsr`:
  $$DSR = \Phi\left( \frac{(\widehat{SR} - SR_0) \sqrt{T-1}}{\sqrt{1 - \gamma_3 \widehat{SR} + \frac{\gamma_4 - 1}{4} \widehat{SR}^2}} \right)$$
  where expected maximum Sharpe $SR_0$ is:
  $$SR_0 \approx \sqrt{V} \left( (1 - \gamma)\Phi^{-1}\left(1 - \frac{1}{N}\right) + \gamma \Phi^{-1}\left(1 - \frac{1}{N e}\right) \right)$$
  with Euler-Mascheroni constant $\gamma \approx 0.57721566$.
- Implement `Pbo`: CSCV combinatorial splitting across $S$ sub-periods (default $S=16$, $\binom{16}{8}=12,870$ splits) to measure relative rank degradation out-of-sample.
- Implement `WalkForward`: Rolling/anchored in-sample / out-of-sample windows yielding `wfe = ann_ret_oos / ann_ret_is` and stability metrics.
- Implement `BlockBootstrap`: Stationary moving block bootstrap with deterministic seed, producing 95% and 99% drawdown distributions and ruin probability.
- Implement `CostStress`: Rerunning equity calculations at $1.0\times, 2.0\times, 3.0\times$ friction multipliers.
- Implement `ValidationBlock`: Aggregating gate results with boolean `passed` verdicts and threshold comparisons.

### 2. Validation Block in API DTOs (E4-S6)
- Add optional `validation: Option<ValidationBlock>` to `BacktestResponse` and `SweepReportResponse` in `honba-api`.
- Export schemas via `honba-codegen`.

### 3. Chunked Streaming Datasets and `DatasetId` (E4-S7)
- Introduce `DatasetId` formatted as `{exchange}:{universe_or_symbol}:{timeframe}:{hash_or_range}`.
- Add `chunk_size` and chunked iterator to `ColumnarSlice` and `Dataset`.
- Ensure deterministic replay across chunks.

### 4. Multi-Timeframe Session Aggregator (E4-S8)
- Implement `SessionBarAggregator` in `honba-market` / `honba-feed`:
  - Consumes lower-timeframe bars.
  - Aligns bars to session open/close boundaries defined by `MarketCalendar`.
  - Dispatches higher-timeframe bar only upon session boundary close.

## Consequences
- Overfitting detection becomes standard and verifiable across Python, CLI, and REST.
- Memory usage during backtests on large datasets remains bounded.
- Multi-timeframe strategies are guaranteed zero-lookahead by construction.
