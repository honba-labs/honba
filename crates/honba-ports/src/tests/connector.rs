//! Unit tests for `crate::connector`.

use honba_messages::{Exchange, InstrumentId, UnixNanos};

use crate::{ConnectorState, MarketDataConnector, MarketEvent};

fn assert_send<T: Send + ?Sized>() {}

fn reliance() -> InstrumentId {
    InstrumentId::new("RELIANCE", Exchange::new("NSE"))
}

#[test]
fn connector_is_object_safe_and_send() {
    assert_send::<Box<dyn MarketDataConnector>>();
}

#[test]
fn markers_compare_by_value() {
    let marker = MarketEvent::GapMarker {
        expected_ts: UnixNanos::from_u64(10),
        actual_ts: UnixNanos::from_u64(20),
        symbol: reliance(),
    };
    assert_eq!(
        marker,
        MarketEvent::GapMarker {
            expected_ts: UnixNanos::from_u64(10),
            actual_ts: UnixNanos::from_u64(20),
            symbol: reliance(),
        }
    );

    let reconnecting = ConnectorState::Reconnecting {
        attempt: 2,
        backoff_ms: 400,
    };
    assert_eq!(
        reconnecting,
        ConnectorState::Reconnecting {
            attempt: 2,
            backoff_ms: 400,
        }
    );
}
