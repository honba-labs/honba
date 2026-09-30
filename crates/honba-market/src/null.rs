//! Null market pack for unit testing and offline simulation without real exchange data.

use chrono::{NaiveDate, NaiveTime};

use crate::calendar::TradingCalendar;
use crate::india::calendar::Session;

/// A simple 24/7 calendar that treats every single day as a trading day.
#[derive(Debug, Default, Clone, Copy)]
pub struct NullCalendar;

impl TradingCalendar for NullCalendar {
    fn session(&self) -> Session {
        Session::new(
            NaiveTime::from_hms_opt(0, 0, 0).unwrap(),
            NaiveTime::from_hms_opt(23, 59, 59).unwrap(),
        )
    }

    fn is_holiday(&self, _date: NaiveDate) -> bool {
        false
    }

    fn is_trading_day(&self, _date: NaiveDate) -> bool {
        true
    }
}
