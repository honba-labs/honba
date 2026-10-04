#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Market data catalog, Parquet readers/writers, and report exporters for Honba.

pub mod export;
/// Data import sources and loaders
pub mod import;

pub use export::{
    CsvReportWriter, ExportError, JsonReportWriter, MarkdownReportWriter, ReportWriter,
};
pub use import::parquet_source::{ParquetBarSource, ParquetError};

#[cfg(test)]
mod tests;

/// A shared, read-only dataset for backtests and sweeps.
#[derive(Clone, Debug)]
pub struct Dataset {
    /// A human-readable name for the dataset.
    pub name: String,
}

impl Dataset {
    /// Creates a new dataset.
    pub fn new(name: impl Into<String>) -> Self {
        Self { name: name.into() }
    }
}
