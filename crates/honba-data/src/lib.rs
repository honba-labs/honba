#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Market data catalog, Parquet readers/writers, and report exporters for Honba.

pub mod dataset;
pub mod export;
/// Data import sources and loaders
pub mod import;

pub use dataset::{ColumnarSlice, ColumnarSliceBuilder, Dataset, DatasetBuildError, DatasetFeed};
pub use export::{
    CsvReportWriter, ExportError, JsonReportWriter, MarkdownReportWriter, ReportWriter,
};
pub use import::parquet_source::{ParquetBarSource, ParquetError};

#[cfg(test)]
mod tests;
