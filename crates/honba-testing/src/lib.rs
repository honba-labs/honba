#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Test fixtures and helpers for the Honba platform.

pub mod assert;
pub mod feed;
pub mod recorder;

pub use assert::{assert_close, assert_close_slice};
pub use feed::VecFeed;
pub use recorder::Recorder;
