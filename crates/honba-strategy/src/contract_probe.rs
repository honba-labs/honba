//! The conformance probe: a reference strategy that exercises the whole contract.

use honba_engine::Result;
use honba_entities::Trade;
use honba_messages::{Bar, InstrumentId, OrderSide, QuoteTick, TradeTick, UnixNanos};
use serde::Serialize;

use crate::context::StrategyContext;
use crate::intent::OrderIntent;
use crate::strategy::Strategy;

/// What [`ContractProbe`] read from its context when a hook ran.
#[derive(Clone, Debug, PartialEq, Serialize)]
pub struct Observation {
    /// The hook (`"on_start"`, `"on_bar"`, ...).
    pub hook: &'static str,
    /// [`StrategyContext::now`].
    pub now: UnixNanos,
    /// [`StrategyContext::position`] of the probe's instrument.
    pub position: f64,
    /// [`StrategyContext::cash`].
    pub cash: f64,
    /// [`StrategyContext::busy`] for the probe's instrument.
    pub busy: bool,
    /// The number of [`StrategyContext::positions`].
    pub open_positions: usize,
}

/// Exercises every hook, every context capability and all four order types.
///
/// It is the main strategy of the shared conformance fixture
/// (`schema/conformance/strategy_contract.json`, ADR 008) and mirrors the
/// Python `honba.strategies.reference.ContractProbe` exactly. Each hook first
/// records an [`Observation`]. Quantities use the instrument's lot size and
/// prices its tick size (1.0 and 0.01 when the instrument is unknown).
///
/// - `on_start`: market buy one lot (processed with the first event).
/// - `on_bar`: if not busy, market buy one lot when flat, or sell the
///   position when the close is below the previous close.
/// - `on_quote`: if long and not busy, limit sell one lot at the ask.
/// - `on_trade`: if not busy, stop buy one lot at `price + tick` when flat,
///   or stop-limit sell one lot (trigger `price - tick`, limit
///   `price - 2 * tick`) when long.
/// - `on_fill`: after the first buy fill, stop sell its quantity at
///   `price - 10 * tick` (processed with the next event).
/// - `on_stop`: sell the position (never executed: the run is over).
///
/// ```
/// use honba_strategy::{ContractProbe, Strategy};
/// use honba_messages::{InstrumentId, Venue};
///
/// let probe = ContractProbe::new(InstrumentId::new("NIFTY50", Venue::new("NSE")));
/// assert_eq!(probe.name(), "contract_probe");
/// assert!(probe.observations().is_empty());
/// ```
pub struct ContractProbe {
    instrument_id: InstrumentId,
    observations: Vec<Observation>,
    last_close: Option<f64>,
    protected: bool,
}

impl ContractProbe {
    /// Creates a probe trading `instrument_id`.
    pub fn new(instrument_id: InstrumentId) -> Self {
        Self {
            instrument_id,
            observations: Vec::new(),
            last_close: None,
            protected: false,
        }
    }

    /// Everything the probe observed, one entry per hook call.
    pub fn observations(&self) -> &[Observation] {
        &self.observations
    }

    fn lot(&self, ctx: &dyn StrategyContext) -> f64 {
        ctx.instrument(&self.instrument_id)
            .map_or(1.0, |i| i.lot_size())
    }

    fn tick(&self, ctx: &dyn StrategyContext) -> f64 {
        ctx.instrument(&self.instrument_id)
            .map_or(0.01, |i| i.tick_size())
    }

    fn observe(&mut self, ctx: &dyn StrategyContext, hook: &'static str) {
        self.observations.push(Observation {
            hook,
            now: ctx.now(),
            position: ctx.position(&self.instrument_id),
            cash: ctx.cash(),
            busy: ctx.busy(&self.instrument_id),
            open_positions: ctx.positions().len(),
        });
    }
}

impl Strategy for ContractProbe {
    fn name(&self) -> &str {
        "contract_probe"
    }

    fn on_start(&mut self, ctx: &mut dyn StrategyContext) -> Result<()> {
        self.observe(ctx, "on_start");
        let lot = self.lot(ctx);
        ctx.submit(OrderIntent::market_buy(self.instrument_id.clone(), lot));
        Ok(())
    }

    fn on_bar(&mut self, ctx: &mut dyn StrategyContext, bar: &Bar) -> Result<()> {
        self.observe(ctx, "on_bar");
        let prev = self.last_close.replace(bar.close());
        if ctx.busy(&self.instrument_id) {
            return Ok(());
        }
        let pos = ctx.position(&self.instrument_id);
        if pos == 0.0 {
            let lot = self.lot(ctx);
            ctx.submit(OrderIntent::market_buy(self.instrument_id.clone(), lot));
        } else if pos > 0.0 && prev.is_some_and(|p| bar.close() < p) {
            ctx.submit(OrderIntent::market_sell(self.instrument_id.clone(), pos));
        }
        Ok(())
    }

    fn on_quote(&mut self, ctx: &mut dyn StrategyContext, quote: &QuoteTick) -> Result<()> {
        self.observe(ctx, "on_quote");
        if !ctx.busy(&self.instrument_id) && ctx.position(&self.instrument_id) > 0.0 {
            let lot = self.lot(ctx);
            ctx.submit(OrderIntent::limit_sell(
                self.instrument_id.clone(),
                lot,
                quote.ask_price(),
            ));
        }
        Ok(())
    }

    fn on_trade(&mut self, ctx: &mut dyn StrategyContext, trade: &TradeTick) -> Result<()> {
        self.observe(ctx, "on_trade");
        if ctx.busy(&self.instrument_id) {
            return Ok(());
        }
        let (pos, lot, tick) = (
            ctx.position(&self.instrument_id),
            self.lot(ctx),
            self.tick(ctx),
        );
        let id = self.instrument_id.clone();
        if pos == 0.0 {
            ctx.submit(OrderIntent::stop_buy(id, lot, trade.price() + tick));
        } else if pos > 0.0 {
            ctx.submit(OrderIntent::stop_limit_sell(
                id,
                lot,
                trade.price() - tick,
                trade.price() - 2.0 * tick,
            ));
        }
        Ok(())
    }

    fn on_fill(&mut self, ctx: &mut dyn StrategyContext, fill: &Trade) -> Result<()> {
        self.observe(ctx, "on_fill");
        if !self.protected && fill.side() == OrderSide::Buy {
            self.protected = true;
            let tick = self.tick(ctx);
            ctx.submit(OrderIntent::stop_sell(
                self.instrument_id.clone(),
                fill.quantity(),
                fill.price() - 10.0 * tick,
            ));
        }
        Ok(())
    }

    fn on_stop(&mut self, ctx: &mut dyn StrategyContext) -> Result<()> {
        self.observe(ctx, "on_stop");
        let pos = ctx.position(&self.instrument_id);
        if pos > 0.0 {
            ctx.submit(OrderIntent::market_sell(self.instrument_id.clone(), pos));
        }
        Ok(())
    }
}
