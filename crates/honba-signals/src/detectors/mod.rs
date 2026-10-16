//! The detector families (ADR 0031 §4). Each is a small, stateless scan over [`Series`].

pub(crate) mod momentum;
pub(crate) mod trend;
pub(crate) mod volume;

use crate::catalog::Params;
use crate::detector::Detector;

/// Builds the detector instances for these parameters, in catalogue order.
pub(crate) fn instances(params: &Params) -> Vec<Box<dyn Detector>> {
    vec![
        Box::new(trend::EmaCross {
            fast: params.ema_fast,
            slow: params.ema_slow,
        }),
        Box::new(momentum::RsiTurn {
            period: params.rsi_period,
            oversold: params.rsi_oversold,
            overbought: params.rsi_overbought,
        }),
        Box::new(momentum::MacdCross),
        Box::new(volume::RelativeVolume {
            window: params.vol_window,
            multiple: params.rel_volume,
        }),
    ]
}
