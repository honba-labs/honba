//! Unit tests for `crate::engine`.

use std::sync::{Arc, Mutex};

use honba_entities::Trade;
use honba_messages::{Event, Message, Order, QuoteTick, UnixNanos};

use super::any_instrument;

use crate::{
    AlgoError, DataFeed, Engine, EngineOutput, ExecutionEngine, Handler, Result, TradingState,
};

fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

fn quote(ts_event: u64, ts_init: u64) -> Message {
    Message::new(
        Event::Quote(QuoteTick::new(
            any_instrument(),
            1.0,
            2.0,
            1.0,
            1.0,
            ts(ts_event),
            ts(ts_init),
        )),
        ts(ts_init),
    )
}

struct EmptyFeed;

impl DataFeed for EmptyFeed {
    fn next(&mut self) -> Result<Option<Message>> {
        Ok(None)
    }
}

#[derive(Clone, Default)]
struct Recorder {
    seen: Arc<Mutex<Vec<(u64, u64)>>>,
}

impl Handler for Recorder {
    fn on_event(&mut self, event: &Event, ts_init: UnixNanos) -> Result<EngineOutput> {
        let mut seen = self.seen.lock().unwrap();
        seen.push((event.ts_event().as_u64(), ts_init.as_u64()));
        Ok(EngineOutput::None)
    }
}

#[derive(Clone, Default)]
struct StubSink {
    submitted: Arc<Mutex<Vec<String>>>,
    cancelled: Arc<Mutex<Vec<String>>>,
}

impl ExecutionEngine for StubSink {
    fn submit(&mut self, order: Order) -> Result<()> {
        self.submitted
            .lock()
            .unwrap()
            .push(order.order_id().as_str().to_string());
        Ok(())
    }

    fn cancel(&mut self, order_id: &str) -> Result<()> {
        self.cancelled.lock().unwrap().push(order_id.to_string());
        Ok(())
    }

    fn drain_fills(&mut self) -> Result<Vec<Trade>> {
        Ok(Vec::new())
    }
}

#[test]
fn inject_enqueues_in_ts_event_order_with_fifo_ties() {
    let mut engine = Engine::new();
    engine.inject(quote(5, 1));
    engine.inject(quote(3, 2));
    engine.inject(quote(5, 4));
    engine.inject(quote(5, 3));

    let recorder = Recorder::default();
    let seen = recorder.seen.clone();
    engine.add_handler(recorder);
    engine.run(&mut EmptyFeed).unwrap();

    assert_eq!(*seen.lock().unwrap(), vec![(3, 2), (5, 1), (5, 4), (5, 3)]);
}

#[test]
fn inject_does_not_advance_the_clock() {
    let mut engine = Engine::new();
    engine.inject(quote(500, 500));
    assert_eq!(engine.now(), ts(0));
    engine.run(&mut EmptyFeed).unwrap();
    assert_eq!(engine.now(), ts(500));
}

#[test]
fn a_fresh_engine_is_active_with_no_execution_and_an_empty_audit() {
    let engine = Engine::new();
    assert_eq!(engine.trading_state(), TradingState::Active);
    assert!(engine.execution().is_none());
    assert!(engine.audit().is_empty());
}

#[test]
fn execution_is_visible_once_attached() {
    let mut engine = Engine::new();
    let sink = StubSink::default();
    let submitted = sink.submitted.clone();
    let cancelled = sink.cancelled.clone();

    engine.set_execution(Box::new(sink));
    assert!(engine.execution().is_some());

    engine.run(&mut EmptyFeed).unwrap();
    assert!(submitted.lock().unwrap().is_empty());
    assert!(cancelled.lock().unwrap().is_empty());
}

#[test]
fn drain_fills_is_empty_before_anything_runs() {
    let mut engine = Engine::new();
    engine.set_execution(Box::new(StubSink::default()));
    engine.run(&mut EmptyFeed).unwrap();
    assert!(engine.drain_fills().unwrap().is_empty());
}

#[test]
fn a_handler_error_aborts_the_run() {
    struct Failing;

    impl Handler for Failing {
        fn on_event(&mut self, _event: &Event, _ts_init: UnixNanos) -> Result<EngineOutput> {
            Err(AlgoError::Component("boom".to_string()))
        }
    }

    let mut engine = Engine::new();
    engine.add_handler(Failing);
    engine.inject(quote(1, 1));
    let err = engine.run(&mut EmptyFeed).unwrap_err();
    assert!(matches!(err, AlgoError::Component(_)));
}
