//! `crate::runtime`: the single explicit runtime slot (no interpreter needed).

use std::sync::{Mutex, MutexGuard};

use crate::runtime::{block_on, info, is_running, start, stop, RuntimeError};

/// The slot is process-global, so tests that start or stop it run one at a time.
static SERIAL: Mutex<()> = Mutex::new(());

fn serial() -> MutexGuard<'static, ()> {
    let guard = SERIAL.lock().unwrap_or_else(|p| p.into_inner());
    stop();
    guard
}

#[test]
fn start_reports_a_multi_thread_runtime() {
    let _g = serial();
    assert!(!is_running());
    let started = start(Some(2)).unwrap();
    assert_eq!(started.flavor, "multi-thread");
    assert_eq!(started.worker_threads, 2);
    assert!(is_running());
    assert_eq!(info(), Some(started));
    stop();
}

#[test]
fn a_second_start_is_refused_and_reports_the_running_runtime() {
    let _g = serial();
    let first = start(None).unwrap();
    match start(None) {
        Err(RuntimeError::AlreadyRunning(running)) => assert_eq!(running, first),
        other => panic!("expected AlreadyRunning, got {other:?}"),
    }
    assert_eq!(info(), Some(first));
    stop();
}

#[test]
fn stop_is_idempotent_and_says_whether_it_stopped_one() {
    let _g = serial();
    assert!(!stop());
    start(None).unwrap();
    assert!(stop());
    assert!(!stop());
    assert!(!is_running());
    assert_eq!(info(), None);
}

#[test]
fn restart_after_stop_gets_a_new_generation() {
    let _g = serial();
    let first = start(None).unwrap();
    stop();
    let second = start(None).unwrap();
    assert_eq!(second.generation, first.generation + 1);
    stop();
}

#[test]
fn zero_worker_threads_is_invalid() {
    let _g = serial();
    assert!(matches!(
        start(Some(0)),
        Err(RuntimeError::InvalidWorkerThreads)
    ));
    assert!(!is_running());
}

#[test]
fn block_on_runs_with_and_without_the_runtime() {
    let _g = serial();
    assert_eq!(block_on(async { 20 + 22 }), 42);
    start(Some(1)).unwrap();
    assert_eq!(block_on(async { 20 + 22 }), 42);
    stop();
}

#[test]
fn started_runtime_drives_timers() {
    let _g = serial();
    start(Some(1)).unwrap();
    let done = block_on(async {
        tokio::time::sleep(std::time::Duration::from_millis(5)).await;
        true
    });
    assert!(done);
    stop();
}
