//! Unit tests for `crate::pyclasses::python_gateway`: the Python-exception to `PortError`
//! table. It works on plain data (class names, `code`, `fatal`), so it needs no interpreter.

use honba_ports::PortError;

use crate::pyclasses::python_gateway::{classify, PyFailure};

fn failure(mro: &[&str], code: Option<&str>, fatal: bool, reject: Option<&str>) -> PyFailure {
    PyFailure {
        mro: mro.iter().map(|s| (*s).to_owned()).collect(),
        code: code.map(str::to_owned),
        fatal,
        reject_code: reject.map(str::to_owned),
        message: "boom".into(),
    }
}

#[test]
fn a_gateway_rejection_keeps_the_venue_code() {
    let f = failure(
        &["GatewayRejected", "AdapterError", "Exception"],
        Some("adapter.rejected"),
        false,
        Some("RMS"),
    );
    assert_eq!(
        classify(&f),
        PortError::Rejected {
            code: "RMS".into(),
            message: "boom".into()
        }
    );
}

#[test]
fn fatal_adapter_errors_need_the_operator() {
    let f = failure(
        &["AdapterFatalError", "AdapterError", "Exception"],
        Some("adapter.fatal"),
        true,
        None,
    );
    assert!(matches!(classify(&f), PortError::Unrecoverable(_)));
}

#[test]
fn capability_errors_are_unsupported() {
    let f = failure(
        &["CapabilityError", "AdapterError"],
        Some("adapter.capability_unsupported"),
        false,
        None,
    );
    assert!(matches!(classify(&f), PortError::Unsupported(_)));
}

#[test]
fn session_errors_are_unavailable_and_retryable() {
    for code in ["adapter.session", "adapter.session_expired"] {
        let f = failure(&["SessionError", "AdapterError"], Some(code), false, None);
        let e = classify(&f);
        assert!(matches!(e, PortError::Unavailable(_)), "{code}");
        assert!(e.is_retryable());
    }
}

#[test]
fn other_adapter_errors_are_retriable() {
    let f = failure(
        &["AdapterError", "Exception"],
        Some("adapter.error"),
        false,
        None,
    );
    assert!(matches!(classify(&f), PortError::Retriable(_)));
}

#[test]
fn builtin_exceptions_map_by_family() {
    assert_eq!(
        classify(&failure(&["TimeoutError", "OSError"], None, false, None)),
        PortError::Timeout
    );
    assert!(matches!(
        classify(&failure(
            &["ConnectionResetError", "ConnectionError", "OSError"],
            None,
            false,
            None
        )),
        PortError::Transport(_)
    ));
    assert!(matches!(
        classify(&failure(&["ValueError", "Exception"], None, false, None)),
        PortError::InvalidRequest(_)
    ));
    assert!(matches!(
        classify(&failure(
            &["KeyError", "LookupError", "Exception"],
            None,
            false,
            None
        )),
        PortError::Internal(_)
    ));
}
