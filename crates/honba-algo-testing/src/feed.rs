//! In-memory data feeds.

use std::collections::VecDeque;

use honba_algo::{DataFeed, Result};
use honba_messages::{Event, InstrumentId, Message, QuoteTick, TradeTick, UnixNanos};

/// A feed backed by an in-memory vector of messages.
///
/// ```
/// use honba_algo::DataFeed;
/// use honba_algo_testing::VecFeed;
/// use honba_messages::UnixNanos;
///
/// let mut feed = VecFeed::new(vec![
///     VecFeed::quote("X", 1.0, 2.0, 1),
///     VecFeed::quote("X", 1.5, 2.5, 2),
/// ]);
///
/// assert!(feed.next().unwrap().is_some());
/// assert!(feed.next().unwrap().is_some());
/// assert!(feed.next().unwrap().is_none());
/// ```
pub struct VecFeed {
    items: VecDeque<Message>,
}

impl VecFeed {
    /// Creates a feed from a vector of messages.
    pub fn new(items: Vec<Message>) -> Self {
        Self { items: items.into() }
    }

    /// Creates an empty feed.
    pub fn empty() -> Self {
        Self::new(Vec::new())
    }

    /// Builds a quote message for the given symbol and timestamps.
    pub fn quote(symbol: &str, bid: f64, ask: f64, ts: u64) -> Message {
        let instrument = InstrumentId::new(symbol, honba_messages::Venue::new("TEST"));
        let t = UnixNanos::from_u64(ts);
        Message::new(
            Event::Quote(QuoteTick::new(instrument, bid, ask, 1.0, 1.0, t, t)),
            t,
        )
    }

    /// Builds a trade message for the given symbol and timestamps.
    pub fn trade(symbol: &str, price: f64, qty: f64, ts: u64) -> Message {
        let instrument = InstrumentId::new(symbol, honba_messages::Venue::new("TEST"));
        let t = UnixNanos::from_u64(ts);
        Message::new(
            Event::Trade(TradeTick::new(
                instrument,
                price,
                qty,
                honba_messages::AggressorSide::Buyer,
                honba_messages::TradeId::new(format!("T-{ts}")),
                t,
                t,
            )),
            t,
        )
    }
}

impl DataFeed for VecFeed {
    fn next(&mut self) -> Result<Option<Message>> {
        Ok(self.items.pop_front())
    }
}
