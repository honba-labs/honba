//! Unit tests for `crate::error`.

use crate::{PortError, PortResult};

fn assert_std_error<E: std::error::Error>(_: &E) {}

#[test]
fn unavailable_is_retryable() {
    assert!(PortError::Unavailable("broker socket closed".into()).is_retryable());
}

#[test]
fn timeout_is_retryable() {
    assert!(PortError::Timeout.is_retryable());
}

#[test]
fn transport_is_retryable() {
    assert!(PortError::Transport("connection reset".into()).is_retryable());
}

#[test]
fn rejected_is_not_retryable() {
    let err = PortError::Rejected {
        code: "E_MARGIN".into(),
        message: "insufficient funds".into(),
    };
    assert!(!err.is_retryable());
}

#[test]
fn invalid_request_is_not_retryable() {
    assert!(!PortError::InvalidRequest("quantity must be > 0".into()).is_retryable());
}

#[test]
fn unsupported_is_not_retryable() {
    assert!(!PortError::Unsupported("opposite side fills".into()).is_retryable());
}

#[test]
fn internal_is_not_retryable() {
    assert!(!PortError::Internal("poisoned channel".into()).is_retryable());
}

#[test]
fn unavailable_display() {
    let err = PortError::Unavailable("broker socket closed".into());
    assert_eq!(err.to_string(), "port unavailable: broker socket closed");
}

#[test]
fn timeout_display() {
    assert_eq!(PortError::Timeout.to_string(), "operation timed out");
}

#[test]
fn transport_display() {
    let err = PortError::Transport("connection reset".into());
    assert_eq!(err.to_string(), "transport error: connection reset");
}

#[test]
fn rejected_display() {
    let err = PortError::Rejected {
        code: "E_MARGIN".into(),
        message: "insufficient funds".into(),
    };
    assert_eq!(
        err.to_string(),
        "broker rejected: E_MARGIN: insufficient funds"
    );
}

#[test]
fn invalid_request_display() {
    let err = PortError::InvalidRequest("quantity must be > 0".into());
    assert_eq!(err.to_string(), "invalid request: quantity must be > 0");
}

#[test]
fn unsupported_display() {
    let err = PortError::Unsupported("opposite side fills".into());
    assert_eq!(
        err.to_string(),
        "unsupported by this port: opposite side fills"
    );
}

#[test]
fn internal_display() {
    let err = PortError::Internal("poisoned channel".into());
    assert_eq!(err.to_string(), "internal port error: poisoned channel");
}

#[test]
fn errors_are_comparable_and_std_errors() {
    let err = PortError::Timeout;
    assert_eq!(err, PortError::Timeout);
    assert_ne!(err, PortError::Internal("other".into()));
    assert_std_error(&err);
}

#[test]
fn port_result_alias_propagates() {
    fn inner() -> PortResult<()> {
        Err(PortError::Timeout)
    }
    fn outer() -> PortResult<u8> {
        inner()?;
        Ok(7)
    }
    assert_eq!(outer(), Err(PortError::Timeout));
}
