//! The `ReportWriter` trait.

use honba_algo_analytics::PerformanceReport;

use crate::error::Result;

/// Something that serializes a [`PerformanceReport`].
pub trait ReportWriter {
    /// Writes the report.
    fn write(&mut self, report: &PerformanceReport) -> Result<()>;
}
