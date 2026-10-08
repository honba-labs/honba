//! Shared state of the REST API: the read ports the handlers depend on.
//!
//! Handlers see only the read ports owned by `honba-ports` ([`InstrumentMaster`],
//! [`BarReader`], [`QuoteReader`], [`DepthReader`]); which adapter answers is decided here, at the composition
//! root. This crate names `honba-data` only to build those adapters.

use std::fmt;
use std::path::Path;
use std::sync::{Arc, Mutex};

use honba_api::StrategyCatalog;
use honba_data::{DatasetReader, ReaderError};
use honba_entities::Currency;
use honba_messages::ErrorDetail;
use honba_ports::{BarReader, DepthReader, InstrumentMaster, QuoteReader};

use crate::{BacktestExecutor, RunService, RunServiceConfig, RunStore, StrategyRegistry};

/// App state for REST API.
#[derive(Clone)]
pub struct AppState {
    /// Reference data: which instruments exist.
    pub instruments: Arc<dyn InstrumentMaster>,
    /// Stored historical bars.
    pub bars: Arc<dyn BarReader>,
    /// Latest top-of-book quotes.
    pub quotes: Arc<dyn QuoteReader>,
    /// Order-book depth.
    pub depth: Arc<dyn DepthReader>,
    /// Strategies compiled by `POST /strategies` in this process.
    ///
    /// Session-scoped and in memory: it is a plain pure value from `honba-api`, not a port,
    /// because it holds no external resource and nothing else implements it. Clones of the
    /// state share it.
    pub strategies: Arc<Mutex<StrategyCatalog>>,
    /// The run service behind `/backtests` and `/journals`; `None` until a journals root is
    /// configured ([`AppState::with_journals_dir`]).
    pub runs: Option<Arc<RunService>>,
    /// Account currency of the runs (journal fills carry no costs; they are zero in this).
    pub account_currency: Currency,
}

impl AppState {
    /// Creates state from any pair of port implementations.
    pub fn new(
        instruments: Arc<dyn InstrumentMaster>,
        bars: Arc<dyn BarReader>,
        quotes: Arc<dyn QuoteReader>,
        depth: Arc<dyn DepthReader>,
    ) -> Self {
        Self {
            instruments,
            bars,
            quotes,
            depth,
            strategies: Arc::default(),
            runs: None,
            account_currency: Currency::Inr,
        }
    }

    /// Serves runs from `service` (tests inject a deterministic executor this way).
    pub fn with_run_service(mut self, service: RunService) -> Self {
        self.runs = Some(Arc::new(service));
        self
    }

    /// Starts the run service over the journals root `dir`: recovers and prunes it (blocking
    /// file I/O, once), then runs backtests over this state's bar reader and strategy catalog.
    ///
    /// Call it once at start-up, never from a request handler. `AppState::default()` has no
    /// journals root and so scans nothing.
    pub fn with_journals_dir(
        mut self,
        dir: &Path,
        config: RunServiceConfig,
    ) -> Result<Self, ErrorDetail> {
        let registry = StrategyRegistry::builtin();
        let executor = BacktestExecutor::new(self.bars.clone(), registry.clone());
        self.account_currency = executor.account_currency()?;
        let service = RunService::start(
            Arc::new(RunStore::system(dir.to_path_buf())),
            Arc::new(executor),
            self.strategies.clone(),
            registry,
            config,
        );
        self.runs = Some(Arc::new(service));
        Ok(self)
    }

    /// Graceful shutdown of the run service (ADR 0017 decision 5): stops admitting, cancels
    /// pending runs, waits out the grace period. Blocking; `None` when there is no service.
    pub fn shutdown_runs(&self) -> Option<crate::ShutdownReport> {
        self.runs.as_ref().map(|runs| runs.shutdown())
    }

    /// Replaces the strategy catalog (for a non-default capacity).
    pub fn with_strategy_catalog(mut self, catalog: StrategyCatalog) -> Self {
        self.strategies = Arc::new(Mutex::new(catalog));
        self
    }

    /// Creates state serving every port from one [`DatasetReader`].
    ///
    /// This is the constructor for tests: build a reader over an in-memory
    /// `Dataset` and no file or network is touched.
    pub fn from_reader(reader: DatasetReader) -> Self {
        let reader = Arc::new(reader);
        Self::new(reader.clone(), reader.clone(), reader.clone(), reader)
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
