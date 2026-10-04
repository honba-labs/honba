//! The order-routing port.

use async_trait::async_trait;
use honba_entities::Trade;
use honba_messages::{Order, OrderId};

use crate::error::PortResult;

/// A venue that accepts orders and reports fills.
///
/// The gateway is the async mirror of `honba_engine::ExecutionEngine`: the kernel decides what
/// to trade synchronously, this port does the talking, and the fills come back through the same
/// stream they were routed on.
///
/// Submitting is not filling. A returned [`OrderId`] means the request was accepted for
/// routing; the fill, or the rejection, arrives later through [`ExecutionGateway::next_fill`] or
/// as an error from `submit_order`. The gateway owns order state, so it is `Send` and driven by
/// one task.
#[async_trait]
pub trait ExecutionGateway: Send {
    /// Routes `order` and returns the id it was accepted under.
    ///
    /// Returns [`PortError::Rejected`](crate::PortError::Rejected) when the venue refused the
    /// order on its merits; that is not retryable.
    async fn submit_order(&mut self, order: Order) -> PortResult<OrderId>;

    /// Cancels a working order.
    ///
    /// A venue that has already filled or cancelled the order reports
    /// [`PortError::Rejected`](crate::PortError::Rejected); the caller then has to reconcile
    /// against the fill stream rather than retry.
    async fn cancel_order(&mut self, id: OrderId) -> PortResult<()>;

    /// Replaces the working quantity with `new_qty` and, when given, the limit price.
    ///
    /// Implementations that cannot modify an order return
    /// [`PortError::Unsupported`](crate::PortError::Unsupported) instead of emulating it.
    async fn modify_order(
        &mut self,
        id: OrderId,
        new_qty: f64,
        new_price: Option<f64>,
    ) -> PortResult<()>;

    /// Returns the next fill, or `Ok(None)` when none is pending.
    ///
    /// As with [`MarketDataFeed::next`](crate::MarketDataFeed::next), `Ok(None)` means idle and
    /// `Err` means the stream is broken.
    async fn next_fill(&mut self) -> PortResult<Option<Trade>>;
}
