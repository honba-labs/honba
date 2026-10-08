//! Zerodha Kite Connect adapter.
#![deny(missing_docs)]

pub mod client;
pub mod gateway;
pub mod mapping;
pub mod rate_limit;
pub mod ticker;
pub mod tokens;
pub mod transport;
pub mod wire;

pub use gateway::ZerodhaGateway;

#[cfg(test)]
mod tests;
