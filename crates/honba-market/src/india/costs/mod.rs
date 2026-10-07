//! Transaction cost models for Indian markets.

pub mod cash_equity;
pub mod model;
pub mod source;
pub mod stt;

pub use cash_equity::NseCashEquitySchedule;
pub use model::{CostBreakdown, CostModel, Segment};
pub use source::CostModelSource;
pub use stt::SttRates;

/// Named charge constant for Securities Transaction Tax.
pub const CHARGE_STT: &str = "stt";
/// Named charge constant for exchange transaction fees.
pub const CHARGE_EXCHANGE_FEE: &str = "exchange_fee";
/// Named charge constant for Goods and Services Tax.
pub const CHARGE_GST: &str = "gst";
/// Named charge constant for stamp duty.
pub const CHARGE_STAMP_DUTY: &str = "stamp_duty";
/// Named charge constant for SEBI regulatory turnover fees.
pub const CHARGE_SEBI_FEE: &str = "sebi_fee";
/// Named charge constant for broker commissions.
pub const CHARGE_BROKERAGE: &str = "brokerage";
/// Named charge constant for the investor protection fund contribution.
pub const CHARGE_IPFT: &str = "ipft";
