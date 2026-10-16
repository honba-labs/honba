//! Unit tests for `crate::registry`.

use crate::registry::*;
use std::collections::BTreeSet;

#[test]
fn every_published_name_is_actually_registered() {
    // Guards the failure where a name is listed in a constant but never
    // added, so the artifact silently omits it.
    let set = full_registry();
    for name in published_names() {
        assert!(
            set.get(name).is_some(),
            "{name} is published but not registered"
        );
    }
}

#[test]
fn every_declared_request_and_response_name_is_registered() {
    let set = full_registry();
    for name in request_type_names() {
        assert!(
            set.get(name).is_some(),
            "{name} is declared but not registered"
        );
    }
}

#[test]
fn the_registry_has_no_dangling_references() {
    assert_eq!(full_registry().dangling_references(), Vec::<String>::new());
}

#[test]
fn published_names_are_unique() {
    let unique: BTreeSet<_> = published_names().into_iter().collect();
    assert_eq!(unique.len(), published_names().len());
}

#[test]
fn the_screener_contract_types_are_published() {
    // The Python bundle used to carry these; once Rust owned the bundle they
    // went missing, so a screener client had no schema for MetricRef & co.
    let set = full_registry();
    for name in [
        "MetricKeySpec",
        "MetricRef",
        "MetricDefinition",
        "ScreenerFilterPredicate",
        "ScreenerFilterGroup",
        "SortSpec",
        "ScreenerScanRequest",
        "ScreenerRow",
        "ScreenerScanResponse",
    ] {
        assert!(published_names().contains(&name), "{name} not published");
        assert!(set.get(name).is_some(), "{name} not registered");
    }
}

#[test]
fn the_signal_contract_types_are_published() {
    // ADR 0031 / E12-S0: the chart and the scan endpoint need a schema for the signal,
    // its reasons, the enums and the scan request/response.
    let set = full_registry();
    for name in [
        "Signal",
        "Reason",
        "SignalHorizon",
        "SignalDirection",
        "ReasonGroup",
        "ReasonPolarity",
        "SignalScanRequest",
        "SignalScanResponse",
    ] {
        assert!(published_names().contains(&name), "{name} not published");
        assert!(set.get(name).is_some(), "{name} not registered");
    }
}

#[test]
fn the_portfolio_contract_types_are_published() {
    // ADR 0032 / E13-S0: the portfolio panel and the read endpoints need a schema for the
    // snapshot, its accounts, positions, holdings, totals and the mode/source enums.
    let set = full_registry();
    for name in [
        "PortfolioSnapshot",
        "PortfolioAccount",
        "PortfolioPosition",
        "PortfolioHolding",
        "PortfolioTotals",
        "PortfolioMode",
        "PortfolioSource",
    ] {
        assert!(published_names().contains(&name), "{name} not published");
        assert!(set.get(name).is_some(), "{name} not registered");
    }
}

#[test]
fn api_responses_are_open_records_and_api_requests_are_closed_inputs() {
    // ADR 0012: the published schema must say what serde does, or a generated
    // client would reject a newer server's response.
    let set = full_registry();
    let responses = [
        "Capabilities",
        "CapabilityManifest",
        "InstrumentsResponse",
        "QuotesResponse",
        "BarsResponse",
        "DepthResponse",
        "StrategiesResponse",
        "CompiledStrategy",
        "BacktestResponse",
        "BacktestMetrics",
        "SweepResponse",
        "SweepReportResponse",
        "OrdersResponse",
        "PositionsResponse",
        "TradingStateResponse",
        "TradingStateChange",
        "TradesResponse",
        "ScreenerResponse",
        "ScreenerResultRow",
        "Signal",
        "SignalScanResponse",
        "PortfolioSnapshot",
    ];
    for name in responses {
        let schema = set.get(name).expect("registered");
        assert_ne!(
            schema.get("additionalProperties"),
            Some(&serde_json::json!(false)),
            "{name} is a response and must tolerate unknown fields"
        );
    }
    for name in request_type_names() {
        let schema = set.get(name).expect("registered");
        assert_eq!(
            schema.get("additionalProperties"),
            Some(&serde_json::json!(false)),
            "{name} is a request body and must reject unknown fields"
        );
    }
    // The signal scan request is a closed input too (ADR 0031 §6), though it is a
    // domain type, not one of honba-api's request DTOs.
    let scan = set.get("SignalScanRequest").expect("registered");
    assert_eq!(
        scan.get("additionalProperties"),
        Some(&serde_json::json!(false)),
        "SignalScanRequest must reject unknown fields"
    );
}
