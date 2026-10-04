//! The engine's trading state.

/// Whether the engine is trading, winding down, or stopped.
///
/// A handler asks for a change with
/// [`EngineOutput::StateChange`](crate::EngineOutput::StateChange); the engine
/// applies it through [`TradingState::can_transition_to`] and records it in
/// its [`AuditLog`](crate::AuditLog).
#[derive(Debug, Clone, Copy, PartialEq, Eq, Hash, Default)]
pub enum TradingState {
    /// Normal trading: orders are accepted.
    #[default]
    Active,
    /// Winding down: orders are still accepted, the strategy is reducing.
    Reducing,
    /// Stopped: orders are refused until the state changes again.
    Halted,
}

impl TradingState {
    /// Orders are accepted in Active and Reducing; a halted engine rejects them.
    pub fn accepts_orders(&self) -> bool {
        match self {
            TradingState::Active | TradingState::Reducing => true,
            TradingState::Halted => false,
        }
    }

    /// Whether the engine may leave this state for `next`.
    ///
    /// Every transition is permitted today, including a state to itself: an
    /// operator resumes a halted engine and a reducing strategy may resume
    /// trading, so nothing here is refused. The rule is deliberately
    /// permissive by design and is written out as the full matrix so that the
    /// live/command layer, which owns the operator controls, can narrow it
    /// without changing callers.
    pub fn can_transition_to(&self, next: TradingState) -> bool {
        match (self, next) {
            (
                TradingState::Active,
                TradingState::Active | TradingState::Reducing | TradingState::Halted,
            )
            | (
                TradingState::Reducing,
                TradingState::Active | TradingState::Reducing | TradingState::Halted,
            )
            | (
                TradingState::Halted,
                TradingState::Active | TradingState::Reducing | TradingState::Halted,
            ) => true,
        }
    }
}
