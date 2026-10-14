//! ADR 0017 decision 6: same seed + same pinned inputs + same bytes under the data root give
//! a byte-identical `events.ndjson`, at 1, 4 and 16 workers (the `determinism_under_threads`
//! shape of `honba-sweep`).

mod support;

use std::sync::Arc;

use honba_api::RunStatus;
use support::*;

/// Runs `n` identical submits on a service with `workers` workers; returns each run's
/// `events.ndjson` bytes.
fn journals_at(workers: usize, n: usize, seed: u64, window_end: &str) -> Vec<Vec<u8>> {
    let scratch = Scratch::new(&format!("determinism-{workers}"));
    let clock = FakeClock::at(T0);
    let service = start_service(
        Arc::new(store(scratch.journals(), &clock)),
        real_executor(),
        config(workers, 64, 1_000),
    );
    let ids: Vec<String> = (0..n)
        .map(|_| {
            let mut request = sma_request(seed);
            request.end = Some(window_end.into());
            service.submit_backtest(&request).unwrap().run_id
        })
        .collect();
    let bytes = ids
        .iter()
        .map(|id| {
            let done = wait_terminal(service.store(), id);
            assert_eq!(done.status, RunStatus::Completed, "{:?}", done.error);
            std::fs::read(scratch.journals().join(id).join("events.ndjson")).unwrap()
        })
        .collect();
    service.shutdown();
    bytes
}

#[test]
fn determinism_under_workers() {
    let mut all: Vec<Vec<u8>> = Vec::new();
    for workers in [1, 4, 16] {
        all.extend(journals_at(workers, 6, 42, "2024-06-01"));
    }
    let first = &all[0];
    assert!(
        first.len() > 1_000,
        "journal suspiciously small: {}",
        first.len()
    );
    assert!(first.ends_with(b"\n"));
    for (i, other) in all.iter().enumerate() {
        assert_eq!(first, other, "run {i} differs from run 0");
    }
}

#[test]
fn a_different_window_changes_the_journal() {
    let a = journals_at(1, 1, 42, "2024-06-01").remove(0);
    let b = journals_at(1, 1, 42, "2024-02-01").remove(0);
    assert_ne!(a, b);
}

/// Runs `n` identical backtests driven from a multi-thread tokio runtime with `worker_threads`
/// workers; returns each run's `events.ndjson` bytes. The run itself stays on the blocking pool
/// (the executor owns no tokio threads), so the runtime only decides how the shell dispatches.
fn journals_at_worker_threads(worker_threads: usize, n: usize) -> Vec<Vec<u8>> {
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .worker_threads(worker_threads)
        .enable_all()
        .build()
        .unwrap();
    runtime.block_on(async move {
        tokio::task::spawn_blocking(move || {
            let scratch = Scratch::new(&format!("threads-{worker_threads}"));
            let clock = FakeClock::at(T0);
            let service = start_service(
                Arc::new(store(scratch.journals(), &clock)),
                real_executor(),
                config(worker_threads, 64, 1_000),
            );
            let ids: Vec<String> = (0..n)
                .map(|_| service.submit_backtest(&sma_request(42)).unwrap().run_id)
                .collect();
            let bytes = ids
                .iter()
                .map(|id| {
                    let done = wait_terminal(service.store(), id);
                    assert_eq!(done.status, RunStatus::Completed, "{:?}", done.error);
                    std::fs::read(scratch.journals().join(id).join("events.ndjson")).unwrap()
                })
                .collect();
            service.shutdown();
            bytes
        })
        .await
        .unwrap()
    })
}

fn determinism_under_worker_threads(worker_threads: usize) {
    let baseline = journals_at_worker_threads(1, 3);
    assert!(
        baseline[0].len() > 1_000,
        "journal suspiciously small: {}",
        baseline[0].len()
    );
    assert!(baseline[0].ends_with(b"\n"));

    let observed = journals_at_worker_threads(worker_threads, 3);
    for (i, bytes) in observed.iter().enumerate() {
        assert_eq!(
            bytes, &baseline[0],
            "run {i} at {worker_threads} worker threads differs from the single-threaded run"
        );
    }
}

#[test]
fn determinism_under_threads_one_worker() {
    determinism_under_worker_threads(1);
}

#[test]
fn determinism_under_threads_four_workers() {
    determinism_under_worker_threads(4);
}

#[test]
fn determinism_under_threads_sixteen_workers() {
    determinism_under_worker_threads(16);
}

#[test]
fn run_ids_and_timestamps_stay_out_of_the_journal() {
    let scratch = Scratch::new("determinism-clean");
    let clock = FakeClock::at(T0);
    let service = start_service(
        Arc::new(store(scratch.journals(), &clock)),
        real_executor(),
        config(2, 8, 500),
    );
    let id = service.submit_backtest(&sma_request(42)).unwrap().run_id;
    wait_terminal(service.store(), &id);
    let text = std::fs::read_to_string(scratch.journals().join(&id).join("events.ndjson")).unwrap();
    assert!(!text.contains(&id));
    assert!(!text.contains("journals"));
    assert!(!text.contains("2026-"));
    service.shutdown();
}
