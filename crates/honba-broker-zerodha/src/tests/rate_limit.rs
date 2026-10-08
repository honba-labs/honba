use crate::rate_limit::RateLimiter;

#[test]
fn burst_up_to_capacity_then_blocked() {
    let mut rl = RateLimiter::new(3, 10);
    assert_eq!(rl.try_acquire(1_000), Ok(()));
    assert_eq!(rl.try_acquire(1_000), Ok(()));
    assert_eq!(rl.try_acquire(1_000), Ok(()));
    // 10/s => one token every 100 ms.
    assert_eq!(rl.try_acquire(1_000), Err(100));
}

#[test]
fn wait_shrinks_as_time_passes_and_token_refills() {
    let mut rl = RateLimiter::new(1, 10);
    assert_eq!(rl.try_acquire(0), Ok(()));
    assert_eq!(rl.try_acquire(40), Err(60));
    assert_eq!(rl.try_acquire(99), Err(1));
    assert_eq!(rl.try_acquire(100), Ok(()));
}

#[test]
fn refill_never_exceeds_capacity() {
    let mut rl = RateLimiter::new(2, 10);
    assert_eq!(rl.try_acquire(0), Ok(()));
    assert_eq!(rl.try_acquire(0), Ok(()));
    assert_eq!(rl.try_acquire(1_000_000), Ok(()));
    assert_eq!(rl.try_acquire(1_000_000), Ok(()));
    assert_eq!(rl.try_acquire(1_000_000), Err(100));
}

#[test]
fn clock_going_backwards_does_not_refill_or_panic() {
    let mut rl = RateLimiter::new(1, 1);
    assert_eq!(rl.try_acquire(5_000), Ok(()));
    assert_eq!(rl.try_acquire(1_000), Err(1_000));
}

#[test]
fn slow_rate_waits_whole_seconds() {
    let mut rl = RateLimiter::new(1, 2);
    assert_eq!(rl.try_acquire(0), Ok(()));
    assert_eq!(rl.try_acquire(0), Err(500));
}
