#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Simulation and paper execution engines for the Honba platform.

pub mod bar_fill;
pub mod paper;

pub use bar_fill::BarFillEngine;
pub use paper::{OrderLedger, PaperExecution};

#[cfg(test)]
mod tests;
