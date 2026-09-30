//! Traits for injecting holiday data and consuming calendars.

use chrono::{Datelike, Duration, NaiveDate, Weekday};

use crate::Result;

use super::Session;

/// A source of trading-holiday dates.
pub trait HolidaySource {
    /// Returns every declared trading holiday that the source knows about.
    fn holidays(&self) -> Result<Vec<NaiveDate>>;
}

/// A trading calendar: sessions, holidays, and date arithmetic.
pub trait TradingCalendar: Send + Sync {
    /// Returns the session window used on trading days.
    fn session(&self) -> Session;

    /// Returns `true` if the given date is a declared holiday.
    fn is_holiday(&self, date: NaiveDate) -> bool;

    /// Returns `true` if the given date is a trading day.
    fn is_trading_day(&self, date: NaiveDate) -> bool {
        !matches!(date.weekday(), Weekday::Sat | Weekday::Sun) && !self.is_holiday(date)
    }

    /// Returns the next trading day strictly after `date`.
    fn next_trading_day(&self, date: NaiveDate) -> NaiveDate {
        let mut d = date + Duration::days(1);
        while !self.is_trading_day(d) {
            d += Duration::days(1);
        }
        d
    }

    /// Returns the previous trading day strictly before `date`.
    fn prev_trading_day(&self, date: NaiveDate) -> NaiveDate {
        let mut d = date - Duration::days(1);
        while !self.is_trading_day(d) {
            d -= Duration::days(1);
        }
        d
    }

    /// Returns the number of trading days in `[start, end]`.
    fn trading_days_between(&self, start: NaiveDate, end: NaiveDate) -> usize {
        let mut count = 0;
        let mut d = start;
        while d <= end {
            if self.is_trading_day(d) {
                count += 1;
            }
            d += Duration::days(1);
        }
        count
    }
}
