//! Shared state of the REST API: the read ports the handlers depend on.
//!
//! Handlers see only [`InstrumentMaster`] and [`BarReader`] (both owned by
//! `honba-ports`); which adapter answers is decided here, at the composition
//! root. This crate names `honba-data` only to build those adapters.

use std::fmt;
use std::path::Path;
use std::sync::Arc;

use honba_data::{DatasetReader, ReaderError};
use honba_ports::{BarReader, InstrumentMaster};

/// App state for REST API.
#[derive(Clone)]
pub struct AppState {
    /// Reference data: which instruments exist.
    pub instruments: Arc<dyn InstrumentMaster>,
    /// Stored historical bars.
    pub bars: Arc<dyn BarReader>,
}

impl AppState {
    /// Creates state from any pair of port implementations.
    pub fn new(instruments: Arc<dyn InstrumentMaster>, bars: Arc<dyn BarReader>) -> Self {
        Self { instruments, bars }
    }

    /// Creates state serving both ports from one [`DatasetReader`].
    ///
    /// This is the constructor for tests: build a reader over an in-memory
    /// `Dataset` and no file or network is touched.
    pub fn from_reader(reader: DatasetReader) -> Self {
        let reader = Arc::new(reader);
        Self::new(reader.clone(), reader)
    }

    /// Creates state serving the `SYMBOL.EXCHANGE.parquet` files in `dir`.
    ///
    /// Blocks on I/O; call it once at start-up. A missing or unreadable
    /// directory is an error rather than an empty catalogue.
    pub fn from_parquet_dir(dir: &Path) -> Result<Self, ReaderError> {
        DatasetReader::from_parquet_dir(dir).map(Self::from_reader)
    }
}

impl Default for AppState {
    /// An empty catalogue: no instruments and no bars.
    fn default() -> Self {
        Self::from_reader(DatasetReader::from_dataset(Default::default()))
    }
}

impl fmt::Debug for AppState {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        f.debug_struct("AppState").finish_non_exhaustive()
    }
}
