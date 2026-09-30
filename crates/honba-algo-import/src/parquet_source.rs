//! Parquet bar source.
//!
//! Expects a single file with columns:
//! `ts` (int64, UnixNanos), `open`, `high`, `low`, `close` (float64),
//! `volume` (float64). Extra columns are ignored.
//!
//! The bar type is fixed at construction; all rows are attributed to the
//! same instrument and specification.

use std::fs::File;
use std::path::Path;

use arrow::array::{Array, Float64Array, Int64Array};
use arrow::datatypes::{DataType, Field, Schema};
use arrow::record_batch::RecordBatch;
use parquet::arrow::arrow_reader::ParquetRecordBatchReaderBuilder;
use thiserror::Error;

use honba_messages::{
    Bar, BarAggregation, BarSpecification, BarType, InstrumentId, PriceType, UnixNanos,
};

#[derive(Debug, Error)]
pub enum ParquetError {
    #[error("io error: {0}")]
    Io(#[from] std::io::Error),
    #[error("parquet error: {0}")]
    Parquet(#[from] parquet::errors::ParquetError),
    #[error("arrow error: {0}")]
    Arrow(#[from] arrow::error::ArrowError),
    #[error("missing column: {0}")]
    MissingColumn(&'static str),
    #[error("column {name} has type {got:?}, expected {want:?}")]
    WrongType {
        name: &'static str,
        got: DataType,
        want: DataType,
    },
}

pub struct ParquetBarSource {
    bar_type: BarType,
    path: std::path::PathBuf,
}

impl ParquetBarSource {
    /// Default spec: 1-minute bars, last price. Override with
    /// [`ParquetBarSource::with_spec`] when the file contains other intervals.
    pub fn new(path: impl AsRef<Path>, instrument: InstrumentId) -> Self {
        let spec = BarSpecification::new(1, BarAggregation::Minute, PriceType::Last);
        Self::with_spec(path, instrument, spec)
    }

    pub fn with_spec(
        path: impl AsRef<Path>,
        instrument: InstrumentId,
        spec: BarSpecification,
    ) -> Self {
        Self {
            bar_type: BarType::new(instrument, spec),
            path: path.as_ref().to_path_buf(),
        }
    }

    pub fn bars(&self) -> Result<Vec<Bar>, ParquetError> {
        let file = File::open(&self.path)?;
        let builder = ParquetRecordBatchReaderBuilder::try_new(file)?;
        let reader = builder.build()?;

        let mut out = Vec::new();
        for batch in reader {
            let batch = batch?;
            self.append_batch(&batch, &mut out)?;
        }
        Ok(out)
    }

    fn append_batch(&self, batch: &RecordBatch, out: &mut Vec<Bar>) -> Result<(), ParquetError> {
        let schema = batch.schema();
        let idx = |name: &'static str| -> Result<usize, ParquetError> {
            schema
                .index_of(name)
                .map_err(|_| ParquetError::MissingColumn(name))
        };
        let i_ts = idx("ts")?;
        let i_o = idx("open")?;
        let i_h = idx("high")?;
        let i_l = idx("low")?;
        let i_c = idx("close")?;
        let i_v = idx("volume")?;

        let ts = downcast_i64(batch.column(i_ts).as_ref(), "ts")?;
        let o = downcast_f64(batch.column(i_o).as_ref(), "open")?;
        let h = downcast_f64(batch.column(i_h).as_ref(), "high")?;
        let l = downcast_f64(batch.column(i_l).as_ref(), "low")?;
        let c = downcast_f64(batch.column(i_c).as_ref(), "close")?;
        let v = downcast_f64(batch.column(i_v).as_ref(), "volume")?;

        for row in 0..batch.num_rows() {
            if ts.is_null(row) {
                continue;
            }
            let t = UnixNanos::from_u64(ts.value(row) as u64);
            out.push(Bar::new(
                self.bar_type.clone(),
                o.value(row),
                h.value(row),
                l.value(row),
                c.value(row),
                v.value(row),
                t, // ts_event
                t, // ts_init
            ));
        }
        Ok(())
    }
}

fn downcast_i64<'a>(
    arr: &'a dyn Array,
    name: &'static str,
) -> Result<&'a Int64Array, ParquetError> {
    arr.as_any()
        .downcast_ref::<Int64Array>()
        .ok_or_else(|| ParquetError::WrongType {
            name,
            got: arr.data_type().clone(),
            want: DataType::Int64,
        })
}

fn downcast_f64<'a>(
    arr: &'a dyn Array,
    name: &'static str,
) -> Result<&'a Float64Array, ParquetError> {
    arr.as_any()
        .downcast_ref::<Float64Array>()
        .ok_or_else(|| ParquetError::WrongType {
            name,
            got: arr.data_type().clone(),
            want: DataType::Float64,
        })
}

/// Schema helper so producers and consumers agree on column layout.
pub fn bar_schema() -> Schema {
    Schema::new(vec![
        Field::new("ts", DataType::Int64, false),
        Field::new("open", DataType::Float64, false),
        Field::new("high", DataType::Float64, false),
        Field::new("low", DataType::Float64, false),
        Field::new("close", DataType::Float64, false),
        Field::new("volume", DataType::Float64, false),
    ])
}
