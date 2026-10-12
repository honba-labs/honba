//! Unit tests for `crate::backoff`.

use crate::ExponentialBackoff;

#[test]
fn backoff_schedule() {
    let backoff = ExponentialBackoff::new(100, 1_000, 0.0, 7);

    let schedule: Vec<u64> = (1..=7)
        .map(|attempt| backoff.next(attempt).as_millis() as u64)
        .collect();

    assert_eq!(schedule, vec![100, 200, 400, 800, 1_000, 1_000, 1_000]);
}

#[test]
fn backoff_jitter_is_deterministic_for_a_seed() {
    let backoff = ExponentialBackoff::new(100, 1_000, 0.25, 7);

    let schedule: Vec<u64> = (1..=7)
        .map(|attempt| backoff.next(attempt).as_millis() as u64)
        .collect();

    assert_eq!(schedule, vec![99, 185, 391, 685, 797, 784, 969]);
    assert_eq!(backoff.next(3), backoff.next(3), "same seed, same delay");

    let other = ExponentialBackoff::new(100, 1_000, 0.25, 8);
    assert_ne!(backoff.next(3), other.next(3), "a new seed re-rolls jitter");
}

#[test]
fn backoff_is_bounded_by_cap_and_base() {
    let backoff = ExponentialBackoff::new(250, 1_000, 0.5, 1);

    for attempt in 0..32 {
        let delay = backoff.next(attempt).as_millis() as u64;
        assert!(delay <= 1_000, "delay {delay} exceeds the cap");
        assert!(delay >= 125, "delay {delay} is below the jitter floor");
    }
}
