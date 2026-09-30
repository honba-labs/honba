//! NSE trading calendar, built from injected holiday data.

use std::collections::HashSet;

use chrono::{NaiveDate, NaiveTime};

use crate::Result;

use super::source::{HolidaySource, TradingCalendar};

/// A trading session window on a given day.
#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub struct Session {
    open: NaiveTime,
    close: NaiveTime,
}

impl Session {
    /// Creates a session between the given times.
    pub fn new(open: NaiveTime, close: NaiveTime) -> Self {
        debug_assert!(close > open, "session close must be after open");
        Self { open, close }
    }

    /// The regular equity session: 09:15 to 15:30 IST.
    pub fn regular() -> Self {
        Self {
            open: NaiveTime::from_hms_opt(9, 15, 0).unwrap(),
            close: NaiveTime::from_hms_opt(15, 30, 0).unwrap(),
        }
    }

    /// The opening time.
    pub fn open(&self) -> NaiveTime { self.open }

    /// The closing time.
    pub fn close(&self) -> NaiveTime { self.close }

    /// Returns `true` if the time falls within `[open, close)`.
    pub fn contains(&self, t: NaiveTime) -> bool {
        t >= self.open && t < self.close
    }
}

/// The NSE trading calendar.
#[derive(Clone, Debug)]
pub struct NseCalendar {
    holidays: HashSet<NaiveDate>,
    session: Session,
}

impl NseCalendar {
    /// Builds a calendar from an explicit set of holiday dates.
    pub fn from_holidays(holidays: impl IntoIterator<Item = NaiveDate>) -> Self {
        Self {
            holidays: holidays.into_iter().collect(),
            session: Session::regular(),
        }
    }

    /// Builds a calendar by querying a [`HolidaySource`].
    pub fn from_source<S: HolidaySource + ?Sized>(source: &S) -> Result<Self> {
        Ok(Self::from_holidays(source.holidays()?))
    }

    /// Overrides the default session window.
    pub fn with_session(mut self, session: Session) -> Self {
        self.session = session;
        self
    }

    /// Iterates over the declared holidays.
    pub fn holidays(&self) -> impl Iterator<Item = &NaiveDate> {
        self.holidays.iter()
    }
}

impl TradingCalendar for NseCalendar {
    fn session(&self) -> Session { self.session }

    fn is_holiday(&self, date: NaiveDate) -> bool {
        self.holidays.contains(&date)
    }
}
