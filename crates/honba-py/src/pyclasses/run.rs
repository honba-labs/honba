//! `run_strategy`: run a Rust reference strategy over JSON wire messages (ADR 008).
//!
//! The cross-language half of the strategy conformance suite and a
//! machine-readable entry point for research and agents: JSON in (wire
//! `Message`s, params, instruments), JSON out (intents, fills, observations,
//! positions, cash), in the shape of `schema/conformance/strategy_contract.json`.

// PyO3 0.22 `#[pyfunction]` expansion trips this lint on `PyResult` returns
// (same allowance as `domain.rs` and `wire.rs`).
#![allow(clippy::useless_conversion)]

use honba_engine::{AlgoError, ExecutionEngine, Handler};
use honba_entities::{Currency, Instrument, InstrumentKind, Trade};
use honba_messages::{InstrumentId, Message, Order};
use honba_sim::BarFillEngine;
use honba_strategy::{
    BuyAndHold, ContractProbe, LedgerContext, SmaCrossover, Strategy, StrategyContext,
    StrategyRunner,
};
use pyo3::exceptions::PyValueError;
use pyo3::prelude::*;
use pyo3::types::PyModule;
use serde::de::DeserializeOwned;
use serde::Deserialize;
use serde_json::{json, Value};

/// Strategy names accepted by [`run_strategy_json`].
pub const STRATEGIES: [&str; 3] = ["contract_probe", "buy_and_hold", "sma_crossover"];

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct ProbeParams {
    instrument_id: InstrumentId,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct BuyAndHoldParams {
    instrument_id: InstrumentId,
    quantity: f64,
}

#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct SmaParams {
    instrument_id: InstrumentId,
    fast: usize,
    slow: usize,
    quantity: f64,
}

fn parse<T: DeserializeOwned>(what: &str, text: &str) -> Result<T, String> {
    serde_json::from_str(text).map_err(|e| format!("invalid {what}: {e}"))
}

/// Parses instrument metadata in the conformance fixture's shape:
/// `{"instrument_id", "kind", "currency", "lot_size", "tick_size"}`.
pub(crate) fn parse_instrument(v: &Value) -> Result<Instrument, String> {
    let id: InstrumentId = serde_json::from_value(v["instrument_id"].clone())
        .map_err(|e| format!("invalid instrument_id: {e}"))?;
    let kind = match v["kind"].as_str() {
        Some("equity") => InstrumentKind::Equity,
        Some("future") => InstrumentKind::Future,
        Some("option") => InstrumentKind::Option,
        Some("fx") => InstrumentKind::Fx,
        Some("index") => InstrumentKind::Index,
        Some("mutual_fund") => InstrumentKind::MutualFund,
        other => return Err(format!("invalid instrument kind {other:?}")),
    };
    let currency: Currency = serde_json::from_value(v["currency"].clone())
        .map_err(|e| format!("invalid currency: {e}"))?;
    let positive = |name: &str| match v[name].as_f64() {
        Some(x) if x.is_finite() && x > 0.0 => Ok(x),
        _ => Err(format!("{name} must be a positive number")),
    };
    Ok(Instrument::new(
        id,
        kind,
        currency,
        positive("lot_size")?,
        positive("tick_size")?,
    ))
}

/// `BarFillEngine` that refuses an order before any bar instead of filling
/// it at 0.0 (the engine's known gap, ADR 006), like the Python mirror.
#[derive(Clone)]
struct PricedBarFill(BarFillEngine);

impl ExecutionEngine for PricedBarFill {
    fn submit(&mut self, order: Order) -> honba_engine::Result<()> {
        if self.0.last_price().is_none() {
            return Err(AlgoError::Component(format!(
                "order {} submitted before any bar: no price to fill at",
                order.order_id().as_str()
            )));
        }
        self.0.submit(order)
    }

    fn cancel(&mut self, order_id: &str) -> honba_engine::Result<()> {
        self.0.cancel(order_id)
    }

    fn drain_fills(&mut self) -> honba_engine::Result<Vec<Trade>> {
        self.0.drain_fills()
    }
}

fn drive<S: Strategy>(
    strategy: S,
    ctx: LedgerContext,
    messages: &[Message],
) -> Result<(S, Value), String> {
    let err = |e: AlgoError| e.to_string();
    let mut execution = BarFillEngine::new();
    let mut runner = StrategyRunner::with_context(strategy, PricedBarFill(execution.clone()), ctx);
    runner.on_start().map_err(err)?;
    for msg in messages {
        execution
            .on_event(msg.event(), msg.ts_init())
            .map_err(err)?;
        runner.on_event(msg.event(), msg.ts_init()).map_err(err)?;
    }
    runner.on_stop().map_err(err)?;

    let intents: Vec<Value> = runner
        .submitted()
        .iter()
        .map(|s| json!({"ts_init": s.ts_init, "intent": s.intent}))
        .collect();
    let fills = serde_json::to_value(runner.fills()).map_err(|e| e.to_string())?;
    let positions: Vec<Value> = runner
        .context()
        .positions()
        .into_iter()
        .map(|(id, q)| json!({"instrument_id": id, "quantity": q}))
        .collect();
    let outcome = json!({
        "intents": intents,
        "fills": fills,
        "observations": [],
        "positions": positions,
        "cash": runner.context().cash(),
    });
    let (strategy, _, _) = runner.into_parts();
    Ok((strategy, outcome))
}

/// Runs the Rust reference `strategy` (one of [`STRATEGIES`]) over `events`
/// (a JSON array of wire `Message`s) with `BarFillEngine` and returns the
/// outcome as JSON. `params` is a JSON object for the strategy's
/// constructor; `instruments` a JSON array of instrument metadata.
pub fn run_strategy_json(
    strategy: &str,
    params: &str,
    events: &str,
    instruments: &str,
    initial_cash: f64,
) -> Result<String, String> {
    let messages: Vec<Message> = parse("events", events)?;
    let raw_instruments: Vec<Value> = parse("instruments", instruments)?;
    let mut ctx = LedgerContext::with_cash(initial_cash);
    for raw in &raw_instruments {
        ctx.add_instrument(parse_instrument(raw)?);
    }

    let outcome = match strategy {
        "contract_probe" => {
            let p: ProbeParams = parse("contract_probe params", params)?;
            let (probe, mut outcome) = drive(ContractProbe::new(p.instrument_id), ctx, &messages)?;
            outcome["observations"] =
                serde_json::to_value(probe.observations()).map_err(|e| e.to_string())?;
            outcome
        }
        "buy_and_hold" => {
            let p: BuyAndHoldParams = parse("buy_and_hold params", params)?;
            drive(BuyAndHold::new(p.instrument_id, p.quantity), ctx, &messages)?.1
        }
        "sma_crossover" => {
            let p: SmaParams = parse("sma_crossover params", params)?;
            if !(p.fast > 0 && p.fast < p.slow) {
                return Err("sma_crossover: fast must be positive and less than slow".into());
            }
            let s = SmaCrossover::new(p.instrument_id, p.fast, p.slow, p.quantity);
            drive(s, ctx, &messages)?.1
        }
        other => {
            return Err(format!(
                "unknown strategy '{other}'; expected one of {STRATEGIES:?}"
            ))
        }
    };
    serde_json::to_string(&outcome).map_err(|e| e.to_string())
}

/// Run a Rust reference strategy over JSON wire messages (ADR 008).
///
/// Returns JSON with `intents`, `fills`, `observations`, `positions` and
/// `cash`. Raises `ValueError` for an unknown strategy, invalid JSON or a
/// failed run.
#[pyfunction]
#[pyo3(signature = (strategy, params, events, instruments="[]", initial_cash=0.0))]
pub fn run_strategy(
    strategy: &str,
    params: &str,
    events: &str,
    instruments: &str,
    initial_cash: f64,
) -> PyResult<String> {
    run_strategy_json(strategy, params, events, instruments, initial_cash)
        .map_err(PyValueError::new_err)
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(run_strategy, m)?)?;
    Ok(())
}
