//! Execution engine trait.

use honba_entities::Trade;
use honba_messages::Order;

use crate::error::Result;

/// Receives orders and produces fills.
///
/// Implementations range from a paper-trading simulator (fills against
/// observed prices) to a live adapter (routes to a broker).
pub trait ExecutionEngine: Send {
    /// Submits an order.
    fn submit(&mut self, order: Order) -> Result<()>;

    /// Cancels an order by id.
    fn cancel(&mut self, order_id: &str) -> Result<()>;

    /// Drains any fills produced since the last call.
    fn drain_fills(&mut self) -> Result<Vec<Trade>>;
}
