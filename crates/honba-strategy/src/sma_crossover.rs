//! SMA crossover strategy.

use honba_engine::Result;
use honba_indicators::{Indicator, Sma};
use honba_messages::{Bar, InstrumentId, UnixNanos};

use crate::intent::OrderIntent;
use crate::strategy::Strategy;

/// Emits a buy when the fast SMA crosses above the slow SMA, and a sell
/// when it crosses back below.
///
/// The strategy tracks only the *cross*, not the position. It emits one buy
/// per upward cross and one sell per downward cross, leaving position
/// accounting to the execution layer.
///
/// ```
/// use honba_strategy::{SmaCrossover, Strategy};
/// use honba_messages::{InstrumentId, Venue};
///
/// let strategy = SmaCrossover::new(
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     5, 20, 75.0,
/// );
/// assert_eq!(strategy.name(), "sma_crossover");
/// ```
pub struct SmaCrossover {
    instrument_id: InstrumentId,
    fast: Sma,
    slow: Sma,
    quantity: f64,
    prev_above: Option<bool>,
    intents: Vec<OrderIntent>,
}

impl SmaCrossover {
    /// Creates an SMA crossover strategy.
    ///
    /// # Panics
    ///
    /// Panics if `fast >= slow` or either is zero.
    pub fn new(instrument_id: InstrumentId, fast: usize, slow: usize, quantity: f64) -> Self {
        assert!(fast > 0 && slow > 0, "SMA periods must be positive");
        assert!(fast < slow, "fast period must be less than slow period");
        Self {
            instrument_id,
            fast: Sma::new(fast),
            slow: Sma::new(slow),
            quantity,
            prev_above: None,
            intents: Vec::new(),
        }
    }

    /// Returns the fast period.
    pub fn fast_period(&self) -> usize {
        self.fast.period()
    }

    /// Returns the slow period.
    pub fn slow_period(&self) -> usize {
        self.slow.period()
    }
}

impl Strategy for SmaCrossover {
    fn name(&self) -> &str {
        "sma_crossover"
    }

    fn on_bar(&mut self, bar: &Bar, _ts_init: UnixNanos) -> Result<()> {
        let f = self.fast.update(bar.close());
        let s = self.slow.update(bar.close());

        if let (Some(f), Some(s)) = (f, s) {
            let above = f > s;
            match self.prev_above {
                Some(prev) if prev != above => {
                    let intent = if above {
                        OrderIntent::market_buy(self.instrument_id.clone(), self.quantity)
                    } else {
                        OrderIntent::market_sell(self.instrument_id.clone(), self.quantity)
                    };
                    self.intents.push(intent);
                }
                _ => {}
            }
            self.prev_above = Some(above);
        }
        Ok(())
    }

    fn drain_intents(&mut self) -> Vec<OrderIntent> {
        std::mem::take(&mut self.intents)
    }
}
