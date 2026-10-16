//! Indicator series computed once per evaluation and shared by every detector.
//!
//! Each series is a `Vec<Option<f64>>` of the same length as the bars: `None` until the
//! indicator has primed, so a detector naturally cannot fire before its inputs are warm. Built
//! with the incremental indicators in `honba-indicators`, so the computation is causal.

use honba_indicators::{Ema, Indicator, Macd, MacdValue, Rsi, Sma};
use honba_messages::Bar;

use crate::catalog::Params;

/// The bar columns plus the indicator series the detectors read.
pub(crate) struct Series {
    /// Open prices, one per bar.
    pub open: Vec<f64>,
    /// Close prices.
    pub close: Vec<f64>,
    /// Volumes.
    pub volume: Vec<f64>,
    /// Fast EMA of close.
    pub ema_fast: Vec<Option<f64>>,
    /// Slow EMA of close.
    pub ema_slow: Vec<Option<f64>>,
    /// RSI of close.
    pub rsi: Vec<Option<f64>>,
    /// MACD of close.
    pub macd: Vec<Option<MacdValue>>,
    /// Simple moving average of volume.
    pub vol_sma: Vec<Option<f64>>,
}

impl Series {
    /// Computes every series over a closed bar window.
    pub fn compute(bars: &[Bar], params: &Params) -> Self {
        let n = bars.len();
        let mut open = Vec::with_capacity(n);
        let mut close = Vec::with_capacity(n);
        let mut volume = Vec::with_capacity(n);
        let mut ema_fast = Vec::with_capacity(n);
        let mut ema_slow = Vec::with_capacity(n);
        let mut rsi = Vec::with_capacity(n);
        let mut macd = Vec::with_capacity(n);
        let mut vol_sma = Vec::with_capacity(n);

        let mut ema_fast_ind = Ema::new(params.ema_fast);
        let mut ema_slow_ind = Ema::new(params.ema_slow);
        let mut rsi_ind = Rsi::new(params.rsi_period);
        let mut macd_ind = Macd::new(params.macd_fast, params.macd_slow, params.macd_signal);
        let mut vol_sma_ind = Sma::new(params.vol_window);

        for bar in bars {
            let c = bar.close();
            open.push(bar.open());
            close.push(c);
            volume.push(bar.volume());
            ema_fast.push(ema_fast_ind.update(c));
            ema_slow.push(ema_slow_ind.update(c));
            rsi.push(rsi_ind.update(c));
            macd.push(macd_ind.update(c));
            vol_sma.push(vol_sma_ind.update(bar.volume()));
        }

        Self {
            open,
            close,
            volume,
            ema_fast,
            ema_slow,
            rsi,
            macd,
            vol_sma,
        }
    }

    /// Number of bars.
    pub fn len(&self) -> usize {
        self.close.len()
    }
}
