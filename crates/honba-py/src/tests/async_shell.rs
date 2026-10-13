//! Unit tests for `crate::pyclasses::async_shell`: the `PyCommand` -> `honba_async::Command`
//! mapping (E2-S1 rest). The Python wrapper types are plain enums, so the conversion is
//! testable without an interpreter.

use honba_async::Command;
use honba_messages::TradingState;

use crate::pyclasses::async_shell::PyCommand;
use crate::pyclasses::risk::PyTradingState;

#[test]
fn stop_maps_to_command_stop() {
    let rust: Command = PyCommand::stop().into();
    assert_eq!(rust, Command::Stop);
}

#[test]
fn state_maps_every_trading_state_verbatim() {
    for (py_state, wire) in [
        (PyTradingState::Active, "active"),
        (PyTradingState::Reducing, "reducing"),
        (PyTradingState::Halted, "halted"),
    ] {
        let rust: Command = PyCommand::state(py_state).into();
        assert_eq!(
            rust,
            Command::State(serde_json::from_value(serde_json::json!(wire)).unwrap()),
            "{wire}"
        );
    }
}

#[test]
fn state_matches_the_stage_python_enforces() {
    // `Command::State(Reducing)` is exactly the state the stage and the runner pair uses, so
    // the operator command and the enforcement share one vocabulary.
    let rust: Command = PyCommand::state(PyTradingState::Reducing).into();
    match rust {
        Command::State(s) => assert_eq!(s, TradingState::Reducing),
        Command::Stop => panic!("expected State(Reducing)"),
    }
}
