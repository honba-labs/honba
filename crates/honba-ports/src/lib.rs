#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Async trait ports: the seam between the synchronous kernel and the asynchronous edges.
//!
//! The Honba event loop is synchronous and single-threaded. `honba-engine` owns the queue, the
//! clock and the audit trail, and every surface — backtest, paper, live — drives that same kernel
//! the same way. I/O is the exception, so it lives at the edges, behind the traits in this
//! crate, and nowhere else.
//!
//! # Ports
//!
//! | Port | Answers |
//! |---|---|
//! | [`Clock`] | what time is it, and wait |
//! | [`MarketDataFeed`] | the next market-data message |
//! | [`ExecutionGateway`] | route an order, cancel, modify, next fill |
//! | [`BarReader`] | the stored bars of one instrument over a range |
//! | [`InstrumentMaster`] | what is this instrument, and what else is there |
//! | [`QuoteReader`] | the latest top-of-book quote of one instrument |
//! | [`DepthReader`] | the order book of one instrument, to N levels |
//! | [`Sink`] | append to the audit trail |
//! | [`SecretStore`] | the credential stored under this key |
//!
//! This crate defines traits and one error taxonomy. It contains no I/O: nothing here opens a
//! socket, reads a file, spawns a task or reads the wall clock. Implementations live at the edges
//! (broker adapters, `honba-data`, `honba-async`); the fakes that make the ports testable live in
//! `honba-testing`, together with the shared contract suite every implementation must pass.
//!
//! # Thread-safety rule
//!
//! A port that owns mutable state — the methods take `&mut self` — is `Send` and not `Sync`: one
//! owner, one writer, driven by one task. A port that only reads its state — the methods take
//! `&self` — is `Send + Sync`, so it can be shared behind an `Arc`. All of them are object-safe, so a
//! registry can hold them as trait objects.
//!
//! # Errors
//!
//! Every method returns [`PortResult`]. Failures are typed values, not strings, and
//! [`PortError::is_retryable`] is the only question the edge needs to ask to decide between
//! backing off and giving up.
//!
//! # Rules the edges must keep
//!
//! 1. [`Clock`] is the only time source outside the kernel; domain code never calls
//!    `UnixNanos::now()`.
//! 2. Ordering is the kernel's job. Feeds may deliver out of order or in bursts.
//! 3. The audit stream is always complete: [`Sink`] is append-only and flushable on demand.
//! 4. `Ok(None)` means idle or exhausted, never a broken stream. A broken stream is an error.

pub mod bars;
pub mod clock;
pub mod error;
pub mod execution;
pub mod feed;
pub mod master;
pub mod secret;
pub mod sink;
pub mod snapshot;

pub use bars::{BarReader, BarRequest};
pub use clock::Clock;
pub use error::{PortError, PortResult};
pub use execution::ExecutionGateway;
pub use feed::MarketDataFeed;
pub use master::InstrumentMaster;
pub use secret::SecretStore;
pub use sink::Sink;
pub use snapshot::{DepthLevel, DepthReader, DepthSnapshot, QuoteReader};

#[cfg(test)]
mod tests;
