//! `honba._honba.api_request`: the REST API, driven in process.
//!
//! The Python SDK's in-process transport calls this instead of reimplementing handlers: it
//! builds the very router `honba serve` serves (`honba_api_rest::api_router_with` over
//! `AppState::from_parquet_dir`) and sends one request through it with
//! [`honba_api_rest::dispatch`]. There is no socket; statuses and envelopes are the served
//! API's, byte for byte.

// PyO3 0.22 `#[pyfunction]` expansion trips this lint on `PyResult` returns.
#![allow(clippy::useless_conversion)]

use std::collections::HashMap;
use std::fmt;
use std::path::{Path, PathBuf};
use std::sync::{Mutex, OnceLock};

use honba_api_rest::{
    api_router_with, build_target, dispatch, AppState, DispatchError, RunServiceConfig,
};
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyModule;

/// Why a request could not be answered at all (an error *response* is still `Ok`).
#[derive(Debug)]
pub enum ApiRequestError {
    /// The data directory could not be loaded.
    DataDir(String),
    /// The run service could not be started over the journals root.
    Runs(String),
    /// The request could not be handed to the router.
    Dispatch(DispatchError),
}

impl fmt::Display for ApiRequestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::DataDir(m) => write!(f, "data directory: {m}"),
            Self::Runs(m) => write!(f, "run service: {m}"),
            Self::Dispatch(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for ApiRequestError {}

/// Run-service settings of an in-process state (ADR 0017 decision 9).
///
/// The default has no journals root, so the run routes answer 503 `unsupported`, like a
/// `honba serve` without one would.
#[derive(Clone, Debug, Default, PartialEq, Eq, Hash)]
pub struct RunsOptions {
    /// Journals root; `Some` starts the run service (created if missing).
    pub journals_dir: Option<String>,
    /// Worker threads; `None` keeps the service default.
    pub max_concurrent: Option<usize>,
    /// Pending-queue bound; `None` keeps the service default.
    pub max_queued: Option<usize>,
}

impl RunsOptions {
    fn config(&self) -> RunServiceConfig {
        let mut config = RunServiceConfig::default();
        if let Some(n) = self.max_concurrent {
            config.max_concurrent = n.max(1);
        }
        if let Some(n) = self.max_queued {
            config.max_queued = n.max(1);
        }
        config
    }
}

type StateKey = (PathBuf, RunsOptions);

/// States by data directory and run settings: loaded once per process, like `honba serve`
/// loads once. A run service lives as long as its state, so runs outlive a Python transport.
static STATES: OnceLock<Mutex<HashMap<StateKey, AppState>>> = OnceLock::new();

fn state_for(data_dir: &str, runs: &RunsOptions) -> Result<AppState, ApiRequestError> {
    let key = (
        std::fs::canonicalize(data_dir).unwrap_or_else(|_| PathBuf::from(data_dir)),
        runs.clone(),
    );
    let mut states = STATES
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(state) = states.get(&key) {
        return Ok(state.clone());
    }
    let mut state = AppState::from_parquet_dir(Path::new(data_dir))
        .map_err(|e| ApiRequestError::DataDir(format!("{data_dir}: {e}")))?;
    if let Some(journals) = &runs.journals_dir {
        std::fs::create_dir_all(journals)
            .map_err(|e| ApiRequestError::Runs(format!("{journals}: {e}")))?;
        state = state
            .with_journals_dir(Path::new(journals), runs.config())
            .map_err(|e| ApiRequestError::Runs(format!("{journals}: {}", e.message)))?;
    }
    states.insert(key, state.clone());
    Ok(state)
}

/// Sends one request through the REST router over `data_dir` and returns `(status, body)`,
/// with the run routes off ([`RunsOptions::default`]).
///
/// `query_json` is a flat JSON object (or `None`), `body_json` the request body (or `None`).
/// The directory's state is cached for the life of the process.
pub fn request(
    data_dir: &str,
    method: &str,
    path: &str,
    query_json: Option<&str>,
    body_json: Option<&str>,
) -> Result<(u16, String), ApiRequestError> {
    request_with(
        data_dir,
        &RunsOptions::default(),
        method,
        path,
        query_json,
        body_json,
    )
}

/// [`request`] with run-service settings: `runs.journals_dir` turns on `/backtests` and
/// `/journals` for this state.
pub fn request_with(
    data_dir: &str,
    runs: &RunsOptions,
    method: &str,
    path: &str,
    query_json: Option<&str>,
    body_json: Option<&str>,
) -> Result<(u16, String), ApiRequestError> {
    let target = build_target(path, query_json).map_err(ApiRequestError::Dispatch)?;
    let router = api_router_with(state_for(data_dir, runs)?);
    crate::runtime::block_on(dispatch(router, method, &target, body_json))
        .map_err(ApiRequestError::Dispatch)
}

/// Drive the REST API in process: `(status, body_json)` for one request.
///
/// `journals_dir` starts the run service over that directory (created if missing), enabling
/// `/backtests` and `/journals`; `max_concurrent_runs` and `max_queued_runs` tune it.
///
/// Raises `OSError` when `data_dir` or `journals_dir` cannot be loaded and `ValueError` for a
/// request that is not well formed (bad method, relative path, non-flat query). A request the
/// API rejects (404, 422, 429, 501, 503) is a normal return carrying the error envelope.
#[pyfunction]
#[pyo3(signature = (data_dir, method, path, query_json=None, body_json=None, journals_dir=None, max_concurrent_runs=None, max_queued_runs=None))]
#[allow(clippy::too_many_arguments)]
pub fn api_request(
    py: Python<'_>,
    data_dir: &str,
    method: &str,
    path: &str,
    query_json: Option<&str>,
    body_json: Option<&str>,
    journals_dir: Option<&str>,
    max_concurrent_runs: Option<usize>,
    max_queued_runs: Option<usize>,
) -> PyResult<(u16, String)> {
    let runs = RunsOptions {
        journals_dir: journals_dir.map(str::to_owned),
        max_concurrent: max_concurrent_runs,
        max_queued: max_queued_runs,
    };
    py.allow_threads(|| request_with(data_dir, &runs, method, path, query_json, body_json))
        .map_err(|e| match e {
            ApiRequestError::DataDir(m) | ApiRequestError::Runs(m) => PyOSError::new_err(m),
            ApiRequestError::Dispatch(e) => PyValueError::new_err(e.to_string()),
        })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(api_request, m)?)?;
    Ok(())
}
