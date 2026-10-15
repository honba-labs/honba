//! A Python broker adapter as a Rust [`ExecutionGateway`] (ADR 0030, E3-S7).
//!
//! [`PythonGateway`] wraps a Python object and implements `honba_ports::ExecutionGateway`, so
//! `GatewayVenue` can put it behind `/orders`. The object speaks a deliberately narrow protocol
//! of JSON strings (`honba.adapters.gateway.ExecutionAdapterGateway` adapts a full
//! `ExecutionAdapter` to it):
//!
//! - `submit_order(order_json: str) -> str` returns the venue order id;
//! - `cancel_order(venue_id: str) -> None`;
//! - `modify_order(venue_id: str, quantity: float, price: float | None) -> None`;
//! - `next_fill() -> str | None` returns one `Trade` as JSON, or `None` when idle.
//!
//! Each method may be a coroutine function or a plain function.
//!
//! Concurrency rules:
//! - the GIL is only ever taken on `spawn_blocking` threads, never on a runtime worker, and
//!   never across an await;
//! - a coroutine runs on a dedicated asyncio loop in its own thread (created here unless the
//!   caller supplies one) and is awaited through `pyo3_async_runtimes::into_future_with_locals`;
//! - a plain function runs on the blocking pool.
//!
//! Failures: a raised exception is classified by [`classify`] (the idea of
//! `honba.adapters.errors`: fatal vs recoverable vs refused) so `GatewayVenue`'s
//! `PortError -> ErrorCode` table applies unchanged.

use std::future::Future;
use std::pin::Pin;
use std::sync::Arc;
use std::time::Duration;

use async_trait::async_trait;
use honba_entities::Trade;
use honba_messages::{Order, OrderId};
use honba_ports::{ExecutionGateway, PortError, PortResult};
use pyo3::prelude::*;
use pyo3::types::{PyDict, PyTuple};
use pyo3_async_runtimes::TaskLocals;

/// What the error table needs to know about a raised Python exception.
#[derive(Debug, Clone)]
pub struct PyFailure {
    /// Class names of the exception type's MRO, most specific first.
    pub mro: Vec<String>,
    /// The `code` class attribute of an `AdapterError`, if any.
    pub code: Option<String>,
    /// The `fatal` class attribute of an `AdapterError`.
    pub fatal: bool,
    /// The venue's refusal code, set by `GatewayRejected`.
    pub reject_code: Option<String>,
    /// `str(exception)`.
    pub message: String,
}

/// Maps a Python failure onto the port taxonomy.
///
/// Order matters: a venue refusal first (it is data, not an outage), then capability, then
/// fatal (`AdapterFatalError`, needs the operator), then session problems (recoverable), then
/// the builtin families, then any other `AdapterError` (recoverable by contract). Anything
/// else is a bug in the adapter and is `Internal`.
pub fn classify(f: &PyFailure) -> PortError {
    let has = |name: &str| f.mro.iter().any(|n| n == name);
    let code = f.code.as_deref().unwrap_or("");
    let message = f.message.clone();
    if let Some(reject) = &f.reject_code {
        return PortError::Rejected {
            code: reject.clone(),
            message,
        };
    }
    if code == "adapter.capability_unsupported" {
        return PortError::Unsupported(message);
    }
    if f.fatal {
        return PortError::Unrecoverable(message);
    }
    if code.starts_with("adapter.session") {
        return PortError::Unavailable(message);
    }
    if has("TimeoutError") {
        return PortError::Timeout;
    }
    if has("ConnectionError") || has("OSError") {
        return PortError::Transport(message);
    }
    if has("ValueError") || has("TypeError") {
        return PortError::InvalidRequest(message);
    }
    if code.starts_with("adapter.") {
        return PortError::Retriable(message);
    }
    PortError::Internal(message)
}

fn failure_of(py: Python<'_>, err: &PyErr) -> PyFailure {
    let ty = err.get_type_bound(py);
    let mro = ty
        .getattr("__mro__")
        .and_then(|m| {
            m.downcast_into::<PyTuple>().map_err(PyErr::from).map(|t| {
                t.iter()
                    .filter_map(|c| c.getattr("__name__").ok()?.extract().ok())
                    .collect()
            })
        })
        .unwrap_or_default();
    let value = err.value_bound(py);
    let attr = |name: &str| value.getattr(name).ok();
    PyFailure {
        mro,
        code: attr("code").and_then(|v| v.extract().ok()),
        fatal: attr("fatal")
            .and_then(|v| v.is_truthy().ok())
            .unwrap_or(false),
        reject_code: attr("reject_code").and_then(|v| v.extract().ok()),
        message: value.str().map(|s| s.to_string()).unwrap_or_default(),
    }
}

fn map_err(py: Python<'_>, err: &PyErr) -> PortError {
    classify(&failure_of(py, err))
}

/// Starts an asyncio loop on a daemon thread and returns it.
fn spawn_loop(py: Python<'_>) -> PyResult<Py<PyAny>> {
    let event_loop = py.import_bound("asyncio")?.call_method0("new_event_loop")?;
    let kwargs = PyDict::new_bound(py);
    kwargs.set_item("target", event_loop.getattr("run_forever")?)?;
    kwargs.set_item("daemon", true)?;
    kwargs.set_item("name", "honba-gateway-loop")?;
    let thread = py
        .import_bound("threading")?
        .call_method("Thread", (), Some(&kwargs))?;
    thread.call_method0("start")?;
    Ok(event_loop.unbind())
}

/// An asyncio loop this gateway started, stopped when it is dropped.
struct OwnedLoop {
    event_loop: Py<PyAny>,
}

impl Drop for OwnedLoop {
    fn drop(&mut self) {
        Python::with_gil(|py| {
            let lp = self.event_loop.bind(py);
            if let Ok(stop) = lp.getattr("stop") {
                let _ = lp.call_method1("call_soon_threadsafe", (stop,));
            }
        });
    }
}

/// An argument passed to the Python object.
enum Arg {
    Str(String),
    F64(f64),
    OptF64(Option<f64>),
}

/// The first step of a call: a finished value, or an awaitable already scheduled on the loop.
enum Step {
    Ready(PyObject),
    Pending(Pin<Box<dyn Future<Output = PyResult<PyObject>> + Send>>),
}

/// A Python object driven as an [`ExecutionGateway`].
pub struct PythonGateway {
    target: Py<PyAny>,
    locals: Arc<TaskLocals>,
    timeout: Option<Duration>,
    _owned_loop: Option<OwnedLoop>,
}

impl PythonGateway {
    /// Wraps `target`. With no `event_loop` a loop is started on its own thread and stopped
    /// when the gateway drops. `timeout` bounds every call (`PortError::Timeout`).
    pub fn new(
        py: Python<'_>,
        target: Py<PyAny>,
        event_loop: Option<Py<PyAny>>,
        timeout: Option<Duration>,
    ) -> PyResult<Self> {
        let (lp, owned) = match event_loop {
            Some(lp) => (lp, None),
            None => {
                let lp = spawn_loop(py)?;
                let owned = OwnedLoop {
                    event_loop: lp.clone_ref(py),
                };
                (lp, Some(owned))
            }
        };
        let locals = TaskLocals::new(lp.into_bound(py)).copy_context(py)?;
        Ok(Self {
            target,
            locals: Arc::new(locals),
            timeout,
            _owned_loop: owned,
        })
    }

    async fn on_blocking<R: Send + 'static>(
        f: impl FnOnce() -> R + Send + 'static,
    ) -> PortResult<R> {
        tokio::task::spawn_blocking(f)
            .await
            .map_err(|e| PortError::Internal(format!("python call task failed: {e}")))
    }

    /// Calls `method(*args)`, awaits it if it is awaitable, and converts the result.
    async fn call<T: Send + 'static>(
        &self,
        method: &'static str,
        args: Vec<Arg>,
        convert: fn(&Bound<'_, PyAny>) -> PyResult<T>,
    ) -> PortResult<T> {
        let target = Python::with_gil(|py| self.target.clone_ref(py));
        let locals = Arc::clone(&self.locals);
        let step = Self::on_blocking(move || {
            Python::with_gil(|py| -> PortResult<Step> {
                let bound = target.bind(py);
                let py_args = PyTuple::new_bound(
                    py,
                    args.into_iter().map(|a| match a {
                        Arg::Str(s) => s.into_py(py),
                        Arg::F64(v) => v.into_py(py),
                        Arg::OptF64(v) => v.into_py(py),
                    }),
                );
                let result = bound
                    .call_method1(method, py_args)
                    .map_err(|e| map_err(py, &e))?;
                let awaitable = py
                    .import_bound("inspect")
                    .and_then(|m| m.call_method1("isawaitable", (&result,)))
                    .and_then(|b| b.is_truthy())
                    .map_err(|e| map_err(py, &e))?;
                if awaitable {
                    let fut = pyo3_async_runtimes::into_future_with_locals(&locals, result)
                        .map_err(|e| map_err(py, &e))?;
                    Ok(Step::Pending(Box::pin(fut)))
                } else {
                    Ok(Step::Ready(result.unbind()))
                }
            })
        })
        .await??;
        let value = match step {
            Step::Ready(v) => v,
            Step::Pending(fut) => {
                let outcome = match self.timeout {
                    Some(limit) => tokio::time::timeout(limit, fut)
                        .await
                        .map_err(|_| PortError::Timeout)?,
                    None => fut.await,
                };
                match outcome {
                    Ok(v) => v,
                    Err(e) => {
                        return Self::on_blocking(move || Python::with_gil(|py| map_err(py, &e)))
                            .await
                            .and_then(Err)
                    }
                }
            }
        };
        Self::on_blocking(move || {
            Python::with_gil(|py| convert(value.bind(py)).map_err(|e| map_err(py, &e)))
        })
        .await?
    }
}

fn to_string(v: &Bound<'_, PyAny>) -> PyResult<String> {
    v.extract()
}

fn to_unit(_: &Bound<'_, PyAny>) -> PyResult<()> {
    Ok(())
}

fn to_opt_string(v: &Bound<'_, PyAny>) -> PyResult<Option<String>> {
    if v.is_none() {
        Ok(None)
    } else {
        v.extract().map(Some)
    }
}

#[async_trait]
impl ExecutionGateway for PythonGateway {
    async fn submit_order(&mut self, order: Order) -> PortResult<OrderId> {
        let json = serde_json::to_string(&order)
            .map_err(|e| PortError::Internal(format!("order does not serialize: {e}")))?;
        let id = self
            .call("submit_order", vec![Arg::Str(json)], to_string)
            .await?;
        if id.is_empty() {
            return Err(PortError::Internal(
                "adapter returned an empty venue order id".into(),
            ));
        }
        Ok(OrderId::new(id))
    }

    async fn cancel_order(&mut self, id: OrderId) -> PortResult<()> {
        self.call(
            "cancel_order",
            vec![Arg::Str(id.as_str().to_owned())],
            to_unit,
        )
        .await
    }

    async fn modify_order(
        &mut self,
        id: OrderId,
        new_qty: f64,
        new_price: Option<f64>,
    ) -> PortResult<()> {
        self.call(
            "modify_order",
            vec![
                Arg::Str(id.as_str().to_owned()),
                Arg::F64(new_qty),
                Arg::OptF64(new_price),
            ],
            to_unit,
        )
        .await
    }

    async fn next_fill(&mut self) -> PortResult<Option<Trade>> {
        match self.call("next_fill", Vec::new(), to_opt_string).await? {
            None => Ok(None),
            Some(json) => serde_json::from_str(&json)
                .map(Some)
                .map_err(|e| PortError::Internal(format!("adapter fill is not a Trade: {e}"))),
        }
    }
}
