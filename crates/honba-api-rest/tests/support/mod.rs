//! Shared fixtures for the run-store integration tests: scratch dirs under cargo's
//! `target/tmp` (never `/tmp`, never the repo's `data/`), a settable clock and a counting
//! entropy source.

#![allow(dead_code)]

use std::path::{Path, PathBuf};
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::Arc;

use honba_api::{
    compile_strategy, ResolvedBacktest, ResolvedRequest, ResolvedSweep, RunKind, RunManifest,
    StrategyCatalog,
};
use honba_api_rest::{RunClock, RunEntropy, RunStore};
use honba_messages::{ErrorDetail, Event, Exchange, InstrumentId, Message, QuoteTick, UnixNanos};
use serde_json::json;

/// 2026-10-08T00:00:00Z.
pub const T0: u64 = 1_791_417_600_000;
pub const DAY: u64 = 86_400_000;

static NEXT: AtomicU64 = AtomicU64::new(0);

/// A scratch directory removed on drop.
pub struct Scratch(PathBuf);

impl Scratch {
    pub fn new(name: &str) -> Self {
        let dir = Path::new(env!("CARGO_TARGET_TMPDIR")).join(format!(
            "runs-{name}-{}-{}",
            std::process::id(),
            NEXT.fetch_add(1, Ordering::Relaxed)
        ));
        std::fs::create_dir_all(&dir).unwrap();
        Self(dir)
    }

    pub fn path(&self) -> &Path {
        &self.0
    }

    /// The journals root inside the scratch dir.
    pub fn journals(&self) -> PathBuf {
        self.0.join("journals")
    }
}

impl Drop for Scratch {
    fn drop(&mut self) {
        let _ = std::fs::remove_dir_all(&self.0);
    }
}

/// A clock the test moves by hand; clones share the time.
#[derive(Clone)]
pub struct FakeClock(Arc<AtomicU64>);

impl FakeClock {
    pub fn at(ms: u64) -> Self {
        Self(Arc::new(AtomicU64::new(ms)))
    }
    pub fn set(&self, ms: u64) {
        self.0.store(ms, Ordering::SeqCst);
    }
    pub fn advance(&self, ms: u64) {
        self.0.fetch_add(ms, Ordering::SeqCst);
    }
}

impl RunClock for FakeClock {
    fn now_unix_ms(&self) -> u64 {
        self.0.load(Ordering::SeqCst)
    }
}

/// Entropy that counts up, so ids are reproducible.
#[derive(Default)]
pub struct CountingEntropy(AtomicU64);

impl RunEntropy for CountingEntropy {
    fn next_bits(&self) -> Result<[u8; 10], ErrorDetail> {
        let n = self.0.fetch_add(1 << 20, Ordering::SeqCst);
        let mut bits = [0u8; 10];
        bits[2..].copy_from_slice(&n.to_be_bytes());
        Ok(bits)
    }
}

pub fn store(root: PathBuf, clock: &FakeClock) -> RunStore {
    RunStore::new(
        root,
        Box::new(clock.clone()),
        Box::new(CountingEntropy::default()),
    )
}

fn compiled() -> honba_api::CompiledStrategy {
    let tcs = json!({"symbol": "TCS", "exchange": "NSE"});
    let manifest = json!({
        "api_version": "1.0.0",
        "name": "sma",
        "source_hash": "abc",
        "universe": {"explicit": [tcs]},
        "subscriptions": {"instruments": [tcs]},
        "driving_timeframe": {"interval": 1, "aggregation": "day"},
        "warmup_bars": 20
    });
    compile_strategy(
        &mut StrategyCatalog::default(),
        serde_json::from_value(manifest).unwrap(),
    )
    .unwrap()
}

pub fn backtest_request(seed: u64) -> ResolvedRequest {
    ResolvedRequest::Backtest(ResolvedBacktest {
        strategy: "sma".into(),
        universe: "nifty50".into(),
        start: UnixNanos::from_u64(1),
        end: UnixNanos::from_u64(2),
        bar_spec: "1d".into(),
        initial_capital: 1_000_000.0,
        seed,
    })
}

pub fn sweep_request(seed: u64) -> ResolvedRequest {
    ResolvedRequest::Sweep(ResolvedSweep {
        strategy: "sma".into(),
        params: json!({"fast": [5, 10]}),
        trials: 2,
        seed,
    })
}

pub fn submit(store: &RunStore, request: ResolvedRequest) -> RunManifest {
    let c = compiled();
    store.submit(c.id, c.ir, request).unwrap()
}

pub fn submit_backtest(store: &RunStore, seed: u64) -> RunManifest {
    submit(store, backtest_request(seed))
}

pub fn quote(n: u64) -> Message {
    let q = QuoteTick::new(
        InstrumentId::new("TCS", Exchange::new("NSE")),
        100.0 + n as f64,
        101.0 + n as f64,
        10.0,
        20.0,
        UnixNanos::from_u64(n),
        UnixNanos::from_u64(n),
    );
    Message::new(Event::Quote(q), UnixNanos::from_u64(n + 1))
}

pub fn run_dir(root: &Path, m: &RunManifest) -> PathBuf {
    root.join(m.run_id.as_str())
}

pub fn kind_of(m: &RunManifest) -> RunKind {
    m.kind
}
