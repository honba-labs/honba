//! Monotonic event clock.

use honba_messages::UnixNanos;

use crate::error::{AlgoError, Result};

/// Tracks the timestamp of the most recently processed event.
///
/// The clock never moves backwards. Any call to [`Clock::advance_to`] with an
/// earlier timestamp returns [`AlgoError::ClockRegression`], which surfaces
/// data-feed ordering bugs immediately.
#[derive(Clone, Copy, Debug, Default, PartialEq, Eq)]
pub struct Clock {
    now: UnixNanos,
}

impl Clock {
    /// Creates a clock at the given time.
    pub fn new(now: UnixNanos) -> Self {
        Self { now }
    }

    /// Returns the current time.
    pub fn now(&self) -> UnixNanos {
        self.now
    }

    /// Advances to the given time.
    ///
    /// Returns an error if `to` is earlier than the current time.
    pub fn advance_to(&mut self, to: UnixNanos) -> Result<()> {
        if to < self.now {
            return Err(AlgoError::ClockRegression {
                current: self.now.as_u64(),
                requested: to.as_u64(),
            });
        }
        self.now = to;
        Ok(())
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    fn ts(n: u64) -> UnixNanos {
        UnixNanos::from_u64(n)
    }

    #[test]
    fn advance_forward_ok() {
        let mut c = Clock::default();
        c.advance_to(ts(100)).unwrap();
        assert_eq!(c.now(), ts(100));
    }

    #[test]
    fn advance_backward_errors() {
        let mut c = Clock::new(ts(100));
        match c.advance_to(ts(50)) {
            Err(AlgoError::ClockRegression { current, requested }) => {
                assert_eq!(current, 100);
                assert_eq!(requested, 50);
            }
            other => panic!("expected ClockRegression, got {other:?}"),
        }
    }

    #[test]
    fn advance_to_same_time_ok() {
        let mut c = Clock::new(ts(100));
        c.advance_to(ts(100)).unwrap();
    }
}
