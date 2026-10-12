//! Bounded exponential backoff with deterministic jitter.

use std::time::Duration;

/// A bounded exponential backoff schedule.
///
/// The delay before retry `attempt` (1-based) is `base_ms * 2^(attempt - 1)`,
/// capped at `cap_ms`, then reduced by up to `jitter` of that delay. The jitter
/// is a splitmix64 hash of `(seed, attempt)`, so a fixed seed yields the same
/// schedule on every run: a reconnect is testable and never touches a clock.
///
/// ```
/// use honba_ports::ExponentialBackoff;
///
/// let backoff = ExponentialBackoff::new(100, 5_000, 0.0, 7);
/// assert_eq!(backoff.next(1).as_millis(), 100);
/// assert_eq!(backoff.next(2).as_millis(), 200);
/// assert_eq!(backoff.next(3).as_millis(), 400);
/// ```
#[derive(Clone, Copy, Debug, PartialEq)]
pub struct ExponentialBackoff {
    base_ms: u64,
    cap_ms: u64,
    jitter: f64,
    seed: u64,
}

impl ExponentialBackoff {
    /// Creates a schedule with `base_ms` the first delay, `cap_ms` the ceiling,
    /// `jitter` the fraction of each delay randomized (in `0.0..=1.0`), and a
    /// `seed` that fixes the jitter sequence.
    pub const fn new(base_ms: u64, cap_ms: u64, jitter: f64, seed: u64) -> Self {
        Self {
            base_ms,
            cap_ms,
            jitter,
            seed,
        }
    }

    /// The first delay, in milliseconds.
    pub const fn base_ms(&self) -> u64 {
        self.base_ms
    }

    /// The maximum delay, in milliseconds.
    pub const fn cap_ms(&self) -> u64 {
        self.cap_ms
    }

    /// Returns the delay to wait before retry number `attempt` (1-based).
    ///
    /// `attempt` of `0` is treated as `1`.
    pub fn next(&self, attempt: u32) -> Duration {
        let shift = attempt.saturating_sub(1).min(63);
        let scaled = self.base_ms.saturating_mul(1u64 << shift);
        let raw = scaled.min(self.cap_ms);
        let jittered = raw as f64 * (1.0 - self.jitter + self.jitter * rand01(self.seed, attempt));
        Duration::from_millis((jittered + 0.5) as u64)
    }
}

/// A splitmix64 value in `[0.0, 1.0)` derived from `seed` and `attempt`.
fn rand01(seed: u64, attempt: u32) -> f64 {
    let mut z = seed ^ u64::from(attempt).wrapping_mul(0x9E37_79B9_7F4A_7C15);
    z = (z ^ (z >> 30)).wrapping_mul(0xBF58_476D_1CE4_E5B9);
    z = (z ^ (z >> 27)).wrapping_mul(0x94D0_49BB_1331_11EB);
    z ^= z >> 31;
    (z >> 11) as f64 / (1u64 << 53) as f64
}
