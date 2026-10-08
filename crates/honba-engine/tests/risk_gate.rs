//! The risk stage through the real flow (ADR 0018 decisions 6-8): handler ->
//! `Engine::submit` -> stage -> `ScriptedExecution`, with the audit and the
//! lifecycle events the handler sees.

use std::collections::BTreeMap;
use std::sync::{Arc, Mutex};

use honba_engine::{AlgoError, AuditKind, Engine, EngineOutput, Handler, Result};
use honba_entities::Currency;
use honba_market::InstrumentRules;
use honba_messages::{
    Event, Exchange, InstrumentId, Message, Order, OrderId, OrderSide, OrderType, QuoteTick,
    TimeInForce, TradingState, UnixNanos,
};
use honba_risk::{
    OrderRateLimit, RiskConfigError, RiskLimits, RiskRefusal, RiskStage, RulesSource,
};
use honba_sim::{Behavior, ScriptedExecution};

const MS: u64 = 1_000_000;

fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

fn x() -> InstrumentId {
    InstrumentId::new("X", Exchange::new("NSE"))
}

fn quote(t: u64) -> Message {
    Message::new(
        Event::Quote(QuoteTick::new(x(), 1.0, 2.0, 1.0, 1.0, ts(t), ts(t))),
        ts(t),
    )
}

fn limit(id: &str, side: OrderSide, qty: f64, t: u64) -> Order {
    Order::new(
        OrderId::new(id),
        x(),
        side,
        OrderType::Limit,
        qty,
        Some(10.0),
        TimeInForce::Day,
        ts(t),
        ts(t),
    )
}

struct Rules(BTreeMap<InstrumentId, InstrumentRules>);

impl RulesSource for Rules {
    fn rules(
        &self,
        id: &InstrumentId,
    ) -> Option<(InstrumentRules, Option<honba_market::PriceBand>)> {
        self.0.get(id).map(|r| (r.clone(), None))
    }
}

fn stage(limits: RiskLimits, known: bool) -> RiskStage {
    let mut m = BTreeMap::new();
    if known {
        m.insert(x(), InstrumentRules::new(1.0, 0.05));
    }
    RiskStage::new(limits, Currency::Inr, Arc::new(Rules(m))).expect("valid limits")
}

type Steps = Vec<(u64, Vec<Order>)>;

/// Emits the scripted orders at each quote's ts and logs the lifecycle it sees.
#[derive(Clone)]
struct Strategy {
    steps: Arc<Mutex<Steps>>,
    seen: Arc<Mutex<Vec<String>>>,
    holds_stage: bool,
}

impl Strategy {
    fn new(steps: Steps) -> Self {
        Self {
            steps: Arc::new(Mutex::new(steps)),
            seen: Arc::new(Mutex::new(Vec::new())),
            holds_stage: false,
        }
    }

    fn holding_stage() -> Self {
        Self {
            holds_stage: true,
            ..Self::new(Vec::new())
        }
    }

    fn seen(&self) -> Vec<String> {
        self.seen.lock().unwrap().clone()
    }
}

impl Handler for Strategy {
    fn on_event(&mut self, event: &Event, _ts_init: UnixNanos) -> Result<EngineOutput> {
        match event {
            Event::Quote(_) => {
                let t = event.ts_event().as_u64();
                let mut steps = self.steps.lock().unwrap();
                Ok(match steps.iter().position(|(at, _)| *at == t) {
                    Some(i) => EngineOutput::Orders(steps.remove(i).1),
                    None => EngineOutput::None,
                })
            }
            Event::OrderRejected {
                order_id, reason, ..
            } => {
                self.seen
                    .lock()
                    .unwrap()
                    .push(format!("rejected {} {reason}", order_id.as_str()));
                Ok(EngineOutput::None)
            }
            _ => Ok(EngineOutput::None),
        }
    }

    fn holds_risk_stage(&self) -> bool {
        self.holds_stage
    }
}

fn run(engine: &mut Engine, quotes: &[u64]) {
    engine.start().unwrap();
    for t in quotes {
        engine.inject(quote(*t));
    }
    engine.finish().unwrap();
}

fn kinds(engine: &Engine) -> Vec<AuditKind> {
    engine
        .audit()
        .iter()
        .map(|r| r.kind.clone())
        .filter(|k| !matches!(k, AuditKind::EventDispatched { .. }))
        .collect()
}

fn rejected(id: &str, reason: &str) -> AuditKind {
    AuditKind::OrderRejected {
        order_id: id.to_string(),
        reason: reason.to_string(),
    }
}

#[test]
fn with_risk_refusal_audits_risk_refused_then_order_rejected_and_reaches_no_venue() {
    let venue = ScriptedExecution::new(10.0).with("O-1", Behavior::Hold);
    let strategy = Strategy::new(vec![(1, vec![limit("O-1", OrderSide::Buy, 5.0, 1)])]);
    let mut engine = Engine::new().with_risk(stage(RiskLimits::default(), false));
    engine.set_execution(Box::new(venue.clone()));
    engine.add_handler(strategy.clone());
    run(&mut engine, &[1]);

    assert_eq!(
        kinds(&engine),
        vec![
            AuditKind::RiskRefused {
                order_id: "O-1".to_string(),
                refusal: RiskRefusal::InstrumentUnknown { instrument_id: x() },
            },
            rejected("O-1", "risk_instrument_unknown"),
        ]
    );
    assert!(venue.working_orders().is_empty(), "the venue saw a submit");
    assert_eq!(
        strategy.seen(),
        vec!["rejected O-1 risk_instrument_unknown"]
    );
}

#[test]
fn reduce_only_two_orders_in_flight() {
    // Long 100, working sell 60, new sell 50: p = 40, 40 - 50 crosses zero.
    let venue = ScriptedExecution::new(10.0)
        .with("S-1", Behavior::Hold)
        .with("S-2", Behavior::Hold);
    let strategy = Strategy::new(vec![(
        1,
        vec![
            limit("S-1", OrderSide::Sell, 60.0, 1),
            limit("S-2", OrderSide::Sell, 50.0, 1),
        ],
    )]);
    let mut engine = Engine::new()
        .with_risk(stage(RiskLimits::default(), true))
        .with_positions([(x(), 100.0)]);
    engine.set_trading_state(TradingState::Reducing);
    engine.set_execution(Box::new(venue.clone()));
    engine.add_handler(strategy.clone());
    run(&mut engine, &[1]);

    assert_eq!(venue.working_orders(), vec!["S-1".to_string()]);
    let refusals: Vec<_> = kinds(&engine)
        .into_iter()
        .filter(|k| matches!(k, AuditKind::RiskRefused { .. }))
        .collect();
    assert_eq!(
        refusals,
        vec![AuditKind::RiskRefused {
            order_id: "S-2".to_string(),
            refusal: RiskRefusal::ReduceOnly {
                position: 40.0,
                side: OrderSide::Sell,
                quantity: 50.0,
            },
        }]
    );
    assert_eq!(
        strategy.seen(),
        vec!["rejected S-2 risk_reduce_only_violation"]
    );
}

#[test]
fn order_rate_refused_in_event_time() {
    let limits = RiskLimits {
        max_notional: None,
        order_rate: Some(OrderRateLimit {
            max_orders: 2,
            window_ms: 1000,
        }),
        ..RiskLimits::default()
    };
    let venue = ScriptedExecution::new(10.0);
    let steps = [(1, "A"), (2, "B"), (3, "C"), (1002, "D")]
        .into_iter()
        .map(|(ms, id)| (ms * MS, vec![limit(id, OrderSide::Buy, 1.0, ms * MS)]))
        .collect();
    let strategy = Strategy::new(steps);
    let mut engine = Engine::new().with_risk(stage(limits, true));
    engine.set_execution(Box::new(venue));
    engine.add_handler(strategy.clone());
    run(&mut engine, &[MS, 2 * MS, 3 * MS, 1002 * MS]);

    let refused: Vec<_> = kinds(&engine)
        .into_iter()
        .filter(|k| {
            matches!(
                k,
                AuditKind::RiskRefused { .. } | AuditKind::OrderRejected { .. }
            )
        })
        .collect();
    // C is the third within one second; by D (1002 ms) A and B have left the window.
    assert_eq!(
        refused,
        vec![
            AuditKind::RiskRefused {
                order_id: "C".to_string(),
                refusal: RiskRefusal::OrderRate {
                    count: 2,
                    max_orders: 2,
                    window_ms: 1000,
                },
            },
            rejected("C", "risk_order_rate_exceeded"),
        ]
    );
}

#[test]
fn double_stage_rejected_at_build() {
    // Engine stage + a handler holding one.
    let mut engine = Engine::new().with_risk(stage(RiskLimits::default(), true));
    engine.add_handler(Strategy::holding_stage());
    assert_eq!(engine.start(), Err(AlgoError::DuplicateRiskStage));
    assert_eq!(
        engine.run(&mut Empty),
        Err(AlgoError::DuplicateRiskStage),
        "run starts through the same guard"
    );

    // Two handlers each holding one.
    let mut engine = Engine::new();
    engine.add_handler(Strategy::holding_stage());
    engine.add_handler(Strategy::holding_stage());
    assert_eq!(engine.start(), Err(AlgoError::DuplicateRiskStage));

    // One stage anywhere is fine.
    let mut engine = Engine::new().with_risk(stage(RiskLimits::default(), true));
    engine.add_handler(Strategy::new(Vec::new()));
    assert_eq!(engine.start(), Ok(()));
    let mut engine = Engine::new();
    engine.add_handler(Strategy::holding_stage());
    assert_eq!(engine.start(), Ok(()));
}

struct Empty;

impl honba_engine::DataFeed for Empty {
    fn next(&mut self) -> Result<Option<Message>> {
        Ok(None)
    }
}

#[test]
fn live_run_without_limits_refused() {
    let full = RiskLimits {
        max_notional: Some(500_000.0),
        order_rate: Some(OrderRateLimit {
            max_orders: 30,
            window_ms: 1000,
        }),
        ..RiskLimits::default()
    };
    let only_notional = RiskLimits {
        max_notional: Some(500_000.0),
        order_rate: None,
        ..RiskLimits::default()
    };
    let refused = Err(AlgoError::RiskConfig(RiskConfigError::LiveRunWithoutLimit));

    // No stage at all.
    assert_eq!(Engine::new().require_live_limits(), refused);
    // A stage with default or partial limits.
    let engine = Engine::new().with_risk(stage(RiskLimits::default(), true));
    assert_eq!(engine.require_live_limits(), refused);
    let engine = Engine::new().with_risk(stage(only_notional, true));
    assert_eq!(engine.require_live_limits(), refused);
    // Both set.
    let engine = Engine::new().with_risk(stage(full, true));
    assert_eq!(engine.require_live_limits(), Ok(()));
}
