#![deny(missing_docs)]
#![deny(rustdoc::broken_intra_doc_links)]
#![deny(unsafe_code)]

//! Market data catalog, Parquet readers/writers, and report exporters for Honba.

pub mod dataset;
pub mod export;
/// Data import sources and loaders
pub mod import;
/// Dated instrument snapshots (E1-S3).
pub mod instruments;
pub mod reader;

pub use dataset::{
    ColumnarSlice, ColumnarSliceBuilder, Dataset, DatasetBuildError, DatasetChunk, DatasetFeed,
    StreamedDatasetFeed,
};
pub use export::{
    CsvReportWriter, ExportError, JsonReportWriter, MarkdownReportWriter, ReportWriter,
};
pub use import::parquet_source::{ParquetBarSource, ParquetError};
pub use instruments::{snapshot_schema, CatalogError, CatalogInstrumentMaster};
pub use reader::{DatasetReader, ReaderError};

#[cfg(test)]
mod tests;
