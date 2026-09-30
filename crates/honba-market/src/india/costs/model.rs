//! Full transaction cost model.

use honba_messages::OrderSide;

use super::stt::SttRates;

/// The market segment an order belongs to.
#[derive(Clone, Copy, Debug, PartialEq, Eq, Hash)]
#[non_exhaustive]
pub enum Segment {
    /// Delivery equity (CNC).
    EquityDelivery,
    /// Intraday equity (MIS).
    EquityIntraday,
    /// Equity futures.
    EquityFutures,
    /// Equity options.
    EquityOptions,
}

/// Itemised cost breakdown for a single fill.
#[derive(Clone, Copy, Debug, PartialEq, Default)]
pub struct CostBreakdown {
    /// Securities Transaction Tax.
    pub stt: f64,
    /// Exchange transaction fee.
    pub exchange_fee: f64,
    /// Goods and Services Tax.
    pub gst: f64,
    /// Stamp duty (buy side only).
    pub stamp_duty: f64,
    /// SEBI turnover fee.
    pub sebi_fee: f64,
    /// Brokerage.
    pub brokerage: f64,
}

impl CostBreakdown {
    /// Sums all line items.
    pub fn total(&self) -> f64 {
        self.stt + self.exchange_fee + self.gst + self.stamp_duty + self.sebi_fee + self.brokerage
    }
}

/// A configurable transaction cost model.
#[derive(Clone, Copy, Debug)]
pub struct CostModel {
    /// STT rate table.
    pub stt: SttRates,
    /// Exchange transaction fee as a fraction of notional.
    pub exchange_fee_rate: f64,
    /// GST rate applied to (exchange fee + brokerage).
    pub gst_rate: f64,
    /// Stamp duty as a fraction of notional.
    pub stamp_duty_rate: f64,
    /// SEBI turnover fee as a fraction of notional.
    pub sebi_fee_rate: f64,
    /// Flat brokerage per order, in rupees.
    pub brokerage_flat: f64,
}

impl CostModel {
    /// Creates a cost model from explicit parameters.
    pub const fn new(
        stt: SttRates,
        exchange_fee_rate: f64,
        gst_rate: f64,
        stamp_duty_rate: f64,
        sebi_fee_rate: f64,
        brokerage_flat: f64,
    ) -> Self {
        Self {
            stt,
            exchange_fee_rate,
            gst_rate,
            stamp_duty_rate,
            sebi_fee_rate,
            brokerage_flat,
        }
    }

    /// Computes the cost breakdown for a fill.
    pub fn compute(&self, segment: Segment, side: OrderSide, notional: f64) -> CostBreakdown {
        let stt = match segment {
            Segment::EquityDelivery => notional * self.stt.for_equity_delivery(side),
            Segment::EquityIntraday => notional * self.stt.for_equity_intraday(side),
            Segment::EquityFutures => notional * self.stt.for_equity_futures(side),
            Segment::EquityOptions => notional * self.stt.for_equity_options(side),
        };

        let exchange_fee = notional * self.exchange_fee_rate;
        let sebi_fee = notional * self.sebi_fee_rate;

        let stamp_duty = match side {
            OrderSide::Buy => notional * self.stamp_duty_rate,
            _ => 0.0,
        };

        let brokerage = self.brokerage_flat;
        let gst = (exchange_fee + brokerage) * self.gst_rate;

        CostBreakdown {
            stt,
            exchange_fee,
            gst,
            stamp_duty,
            sebi_fee,
            brokerage,
        }
    }
}
