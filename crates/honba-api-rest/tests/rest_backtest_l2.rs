//! `fill_model = l2` through the real router (ADR 0030): with a tick source configured the
//! run matches against ticks; without one it fails with the stable `l2_requires_ticks` reason.

mod support;

use std::sync::Arc;
use std::time::{Duration, Instant};

use axum::body::Body;
use axum::http::{Request, StatusCode};
use axum::Router;
use honba_api_rest::{api_router_with, AppState, RunServiceConfig};
use honba_config::{ExecutionConfig, FillModel};
use honba_feed::InMemoryTickReader;
use honba_messages::{Exchange, InstrumentId, QuoteTick, UnixNanos};
use serde_json::Value;
use support::*;
use tower::util::ServiceExt;

const BARS_FROM_NS: u64 = 1_704_067_200_000_000_000;
const DAY_NS: u64 = 86_400_000_000_000;

fn l2() -> ExecutionConfig {
    ExecutionConfig {
        fill_model: FillModel::L2,
        ..ExecutionConfig::default()
    }
}

fn state() -> AppState {
    let reader = fixture_reader();
    AppState::new(reader.clone(), reader.clone(), reader.clone(), reader).with_execution(l2())
}

async fn call(
    router: &Router,
    method: &str,
    uri: &str,
    body: Option<Value>,
) -> (StatusCode, Value) {
    let mut req = Request::builder().method(method).uri(uri);
    let body = match body {
        Some(v) => {
            req = req.header("content-type", "application/json");
            Body::from(v.to_string())
        }
        None => Body::empty(),
    };
    let res = router
        .clone()
        .oneshot(req.body(body).unwrap())
        .await
        .unwrap();
    let status = res.status();
    let bytes = axum::body::to_bytes(res.into_body(), usize::MAX)
        .await
        .unwrap();
    (status, serde_json::from_slice(&bytes).unwrap())
}

async fn run_to_end(router: &Router) -> Value {
    let body = serde_json::to_value(sma_request(42)).unwrap();
    let (status, submitted) = call(router, "POST", "/backtests", Some(body)).await;
    assert_eq!(status, StatusCode::OK, "{submitted}");
    let id = submitted["data"]["run_id"].as_str().unwrap().to_owned();
    let deadline = Instant::now() + Duration::from_secs(30);
    loop {
        let (_, body) = call(router, "GET", &format!("/backtests/{id}"), None).await;
        if matches!(
            body["data"]["status"].as_str(),
            Some("completed" | "failed")
        ) {
            return body;
        }
        assert!(Instant::now() < deadline, "run never finished");
        tokio::time::sleep(Duration::from_millis(5)).await;
    }
}

#[tokio::test]
async fn l2_without_a_tick_source_fails_with_the_stable_reason() {
    let scratch = Scratch::new("rest-l2-none");
    let router = api_router_with(
        state()
            .with_journals_dir(&scratch.journals(), RunServiceConfig::default())
            .unwrap(),
    );
    let done = run_to_end(&router).await;
    assert_eq!(done["data"]["status"], "failed", "{done}");
    assert_eq!(
        done["data"]["error"]["context"]["reason"],
        "l2_requires_ticks"
    );
}

#[tokio::test]
async fn l2_with_a_tick_source_completes() {
    let scratch = Scratch::new("rest-l2-ticks");
    let tcs = InstrumentId::new("TCS", Exchange::new("NSE"));
    let quotes = (0..80u64)
        .map(|i| {
            let t = UnixNanos::from_u64(BARS_FROM_NS + i * DAY_NS);
            let mid = 100.0 + i as f64 * 0.05;
            QuoteTick::new(tcs.clone(), mid - 0.1, mid + 0.1, 1e6, 1e6, t, t)
        })
        .collect();
    let router = api_router_with(
        state()
            .with_tick_reader(Arc::new(InMemoryTickReader::new().with_quotes(quotes)))
            .with_journals_dir(&scratch.journals(), RunServiceConfig::default())
            .unwrap(),
    );
    let done = run_to_end(&router).await;
    assert_eq!(done["data"]["status"], "completed", "{done}");
}
