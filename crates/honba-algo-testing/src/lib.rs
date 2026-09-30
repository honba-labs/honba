#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Test fixtures and helpers for the Honba platform.
//!
//! This crate exists so downstream tests don't reimplement the same fixtures.
//! It is *not* a dev-dependency-only crate — integration tests need it at
//! build time, and some users run paper sessions against it directly.

pub mod assert;
pub mod feed;
pub mod paper;
pub mod recorder;

pub use assert::{assert_close, assert_close_slice};
pub use feed::VecFeed;
pub use paper::{OrderLedger, PaperExecution};
pub use recorder::Recorder;
