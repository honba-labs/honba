//! Python bindings for `honba-async`: the operator `Command` (ADR 0018, E2-S1 rest).
//!
//! `_honba.Command` is the Python spelling of `honba_async::Command`, so a Python caller can
//! express the same operator intents the Rust async shell accepts. `Command::State` carries a
//! `TradingState` (the very type `honba-risk` and the engine enforce), so the spellings cannot
//! drift apart.

use pyo3::prelude::*;
use pyo3::types::PyModule;

use honba_async::Command;

use super::risk::PyTradingState;

/// The operator command crossing into a running engine task (`honba_async::Command`).
#[pyclass(name = "Command", module = "honba", frozen)]
#[derive(Debug, Clone, PartialEq, Eq)]
pub struct PyCommand {
    kind: CommandKind,
}

#[derive(Debug, Clone, PartialEq, Eq)]
enum CommandKind {
    Stop,
    State(PyTradingState),
}

impl From<PyCommand> for Command {
    fn from(cmd: PyCommand) -> Self {
        match cmd.kind {
            CommandKind::Stop => Command::Stop,
            CommandKind::State(s) => Command::State(s.into()),
        }
    }
}

fn trading_state_wire(s: &PyTradingState) -> &'static str {
    match s {
        PyTradingState::Active => "active",
        PyTradingState::Reducing => "reducing",
        PyTradingState::Halted => "halted",
    }
}

#[pymethods]
impl PyCommand {
    /// `Command::Stop`: stop the run gracefully.
    #[staticmethod]
    pub fn stop() -> PyCommand {
        PyCommand {
            kind: CommandKind::Stop,
        }
    }

    /// `Command::State`: move the engine's trading state.
    #[staticmethod]
    pub fn state(trading_state: PyTradingState) -> PyCommand {
        PyCommand {
            kind: CommandKind::State(trading_state),
        }
    }

    fn __repr__(&self) -> String {
        match &self.kind {
            CommandKind::Stop => "Command.STOP".to_string(),
            CommandKind::State(s) => {
                format!("Command.STATE({})", trading_state_wire(s).to_uppercase())
            }
        }
    }

    fn __str__(&self) -> String {
        match &self.kind {
            CommandKind::Stop => "stop".to_string(),
            CommandKind::State(s) => format!("state({})", trading_state_wire(s)),
        }
    }
}

pub fn register(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_class::<PyCommand>()?;
    Ok(())
}
