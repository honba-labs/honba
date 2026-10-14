//! The live bar feed behind the simulated market (E3-S7 follow-up).
//!
//! [`AppState::spawn_bar_pump`] consumes a `tokio::sync::mpsc` receiver of [`Bar`]s and calls
//! [`AppState::advance_market`] for each, so a running server fills orders as bars arrive.
//! Event time is the bars' own `ts_event`; the pump never reads the wall clock. It ends when
//! every sender is dropped (the graceful shutdown: the composition root drops its sender, or
//! aborts the task). An `advance_market` error (an out-of-order bar) is logged and counted,
//! and the pump keeps going.

use tokio::sync::mpsc::Receiver;
use tokio::task::JoinHandle;

use honba_messages::Bar;

use crate::AppState;

/// What a finished bar pump did.
#[derive(Debug, Clone, Copy, PartialEq, Eq, Default)]
pub struct BarPumpReport {
    /// Bars received from the source.
    pub bars: u64,
    /// Bars whose `advance_market` call failed (logged, then skipped).
    pub errors: u64,
}

impl AppState {
    /// Spawns a task feeding every bar from `bars` to [`AppState::advance_market`].
    ///
    /// Must be called inside a tokio runtime. The task ends when the channel closes and then
    /// returns its [`BarPumpReport`].
    pub fn spawn_bar_pump(&self, bars: Receiver<Bar>) -> JoinHandle<BarPumpReport> {
        let state = self.clone();
        tokio::spawn(async move { state.run_bar_pump(bars).await })
    }

    /// Runs the bar pump on the current task until `bars` closes.
    pub async fn run_bar_pump(&self, mut bars: Receiver<Bar>) -> BarPumpReport {
        let mut report = BarPumpReport::default();
        while let Some(bar) = bars.recv().await {
            report.bars += 1;
            if let Err(e) = self.advance_market(std::slice::from_ref(&bar)) {
                report.errors += 1;
                tracing::warn!(ts_event = ?bar.ts_event(), error = %e.message, "bar pump: advance_market failed; bar skipped");
            }
        }
        tracing::info!(
            bars = report.bars,
            errors = report.errors,
            "bar pump: source closed"
        );
        report
    }
}
