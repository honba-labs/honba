//! Bidirectional map between Kite instrument tokens and Honba instruments.

use std::collections::HashMap;

use honba_messages::{Exchange, InstrumentId};

/// Errors raised while building a [`TokenMap`].
#[derive(Debug, thiserror::Error)]
#[non_exhaustive]
pub enum TokensError {
    /// A CSV row could not be parsed.
    #[error("invalid instruments csv: {0}")]
    Csv(String),
}

/// Bidirectional token <-> instrument map.
#[derive(Debug, Default, Clone)]
pub struct TokenMap {
    by_token: HashMap<u32, InstrumentId>,
    by_instrument: HashMap<InstrumentId, u32>,
}

impl TokenMap {
    /// Creates an empty map.
    pub fn new() -> Self {
        Self::default()
    }

    /// Inserts a pair, removing any stale entry for the same token or instrument so both
    /// directions stay consistent.
    pub fn insert(&mut self, token: u32, instrument: InstrumentId) {
        if let Some(old) = self.by_token.remove(&token) {
            self.by_instrument.remove(&old);
        }
        if let Some(old) = self.by_instrument.remove(&instrument) {
            self.by_token.remove(&old);
        }
        self.by_instrument.insert(instrument.clone(), token);
        self.by_token.insert(token, instrument);
    }

    /// Returns the token for an instrument.
    pub fn token_for(&self, instrument: &InstrumentId) -> Option<u32> {
        self.by_instrument.get(instrument).copied()
    }

    /// Returns the instrument for a token.
    pub fn instrument_for(&self, token: u32) -> Option<&InstrumentId> {
        self.by_token.get(&token)
    }

    /// Returns the number of pairs.
    pub fn len(&self) -> usize {
        self.by_token.len()
    }

    /// Returns `true` when the map holds no pairs.
    pub fn is_empty(&self) -> bool {
        self.by_token.is_empty()
    }

    /// Parses the Kite daily instruments dump, mapping `(tradingsymbol, exchange)` to the
    /// instrument token. Later duplicate rows replace earlier ones.
    pub fn from_instruments_csv(csv_text: &str) -> Result<Self, TokensError> {
        let mut reader = csv::Reader::from_reader(csv_text.as_bytes());
        let headers = reader
            .headers()
            .map_err(|e| TokensError::Csv(e.to_string()))?
            .clone();
        let col = |name: &str| {
            headers
                .iter()
                .position(|h| h == name)
                .ok_or_else(|| TokensError::Csv(format!("missing column {name}")))
        };
        let (c_tok, c_sym, c_exch) = (
            col("instrument_token")?,
            col("tradingsymbol")?,
            col("exchange")?,
        );
        let mut map = Self::new();
        for (i, rec) in reader.records().enumerate() {
            let rec = rec.map_err(|e| TokensError::Csv(e.to_string()))?;
            let field = |c: usize| {
                rec.get(c)
                    .ok_or_else(|| TokensError::Csv(format!("row {}: missing field", i + 1)))
            };
            let token: u32 = field(c_tok)?
                .parse()
                .map_err(|_| TokensError::Csv(format!("row {}: bad instrument_token", i + 1)))?;
            let instrument = InstrumentId::new(field(c_sym)?, Exchange::new(field(c_exch)?));
            map.insert(token, instrument);
        }
        Ok(map)
    }
}
