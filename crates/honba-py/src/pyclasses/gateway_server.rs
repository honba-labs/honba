//! `honba._honba.GatewayServer`: the REST app with a Python broker adapter behind `/orders`
//! (ADR 0030 "async gateway bridge", E3-S7).
//!
//! It composes the pieces that already exist: [`PythonGateway`] (a Python object as a Rust
//! `ExecutionGateway`), `GatewayVenue` (the async-to-sync bridge) and `honba_api_rest::serve`.
//! The server owns a private multi-thread tokio runtime, so it never touches the interpreter's
//! shared runtime and a slow Python coroutine only occupies the gateway task, not the HTTP
//! workers. It is a plain HTTP server on a socket: drive it with any client.

// PyO3 0.22 `#[pymethods]` expansion trips this lint on `PyResult` returns.
#![allow(clippy::useless_conversion)]

use std::net::SocketAddr;
use std::path::Path;
use std::time::Duration;

use honba_api_rest::{AppState, GatewayVenue};
use pyo3::exceptions::{PyOSError, PyRuntimeError, PyValueError};
use pyo3::prelude::*;
use pyo3::types::PyModule;
use tokio::runtime::Runtime;
use tokio::sync::oneshot;
use tokio::task::JoinHandle;

use super::python_gateway::PythonGateway;

struct Running {
    runtime: Runtime,
    addr: SocketAddr,
    shutdown: oneshot::Sender<()>,
    serve: JoinHandle<std::io::Result<()>>,
    drain: JoinHandle<()>,
}

/// The REST app over `data_dir` with `gateway` as its execution venue.
///
/// `gateway` is a Python object with `submit_order(order_json)`, `cancel_order(venue_id)`,
/// `modify_order(venue_id, quantity, price)` and `next_fill()`, each sync or `async`;
/// `honba.adapters.gateway.ExecutionAdapterGateway` adapts an `ExecutionAdapter` to it.
///
/// `poll_interval_ms` is how often the adapter's fill stream is polled and `drain_interval_ms`
/// how often venue events are folded into the order ledger. `call_timeout_s` bounds every call
/// into Python (a timed-out submit rejects the order with the `timeout` code). The adapter's
/// calls run one at a time on the gateway task, so a slow call delays later broker calls but
/// never an HTTP request.
#[pyclass(name = "GatewayServer", module = "honba")]
pub struct PyGatewayServer {
    data_dir: String,
    gateway: PyObject,
    event_loop: Option<PyObject>,
    host: String,
    port: u16,
    poll: Duration,
    drain: Duration,
    timeout: Option<Duration>,
    running: Option<Running>,
}

#[pymethods]
impl PyGatewayServer {
    #[new]
    #[pyo3(signature = (data_dir, gateway, host="127.0.0.1", port=0, poll_interval_ms=50, drain_interval_ms=50, call_timeout_s=None, event_loop=None))]
    #[allow(clippy::too_many_arguments)]
    fn new(
        data_dir: &str,
        gateway: PyObject,
        host: &str,
        port: u16,
        poll_interval_ms: u64,
        drain_interval_ms: u64,
        call_timeout_s: Option<f64>,
        event_loop: Option<PyObject>,
    ) -> PyResult<Self> {
        if call_timeout_s.is_some_and(|t| !t.is_finite() || t <= 0.0) {
            return Err(PyValueError::new_err("call_timeout_s must be positive"));
        }
        Ok(Self {
            data_dir: data_dir.to_owned(),
            gateway,
            event_loop,
            host: host.to_owned(),
            port,
            poll: Duration::from_millis(poll_interval_ms.max(1)),
            drain: Duration::from_millis(drain_interval_ms.max(1)),
            timeout: call_timeout_s.map(Duration::from_secs_f64),
            running: None,
        })
    }

    /// Starts serving and returns the bound port. Raises `RuntimeError` if already started and
    /// `OSError` if the data directory cannot be loaded or the address cannot be bound.
    fn start(&mut self, py: Python<'_>) -> PyResult<u16> {
        if self.running.is_some() {
            return Err(PyRuntimeError::new_err(
                "the gateway server is already running",
            ));
        }
        let python_gateway = PythonGateway::new(
            py,
            self.gateway.clone_ref(py),
            self.event_loop.as_ref().map(|l| l.clone_ref(py)),
            self.timeout,
        )?;
        let (data_dir, bind, poll, drain) = (
            self.data_dir.clone(),
            format!("{}:{}", self.host, self.port),
            self.poll,
            self.drain,
        );
        let running = py
            .allow_threads(move || -> std::io::Result<Running> {
                let runtime = tokio::runtime::Builder::new_multi_thread()
                    .worker_threads(2)
                    .thread_name("honba-gateway-server")
                    .enable_all()
                    .build()?;
                let (addr, shutdown, serve, drain) = runtime.block_on(async {
                    let state = AppState::from_parquet_dir(Path::new(&data_dir))
                        .map_err(|e| std::io::Error::other(format!("{data_dir}: {e}")))?
                        .with_execution_gateway(GatewayVenue::spawn_with_poll(
                            python_gateway,
                            poll,
                        ));
                    let listener = tokio::net::TcpListener::bind(&bind).await?;
                    let addr = listener.local_addr()?;
                    let drain = state.spawn_venue_drain(drain);
                    let (tx, rx) = oneshot::channel::<()>();
                    let serve = tokio::spawn(honba_api_rest::serve(listener, state, async {
                        let _ = rx.await;
                    }));
                    Ok::<_, std::io::Error>((addr, tx, serve, drain))
                })?;
                Ok(Running {
                    runtime,
                    addr,
                    shutdown,
                    serve,
                    drain,
                })
            })
            .map_err(|e| PyOSError::new_err(e.to_string()))?;
        let port = running.addr.port();
        self.running = Some(running);
        Ok(port)
    }

    /// Stops serving, drains in-flight requests and joins the server threads. Idempotent.
    fn stop(&mut self, py: Python<'_>) {
        if let Some(running) = self.running.take() {
            py.allow_threads(move || {
                let _ = running.shutdown.send(());
                running.drain.abort();
                let _ = running.runtime.block_on(running.serve);
                running.runtime.shutdown_timeout(Duration::from_secs(5));
            });
        }
    }

    /// The bound port; raises `RuntimeError` before `start`.
    #[getter]
    fn port(&self) -> PyResult<u16> {
        self.running
            .as_ref()
            .map(|r| r.addr.port())
            .ok_or_else(|| PyRuntimeError::new_err("the gateway server is not running"))
    }

    /// `http://host:port` of the running server; raises `RuntimeError` before `start`.
    #[getter]
    fn url(&self) -> PyResult<String> {
        self.running
            .as_ref()
            .map(|r| format!("http://{}", r.addr))
            .ok_or_else(|| PyRuntimeError::new_err("the gateway server is not running"))
    }

    fn __enter__<'a>(mut slf: PyRefMut<'a, Self>, py: Python<'a>) -> PyResult<PyRefMut<'a, Self>> {
        slf.start(py)?;
        Ok(slf)
    }

    fn __exit__(
        &mut self,
        py: Python<'_>,
        _exc_type: PyObject,
        _exc: PyObject,
        _tb: PyObject,
    ) -> bool {
        self.stop(py);
        false
    }
}

impl Drop for PyGatewayServer {
    fn drop(&mut self) {
        if let Some(running) = self.running.take() {
            let _ = running.shutdown.send(());
            running.drain.abort();
            running.runtime.shutdown_background();
        }
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyGatewayServer>()?;
    Ok(())
}
