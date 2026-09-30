//! RSI mean-reversion strategy.

use honba_algo::Result;
use honba_algo_indicators::{Indicator, Rsi};
use honba_messages::{Bar, InstrumentId, UnixNanos};

use crate::intent::OrderIntent;
use crate::strategy::Strategy;

/// Where the strategy believes it sits.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
enum Side {
    Flat,
    Long,
    Short,
}

/// Buys when RSI drops below the oversold threshold, sells when it rises
/// above the overbought threshold.
///
/// The strategy tracks its own assumed side so it doesn't emit duplicate
/// signals while RSI remains in the extreme zone.
///
/// ```
/// use honba_algo_strategies::{RsiReversal, Strategy};
/// use honba_messages::{InstrumentId, Venue};
///
/// let strategy = RsiReversal::new(
///     InstrumentId::new("NIFTY50", Venue::new("NSE")),
///     14, 30.0, 70.0, 75.0,
/// );
/// assert_eq!(strategy.name(), "rsi_reversal");
/// ```
pub struct RsiReversal {
    instrument_id: InstrumentId,
    rsi: Rsi,
    oversold: f64,
    overbought: f64,
    quantity: f64,
    side: Side,
    intents: Vec<OrderIntent>,
}

impl RsiReversal {
    /// Creates an RSI reversal strategy.
    ///
    /// # Panics
    ///
    /// Panics if `period` is zero or thresholds are out of order.
    pub fn new(
        instrument_id: InstrumentId,
        period: usize,
        oversold: f64,
        overbought: f64,
        quantity: f64,
    ) -> Self {
        assert!(period > 0, "RSI period must be positive");
        assert!(oversold < overbought, "oversold must be below overbought");
        Self {
            instrument_id,
            rsi: Rsi::new(period),
            oversold,
            overbought,
            quantity,
            side: Side::Flat,
            intents: Vec::new(),
        }
    }

    /// Returns the RSI period.
    pub fn period(&self) -> usize {
        self.rsi.period()
    }
}

impl Strategy for RsiReversal {
    fn name(&self) -> &str {
        "rsi_reversal"
    }

    fn on_bar(&mut self, bar: &Bar, _ts_init: UnixNanos) -> Result<()> {
        let Some(v) = self.rsi.update(bar.close()) else {
            return Ok(());
        };

        if v < self.oversold && self.side != Side::Long {
            self.intents.push(OrderIntent::market_buy(
                self.instrument_id.clone(),
                self.quantity,
            ));
            self.side = Side::Long;
        } else if v > self.overbought && self.side != Side::Short {
            self.intents.push(OrderIntent::market_sell(
                self.instrument_id.clone(),
                self.quantity,
            ));
            self.side = Side::Short;
        }
        Ok(())
    }

    fn drain_intents(&mut self) -> Vec<OrderIntent> {
        std::mem::take(&mut self.intents)
    }
}
