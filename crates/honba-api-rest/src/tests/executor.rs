//! `BacktestExecutor`: one backtest through the real kernel over an in-memory bar store.

use std::sync::Arc;

use honba_api::{
    JournalWriter, ResolvedBacktest, ResolvedRequest, ResolvedSweep, RunExecutor, RunJob, RunKind,
    RunOutcome,
};
use honba_data::{ColumnarSliceBuilder, Dataset, DatasetReader};
use honba_messages::{
    BarAggregation, BarSpecification, ErrorCode, ErrorDetail, Event, Exchange, InstrumentId,
    Message, PriceType, UnixNanos, SCHEMA_VERSION,
};
use serde_json::{json, Value};

use crate::{BacktestExecutor, StrategyRegistry};

const DAY_NS: u64 = 86_400_000_000_000;
const T0: u64 = 1_700_000_000_000_000_000;

fn tcs() -> InstrumentId {
    InstrumentId::new("TCS", Exchange::new("NSE"))
}

fn day_spec() -> BarSpecification {
    BarSpecification::new(1, BarAggregation::Day, PriceType::Last)
}

/// A wave that makes the 3/8 SMA crossover trade several times.
fn closes() -> Vec<f64> {
    (0..80)
        .map(|i| 100.0 + 12.0 * (f64::from(i) * 0.35).sin() + f64::from(i) * 0.05)
        .collect()
}

fn reader(spec: BarSpecification) -> Arc<DatasetReader> {
    let mut builder = ColumnarSliceBuilder::new(tcs(), spec);
    for (i, c) in closes().into_iter().enumerate() {
        let ts = UnixNanos::from_u64(T0 + i as u64 * DAY_NS);
        builder
            .push(ts, c - 0.5, c + 1.0, c - 1.0, c, 1_000.0)
            .unwrap();
    }
    let dataset = Dataset::from_slices(vec![builder.finish().unwrap()]).unwrap();
    Arc::new(DatasetReader::from_dataset(dataset))
}

fn executor() -> BacktestExecutor {
    BacktestExecutor::new(reader(day_spec()), StrategyRegistry::builtin())
}

fn job_with(strategy: &str, universe: &str, bar_spec: &str, seed: u64) -> RunJob {
    let ir = StrategyRegistry::builtin()
        .ir_for(strategy, &tcs(), "1d")
        .unwrap_or_else(|_| {
            StrategyRegistry::builtin()
                .ir_for("sma_crossover", &tcs(), "1d")
                .unwrap()
        });
    RunJob {
        run_id: honba_api::RunIdGenerator::default()
            .next(1, [0; 10])
            .unwrap(),
        kind: RunKind::Backtest,
        strategy_id: strategy.to_owned(),
        strategy: ir,
        request: ResolvedRequest::Backtest(ResolvedBacktest {
            strategy: strategy.to_owned(),
            universe: universe.to_owned(),
            start: UnixNanos::from_u64(T0),
            end: UnixNanos::from_u64(T0 + 90 * DAY_NS),
            bar_spec: bar_spec.to_owned(),
            initial_capital: 1_000_000.0,
            seed,
        }),
        seed,
    }
}

fn job(seed: u64) -> RunJob {
    job_with("sma_crossover", "TCS.NSE", "1d", seed)
}

#[derive(Default)]
struct Memory {
    records: Vec<Message>,
    flushed: bool,
}

impl JournalWriter for Memory {
    fn append(&mut self, msg: &Message) -> Result<(), ErrorDetail> {
        self.records.push(msg.clone());
        Ok(())
    }
    fn flush(&mut self) -> Result<(), ErrorDetail> {
        self.flushed = true;
        Ok(())
    }
}

struct Broken;

impl JournalWriter for Broken {
    fn append(&mut self, _: &Message) -> Result<(), ErrorDetail> {
        Err(
            ErrorDetail::new(ErrorCode::InternalError, "journal write failed: Other")
                .with_context(json!({"reason": "journal_write"})),
        )
    }
    fn flush(&mut self) -> Result<(), ErrorDetail> {
        Ok(())
    }
}

fn kinds(journal: &Memory) -> Vec<String> {
    journal
        .records
        .iter()
        .map(|m| {
            serde_json::to_value(m).unwrap()["event"]["type"]
                .as_str()
                .unwrap_or("?")
                .to_owned()
        })
        .collect()
}

fn lines(journal: &Memory) -> Vec<String> {
    journal
        .records
        .iter()
        .map(|m| serde_json::to_string(m).unwrap())
        .collect()
}

fn metrics_of(outcome: RunOutcome) -> (honba_api::BacktestMetrics, Value) {
    match outcome {
        RunOutcome::Backtest {
            metrics,
            assumptions,
        } => (metrics, assumptions),
        other => panic!("expected a backtest outcome, got {other:?}"),
    }
}

#[test]
fn a_run_journals_bars_orders_and_fills_at_the_current_schema_version() {
    let mut journal = Memory::default();
    executor().execute(job(42), &mut journal).unwrap();
    let kinds = kinds(&journal);
    assert_eq!(kinds.iter().filter(|k| *k == "bar").count(), 80);
    assert!(kinds.iter().any(|k| k == "order"), "{kinds:?}");
    assert!(kinds.iter().any(|k| k == "order_filled"), "{kinds:?}");
    assert!(journal
        .records
        .iter()
        .all(|m| m.schema_version() == SCHEMA_VERSION));
    assert_eq!(SCHEMA_VERSION, 4);
    assert!(
        journal.flushed,
        "the journal is flushed at the end of the run"
    );
    // Kernel order: the first record is the first bar.
    assert!(matches!(journal.records[0].event(), Event::Bar(_)));
}

#[test]
fn every_fill_follows_its_order_in_the_journal() {
    let mut journal = Memory::default();
    executor().execute(job(42), &mut journal).unwrap();
    let mut ordered = std::collections::HashSet::new();
    let mut fills = 0;
    for message in &journal.records {
        match message.event() {
            Event::Order(order) => {
                ordered.insert(order.order_id().as_str().to_owned());
            }
            Event::OrderFilled { order_id, .. } | Event::OrderPartiallyFilled { order_id, .. } => {
                assert!(
                    ordered.contains(order_id.as_str()),
                    "{order_id:?} before its order"
                );
                fills += 1;
            }
            _ => {}
        }
    }
    assert!(fills >= 2);
}

#[test]
fn the_outcome_has_metrics_and_stated_assumptions() {
    let mut journal = Memory::default();
    let outcome = executor().execute(job(42), &mut journal).unwrap();
    let (metrics, assumptions) = metrics_of(outcome);
    assert!(metrics.trades >= 1, "{metrics:?}");
    assert!(metrics.net_pnl.is_finite() && metrics.sharpe.is_finite());
    assert!((0.0..=1.0).contains(&metrics.max_drawdown));
    assert!((metrics.total_return - metrics.net_pnl / 1_000_000.0).abs() < 1e-9);
    assert!(assumptions["not_modelled"]
        .as_array()
        .is_some_and(|a| !a.is_empty()));
    assert!(assumptions["timing"].is_string());
}

#[test]
fn the_same_job_gives_byte_identical_journal_lines() {
    let (mut a, mut b) = (Memory::default(), Memory::default());
    let first = executor().execute(job(42), &mut a).unwrap();
    let second = executor().execute(job(42), &mut b).unwrap();
    assert!(!a.records.is_empty());
    assert_eq!(lines(&a), lines(&b));
    assert_eq!(first, second);
}

#[test]
fn the_journal_body_carries_no_wall_clock_run_id_or_path() {
    let mut journal = Memory::default();
    let job = job(42);
    let id = job.run_id.to_string();
    executor().execute(job, &mut journal).unwrap();
    let text = lines(&journal).join("\n");
    assert!(!text.contains(&id));
    assert!(!text.contains("journals"));
    assert!(!text.contains("/"));
}

#[test]
fn an_unregistered_strategy_is_an_error_not_a_panic() {
    let mut job = job(42);
    job.strategy_id = "sha256:abc".into();
    job.strategy.manifest.name = "mystery".into();
    let err = executor().execute(job, &mut Memory::default()).unwrap_err();
    assert_eq!(err.code, ErrorCode::ValidationInvalidRequest);
    assert_eq!(err.context.unwrap()["field"], "strategy");
}

#[test]
fn a_catalog_ir_runs_through_its_registered_name() {
    // A compiled manifest named after a registered strategy has a Rust implementation.
    let mut job = job(42);
    job.strategy_id = "sha256:abc".into();
    let mut journal = Memory::default();
    executor().execute(job, &mut journal).unwrap();
    assert!(!journal.records.is_empty());
}

#[test]
fn a_sweep_job_is_not_implemented_by_this_executor() {
    let mut job = job(42);
    job.kind = RunKind::Sweep;
    job.request = ResolvedRequest::Sweep(ResolvedSweep {
        strategy: "sma_crossover".into(),
        params: json!({"fast": [1, 2, 1]}),
        trials: 2,
        seed: 42,
    });
    let err = executor().execute(job, &mut Memory::default()).unwrap_err();
    assert_eq!(err.code, ErrorCode::NotImplemented);
}

#[test]
fn no_bars_in_the_window_is_market_data_unavailable() {
    let mut job = job(42);
    if let ResolvedRequest::Backtest(r) = &mut job.request {
        r.start = UnixNanos::from_u64(1);
        r.end = UnixNanos::from_u64(2);
    }
    let err = executor().execute(job, &mut Memory::default()).unwrap_err();
    assert_eq!(err.code, ErrorCode::MarketDataUnavailable);
    assert_eq!(err.context.unwrap()["reason"], "no_data");
}

#[test]
fn a_timeframe_the_store_does_not_hold_is_unsupported() {
    let job = job_with("sma_crossover", "TCS.NSE", "5m", 42);
    let err = executor().execute(job, &mut Memory::default()).unwrap_err();
    assert_eq!(err.code, ErrorCode::Unsupported);
}

#[test]
fn a_universe_that_is_not_one_instrument_is_refused() {
    for universe in ["TCS", "TCS.NSE,INFY.NSE", ""] {
        let job = job_with("sma_crossover", universe, "1d", 42);
        let err = executor().execute(job, &mut Memory::default()).unwrap_err();
        assert_eq!(
            err.code,
            ErrorCode::ValidationInvalidRequest,
            "{universe:?}"
        );
        assert_eq!(err.context.unwrap()["field"], "universe");
    }
}

#[test]
fn a_journal_failure_fails_the_run_with_its_reason() {
    let err = executor().execute(job(42), &mut Broken).unwrap_err();
    assert_eq!(err.code, ErrorCode::InternalError);
    assert_eq!(err.context.unwrap()["reason"], "journal_write");
}

#[test]
fn a_buy_and_hold_run_has_one_unpaired_fill_and_no_round_trips() {
    let job = job_with("buy_and_hold", "TCS.NSE", "1d", 5);
    let mut journal = Memory::default();
    let (metrics, _) = metrics_of(executor().execute(job, &mut journal).unwrap());
    assert_eq!(metrics.trades, 0);
    assert_eq!(metrics.net_pnl, 0.0);
    let fills = kinds(&journal)
        .iter()
        .filter(|k| *k == "order_filled")
        .count();
    assert_eq!(fills, 1);
}
