#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Simulation and paper execution engines for the Honba platform.

pub mod bar_fill;
pub mod next_open;
pub mod paper;
pub mod scripted;

pub use bar_fill::{BarFillEngine, FillCosts, FillCostsError};
pub use next_open::{FillCostFn, NextOpenSim};
pub use paper::{OrderLedger, PaperExecution};
pub use scripted::{Behavior, ScriptedExecution};

#[cfg(test)]
mod tests;
