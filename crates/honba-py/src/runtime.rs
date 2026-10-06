//! The one explicit async runtime of an interpreter (ADR 0015, E10-S7).
//!
//! Python's `honba.event_loop` owns the lifecycle: it calls [`start`] before the first async
//! use and [`stop`] at exit. The runtime lives in a process-global slot instead of being leaked,
//! so it can be shut down deterministically (worker threads joined) and started again.
//!
//! Rules:
//! - At most one runtime exists at a time; a second [`start`] is refused, not replaced.
//! - [`block_on`] reuses the started runtime. With none started it builds a current-thread
//!   runtime for that call only, so no second runtime ever outlives a call.
//! - [`stop`] waits for in-flight [`block_on`] calls, then drops the runtime (joining its
//!   threads).
//!
//! `pyo3-async-runtimes` is deliberately not given this runtime: its `init_with_runtime`
//! accepts a `&'static` runtime once per process, which would again need a leak and forbid
//! restart. Rust code that needs the runtime goes through this module.

use std::fmt;
use std::future::Future;
use std::sync::atomic::{AtomicU64, Ordering};
use std::sync::RwLock;

/// What a started runtime looks like from outside.
#[derive(Clone, Debug, PartialEq, Eq)]
pub struct RuntimeInfo {
    /// Always `"multi-thread"` for the started runtime.
    pub flavor: &'static str,
    /// Worker threads the runtime was built with.
    pub worker_threads: usize,
    /// 1 for the first start in this process, +1 for every later start.
    pub generation: u64,
}

/// Why [`start`] refused.
#[derive(Debug, PartialEq, Eq)]
pub enum RuntimeError {
    /// A runtime is already running; carries its description.
    AlreadyRunning(RuntimeInfo),
    /// `worker_threads` was 0.
    InvalidWorkerThreads,
    /// The operating system refused to build the runtime.
    Build(String),
}

impl fmt::Display for RuntimeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::AlreadyRunning(i) => write!(
                f,
                "an async runtime is already running (generation {}, {} worker threads)",
                i.generation, i.worker_threads
            ),
            Self::InvalidWorkerThreads => f.write_str("worker_threads must be at least 1"),
            Self::Build(m) => write!(f, "failed to build the async runtime: {m}"),
        }
    }
}

impl std::error::Error for RuntimeError {}

struct Slot {
    runtime: tokio::runtime::Runtime,
    info: RuntimeInfo,
}

static SLOT: RwLock<Option<Slot>> = RwLock::new(None);
static GENERATION: AtomicU64 = AtomicU64::new(0);

/// Starts the runtime. `worker_threads` of `None` uses one per available core.
pub fn start(worker_threads: Option<usize>) -> Result<RuntimeInfo, RuntimeError> {
    if worker_threads == Some(0) {
        return Err(RuntimeError::InvalidWorkerThreads);
    }
    let mut slot = SLOT.write().unwrap_or_else(|p| p.into_inner());
    if let Some(running) = slot.as_ref() {
        return Err(RuntimeError::AlreadyRunning(running.info.clone()));
    }
    let workers = worker_threads
        .or_else(|| std::thread::available_parallelism().ok().map(|n| n.get()))
        .unwrap_or(1);
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(workers)
        .thread_name("honba-runtime")
        .enable_all()
        .build()
        .map_err(|e| RuntimeError::Build(e.to_string()))?;
    let info = RuntimeInfo {
        flavor: "multi-thread",
        worker_threads: workers,
        generation: GENERATION.fetch_add(1, Ordering::SeqCst) + 1,
    };
    *slot = Some(Slot {
        runtime,
        info: info.clone(),
    });
    Ok(info)
}

/// Stops the runtime and joins its threads. Returns whether one was running.
pub fn stop() -> bool {
    let taken = SLOT.write().unwrap_or_else(|p| p.into_inner()).take();
    taken.is_some()
}

/// Whether a runtime is started.
pub fn is_running() -> bool {
    info().is_some()
}

/// The started runtime's description, if any.
pub fn info() -> Option<RuntimeInfo> {
    SLOT.read()
        .unwrap_or_else(|p| p.into_inner())
        .as_ref()
        .map(|s| s.info.clone())
}

/// Drives `fut` to completion on the started runtime, or on a current-thread runtime built for
/// this call only when none is started. Must not be called from inside a runtime.
pub fn block_on<F: Future>(fut: F) -> F::Output {
    let slot = SLOT.read().unwrap_or_else(|p| p.into_inner());
    match slot.as_ref() {
        Some(s) => s.runtime.block_on(fut),
        None => {
            drop(slot);
            tokio::runtime::Builder::new_current_thread()
                .enable_all()
                .build()
                .expect("a current-thread runtime always builds")
                .block_on(fut)
        }
    }
}
