//! Pure, clock-injected token bucket. The caller supplies the time and decides how to wait.

/// Token bucket holding up to `capacity` tokens, refilled at `per_second` tokens per second.
#[derive(Debug, Clone)]
pub struct RateLimiter {
    /// Capacity in milli-tokens.
    capacity: u64,
    /// Refill in milli-tokens per millisecond (equals tokens per second).
    rate: u64,
    tokens: u64,
    last_ms: Option<u64>,
}

const TOKEN: u64 = 1_000;

impl RateLimiter {
    /// Creates a full bucket.
    ///
    /// # Panics
    /// If `capacity` or `per_second` is zero.
    pub fn new(capacity: u32, per_second: u32) -> Self {
        assert!(
            capacity > 0 && per_second > 0,
            "capacity and rate must be positive"
        );
        let capacity = u64::from(capacity) * TOKEN;
        Self {
            capacity,
            rate: u64::from(per_second),
            tokens: capacity,
            last_ms: None,
        }
    }

    /// Takes one token at `now_millis`, or returns the milliseconds to wait before one is
    /// available. A clock that moves backwards adds no tokens.
    pub fn try_acquire(&mut self, now_millis: u64) -> Result<(), u64> {
        if let Some(last) = self.last_ms {
            let elapsed = now_millis.saturating_sub(last);
            self.tokens = self
                .tokens
                .saturating_add(elapsed.saturating_mul(self.rate))
                .min(self.capacity);
            self.last_ms = Some(last.max(now_millis));
        } else {
            self.last_ms = Some(now_millis);
        }
        if self.tokens >= TOKEN {
            self.tokens -= TOKEN;
            Ok(())
        } else {
            Err((TOKEN - self.tokens).div_ceil(self.rate))
        }
    }
}
