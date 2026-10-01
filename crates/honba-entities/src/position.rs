//! Position tracking.

use honba_messages::validation::{finite, non_negative, serialize_finite};
use honba_messages::{InstrumentId, InvariantError};
use serde::{Deserialize, Serialize};

use crate::error::{EntitiesError, Result};
use crate::instrument::Currency;

honba_messages::enum_with_all! {
    /// The direction of a position.
    #[derive(Clone, Copy, Debug, PartialEq, Eq, Hash, Serialize, Deserialize)]
    #[serde(rename_all = "snake_case")]
    #[non_exhaustive]
    pub enum PositionSide {
        /// Long (net bought).
        Long,
        /// Short (net sold).
        Short,
    }
}

impl PositionSide {
    /// Returns the sign associated with the side: `+1` for long, `-1` for short.
    pub const fn sign(&self) -> f64 {
        match self {
            PositionSide::Long => 1.0,
            PositionSide::Short => -1.0,
        }
    }

    /// Returns the opposite side.
    pub const fn opposite(&self) -> Self {
        match self {
            PositionSide::Long => PositionSide::Short,
            PositionSide::Short => PositionSide::Long,
        }
    }
}

/// A position in a single instrument.
///
/// A position is *flat* when `quantity == 0`. Fills update the position via
/// [`Position::apply_fill`]. Realized PnL accrues when a fill reduces or
/// reverses the existing position; unrealized PnL is computed on demand
/// against a mark price.
///
/// ```
/// use honba_entities::{Currency, Position, PositionSide};
/// use honba_messages::{InstrumentId, Venue};
///
/// let id = InstrumentId::new("NIFTY50", Venue::new("NSE"));
/// let mut pos = Position::flat(id, Currency::Inr);
///
/// pos.apply_fill(PositionSide::Long, 75.0, 22_000.0);
/// pos.apply_fill(PositionSide::Long, 25.0, 22_100.0);
///
/// assert_eq!(pos.quantity(), 100.0);
/// assert_eq!(pos.avg_price(), 22_025.0);   // (75*22000 + 25*22100) / 100
/// ```
///
/// Invariants (checked by [`Position::validate`] and on deserialization):
/// `quantity` and `avg_price` are finite and `>= 0` (the side carries the
/// direction), `realized_pnl` is finite.
#[derive(Clone, Debug, PartialEq, Serialize, Deserialize)]
#[serde(try_from = "PositionRepr")]
pub struct Position {
    instrument_id: InstrumentId,
    currency: Currency,
    side: PositionSide,
    #[serde(serialize_with = "serialize_finite")]
    quantity: f64,
    #[serde(serialize_with = "serialize_finite")]
    avg_price: f64,
    #[serde(serialize_with = "serialize_finite")]
    realized_pnl: f64,
}

/// The raw wire form, validated into a [`Position`].
#[derive(Deserialize)]
#[serde(deny_unknown_fields)]
struct PositionRepr {
    instrument_id: InstrumentId,
    currency: Currency,
    side: PositionSide,
    quantity: f64,
    avg_price: f64,
    realized_pnl: f64,
}

impl TryFrom<PositionRepr> for Position {
    type Error = InvariantError;

    fn try_from(r: PositionRepr) -> std::result::Result<Self, Self::Error> {
        let position = Position {
            instrument_id: r.instrument_id,
            currency: r.currency,
            side: r.side,
            quantity: r.quantity,
            avg_price: r.avg_price,
            realized_pnl: r.realized_pnl,
        };
        position.validate()?;
        Ok(position)
    }
}

impl Position {
    /// Creates a flat position.
    pub fn flat(instrument_id: InstrumentId, currency: Currency) -> Self {
        Self {
            instrument_id,
            currency,
            side: PositionSide::Long,
            quantity: 0.0,
            avg_price: 0.0,
            realized_pnl: 0.0,
        }
    }

    /// Returns the instrument id.
    pub fn instrument_id(&self) -> &InstrumentId {
        &self.instrument_id
    }

    /// Returns the settlement currency.
    pub fn currency(&self) -> Currency {
        self.currency
    }

    /// Returns the current side. Meaningful only when `quantity > 0`.
    pub fn side(&self) -> PositionSide {
        self.side
    }

    /// Returns the absolute quantity held.
    pub fn quantity(&self) -> f64 {
        self.quantity
    }

    /// Returns the volume-weighted average entry price.
    pub fn avg_price(&self) -> f64 {
        self.avg_price
    }

    /// Returns realized profit and loss in the position's currency.
    pub fn realized_pnl(&self) -> f64 {
        self.realized_pnl
    }

    /// Checks the position's invariants (see [`Position`]).
    pub fn validate(&self) -> std::result::Result<(), InvariantError> {
        non_negative("quantity", self.quantity)?;
        non_negative("avg_price", self.avg_price)?;
        finite("realized_pnl", self.realized_pnl)?;
        Ok(())
    }

    /// Returns `true` if the position holds no quantity.
    pub fn is_flat(&self) -> bool {
        self.quantity == 0.0
    }

    /// Returns unrealized PnL against a mark price.
    pub fn unrealized_pnl(&self, mark: f64) -> f64 {
        if self.is_flat() {
            return 0.0;
        }
        (mark - self.avg_price) * self.side.sign() * self.quantity
    }

    /// Applies a fill to the position.
    ///
    /// If the fill is in the same direction as the position, quantity
    /// increases and the average price updates. If it's in the opposite
    /// direction, quantity decreases and realized PnL accrues. If it exceeds
    /// the existing quantity, the position reverses.
    ///
    /// ```
    /// use honba_entities::{Currency, Position, PositionSide};
    /// use honba_messages::{InstrumentId, Venue};
    ///
    /// let id = InstrumentId::new("X", Venue::new("NSE"));
    /// let mut pos = Position::flat(id, Currency::Inr);
    /// pos.apply_fill(PositionSide::Long, 100.0, 10.0);
    /// pos.apply_fill(PositionSide::Short, 40.0, 12.0);
    /// assert_eq!(pos.quantity(), 60.0);
    /// assert_eq!(pos.realized_pnl(), 80.0);   // 40 * (12 - 10)
    /// ```
    pub fn apply_fill(&mut self, fill_side: PositionSide, qty: f64, px: f64) {
        debug_assert!(qty > 0.0, "fill quantity must be positive");

        if self.is_flat() {
            self.side = fill_side;
            self.quantity = qty;
            self.avg_price = px;
            return;
        }

        if fill_side == self.side {
            let new_qty = self.quantity + qty;
            self.avg_price = (self.avg_price * self.quantity + px * qty) / new_qty;
            self.quantity = new_qty;
            return;
        }

        // Opposite side: reduce, possibly reverse.
        let closable = self.quantity.min(qty);
        self.realized_pnl += (px - self.avg_price) * self.side.sign() * closable;

        if qty < self.quantity {
            self.quantity -= qty;
        } else if qty == self.quantity {
            self.quantity = 0.0;
            self.avg_price = 0.0;
        } else {
            // Reverse.
            self.side = fill_side;
            self.quantity = qty - self.quantity;
            self.avg_price = px;
        }
    }

    /// Returns an error if the position is not flat.
    pub fn ensure_flat(&self) -> Result<()> {
        if self.is_flat() {
            Ok(())
        } else {
            Err(EntitiesError::Arithmetic(format!(
                "position for {} is not flat (qty={})",
                self.instrument_id, self.quantity
            )))
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    use honba_messages::{InvariantError, Venue};

    fn short() -> Position {
        let mut p = Position::flat(InstrumentId::new("X", Venue::new("NSE")), Currency::Inr);
        p.apply_fill(PositionSide::Short, 60.0, 10.0);
        p
    }

    #[test]
    fn validate_reports_typed_errors() {
        assert_eq!(short().validate(), Ok(()));
        let negative = Position {
            quantity: -5.0,
            ..short()
        };
        assert_eq!(
            negative.validate(),
            Err(InvariantError::Negative {
                field: "quantity",
                value: -5.0,
            })
        );
        let json = serde_json::to_value(negative).unwrap();
        let err = serde_json::from_value::<Position>(json)
            .unwrap_err()
            .to_string();
        assert!(err.contains("quantity"), "{err}");
    }

    #[test]
    fn non_finite_values_never_serialize_as_null() {
        let p = Position {
            realized_pnl: f64::NAN,
            ..short()
        };
        assert!(serde_json::to_string(&p).is_err());
    }

    #[test]
    fn position_side_serializes_lowercase() {
        assert_eq!(serde_json::to_value(PositionSide::Long).unwrap(), "long");
        assert_eq!(serde_json::to_value(PositionSide::Short).unwrap(), "short");
    }
}
