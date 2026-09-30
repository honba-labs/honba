//! End-to-end engine tests using injected feeds and handlers.

use std::sync::{Arc, Mutex};

use honba_algo::{
    AlgoError, DataFeed, Engine, Handler, Result,
};
use honba_messages::{
    Event, InstrumentId, Message, QuoteTick, UnixNanos, Venue,
};

fn ts(n: u64) -> UnixNanos {
    UnixNanos::from_u64(n)
}

fn quote(t: u64) -> Message {
    Message::new(
        Event::Quote(QuoteTick::new(
            InstrumentId::new("X", Venue::new("NSE")),
            1.0, 2.0, 1.0, 1.0, ts(t), ts(t),
        )),
        ts(t),
    )
}

struct VecFeed {
    items: std::collections::VecDeque<Message>,
}

impl VecFeed {
    fn new(items: Vec<Message>) -> Self {
        Self { items: items.into() }
    }
}

impl DataFeed for VecFeed {
    fn next(&mut self) -> Result<Option<Message>> {
        Ok(self.items.pop_front())
    }
}

#[derive(Clone, Default)]
struct Recorder {
    events: Arc<Mutex<Vec<u64>>>,
    started: Arc<Mutex<bool>>,
    stopped: Arc<Mutex<bool>>,
}

impl Handler for Recorder {
    fn on_start(&mut self) -> Result<()> {
        *self.started.lock().unwrap() = true;
        Ok(())
    }

    fn on_event(&mut self, event: &Event, _ts_init: UnixNanos) -> Result<()> {
        self.events.lock().unwrap().push(event.ts_event().as_u64());
        Ok(())
    }

    fn on_stop(&mut self) -> Result<()> {
        *self.stopped.lock().unwrap() = true;
        Ok(())
    }
}

#[test]
fn engine_processes_events_in_order() {
    let mut feed = VecFeed::new(vec![quote(3), quote(1), quote(2)]);

    let rec = Recorder::default();
    let events = rec.events.clone();
    let started = rec.started.clone();
    let stopped = rec.stopped.clone();

    let mut engine = Engine::new();
    engine.add_handler(rec);
    engine.run(&mut feed).unwrap();

    assert_eq!(*events.lock().unwrap(), vec![1, 2, 3]);
    assert!(*started.lock().unwrap());
    assert!(*stopped.lock().unwrap());
    assert_eq!(engine.now(), ts(3));
}

#[test]
fn engine_with_empty_feed_finishes() {
    let mut feed = VecFeed::new(vec![]);
    let rec = Recorder::default();
    let events = rec.events.clone();

    let mut engine = Engine::new();
    engine.add_handler(rec);
    engine.run(&mut feed).unwrap();

    assert!(events.lock().unwrap().is_empty());
    assert_eq!(engine.now(), ts(0));
}

#[test]
fn engine_detects_clock_regression() {
    // Feed that returns ts=10 then ts=5 — the queue orders by ts, so 5 pops
    // first. That's fine. To force regression we need a handler that
    // observes ts=10 then a later event with ts=5, which the queue prevents.
    //
    // Instead, test the clock directly by driving an out-of-order queue via
    // a handler that advances a second clock. Here we settle for verifying
    // the engine enforces monotonic time on the events it dispatches.
    let mut feed = VecFeed::new(vec![quote(5), quote(10)]);

    #[derive(Clone, Default)]
    struct MaxTs(Arc<Mutex<u64>>);

    impl Handler for MaxTs {
        fn on_event(&mut self, event: &Event, _init: UnixNanos) -> Result<()> {
            let t = event.ts_event().as_u64();
            let mut m = self.0.lock().unwrap();
            assert!(t >= *m, "event ts went backwards: {t} < {m}");
            *m = t;
            Ok(())
        }
    }

    let mut engine = Engine::new();
    engine.add_handler(MaxTs::default());
    engine.run(&mut feed).unwrap();

    // Sanity: the AlgoError variant exists and formats.
    let e = AlgoError::ClockRegression { current: 10, requested: 5 };
    assert!(format!("{e}").contains("clock cannot go backwards"));
}

#[test]
fn multiple_handlers_all_receive_events() {
    let mut feed = VecFeed::new(vec![quote(1), quote(2)]);

    let a = Recorder::default();
    let b = Recorder::default();
    let ea = a.events.clone();
    let eb = b.events.clone();

    let mut engine = Engine::new();
    engine.add_handler(a);
    engine.add_handler(b);
    engine.run(&mut feed).unwrap();

    assert_eq!(*ea.lock().unwrap(), vec![1, 2]);
    assert_eq!(*eb.lock().unwrap(), vec![1, 2]);
}

#[test]
fn batch_size_one_requires_ordered_feed() {
    // With batch_size = 1 the engine has no look-ahead, so an unordered
    // feed trips the monotonic clock.
    let mut feed = VecFeed::new(vec![quote(3), quote(1)]);

    let mut engine = Engine::new().with_batch_size(1);
    engine.add_handler(Recorder::default());

    let err = engine.run(&mut feed).unwrap_err();
    assert!(matches!(err, AlgoError::ClockRegression { .. }));
}

