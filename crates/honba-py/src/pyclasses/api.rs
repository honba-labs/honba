//! `honba._honba.api_request`: the REST read API, driven in process.
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

use honba_api_rest::{api_router_with, build_target, dispatch, AppState, DispatchError};
use pyo3::exceptions::{PyOSError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyModule;

/// Why a request could not be answered at all (an error *response* is still `Ok`).
#[derive(Debug)]
pub enum ApiRequestError {
    /// The data directory could not be loaded.
    DataDir(String),
    /// The request could not be handed to the router.
    Dispatch(DispatchError),
}

impl fmt::Display for ApiRequestError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::DataDir(m) => write!(f, "data directory: {m}"),
            Self::Dispatch(e) => e.fmt(f),
        }
    }
}

impl std::error::Error for ApiRequestError {}

/// States by data directory: loaded once per process, like `honba serve` loads once.
static STATES: OnceLock<Mutex<HashMap<PathBuf, AppState>>> = OnceLock::new();

/// A small runtime that only drives the router; it owns no sockets and no timers.
static RUNTIME: OnceLock<tokio::runtime::Runtime> = OnceLock::new();

fn state_for(data_dir: &str) -> Result<AppState, ApiRequestError> {
    let key = std::fs::canonicalize(data_dir).unwrap_or_else(|_| PathBuf::from(data_dir));
    let mut states = STATES
        .get_or_init(Default::default)
        .lock()
        .unwrap_or_else(|poisoned| poisoned.into_inner());
    if let Some(state) = states.get(&key) {
        return Ok(state.clone());
    }
    let state = AppState::from_parquet_dir(Path::new(data_dir))
        .map_err(|e| ApiRequestError::DataDir(format!("{data_dir}: {e}")))?;
    states.insert(key, state.clone());
    Ok(state)
}

fn runtime() -> &'static tokio::runtime::Runtime {
    RUNTIME.get_or_init(|| {
        tokio::runtime::Builder::new_current_thread()
            .build()
            .expect("a current-thread runtime always builds")
    })
}

/// Sends one request through the REST router over `data_dir` and returns `(status, body)`.
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
    let target = build_target(path, query_json).map_err(ApiRequestError::Dispatch)?;
    let router = api_router_with(state_for(data_dir)?);
    runtime()
        .block_on(dispatch(router, method, &target, body_json))
        .map_err(ApiRequestError::Dispatch)
}

/// Drive the REST read API in process: `(status, body_json)` for one request.
///
/// Raises `OSError` when `data_dir` cannot be loaded and `ValueError` for a request that is
/// not well formed (bad method, relative path, non-flat query). A request the API rejects
/// (404, 422, 501) is a normal return carrying the error envelope.
#[pyfunction]
#[pyo3(signature = (data_dir, method, path, query_json=None, body_json=None))]
pub fn api_request(
    py: Python<'_>,
    data_dir: &str,
    method: &str,
    path: &str,
    query_json: Option<&str>,
    body_json: Option<&str>,
) -> PyResult<(u16, String)> {
    py.allow_threads(|| request(data_dir, method, path, query_json, body_json))
        .map_err(|e| match e {
            ApiRequestError::DataDir(m) => PyOSError::new_err(m),
            ApiRequestError::Dispatch(e) => PyValueError::new_err(e.to_string()),
        })
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(api_request, m)?)?;
    Ok(())
}
