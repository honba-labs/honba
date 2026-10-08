//! Zerodha Kite Connect adapter.
#![deny(missing_docs)]

pub mod client;
pub mod mapping;
pub mod rate_limit;
pub mod ticker;
pub mod tokens;
pub mod transport;
pub mod wire;

#[cfg(test)]
mod tests;
