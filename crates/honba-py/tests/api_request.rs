//! `honba::pyclasses::api::request` end to end over the real REST router, without a Python
//! interpreter or a socket: statuses and envelopes match what `honba serve` answers.

use std::path::PathBuf;

use honba::pyclasses::api::{request, request_with, ApiRequestError, RunsOptions};
use serde_json::{json, Value};

/// A fresh data directory owned by one test. Each test passes its own `label`, so tests running in
/// parallel never remove or reuse each other's directory.
fn data_dir(label: &str) -> String {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("honba-py-api-request")
        .join(label);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.to_str().unwrap().to_owned()
}

#[test]
fn data_dirs_of_different_tests_do_not_collide() {
    let first = data_dir("collide_first");
    let marker = PathBuf::from(&first).join("marker");
    std::fs::write(&marker, "x").unwrap();
    let second = data_dir("collide_second");
    assert_ne!(first, second);
    assert!(
        marker.exists(),
        "creating another test's directory removed this one's files"
    );
}

fn call(
    dir: &str,
    method: &str,
    path: &str,
    query: Option<&str>,
    body: Option<&str>,
) -> (u16, Value) {
    let (status, text) = request(dir, method, path, query, body).unwrap();
    (status, serde_json::from_str(&text).unwrap_or(Value::Null))
}

#[test]
fn reads_answer_inside_the_envelope() {
    let dir = data_dir("reads_answer_inside_the_envelope");
    let (status, body) = call(&dir, "GET", "/health", None, None);
    assert_eq!((status, &body["data"]["status"]), (200, &json!("ok")));
    let (status, body) = call(
        &dir,
        "GET",
        "/instruments",
        Some(r#"{"exchange": "NSE"}"#),
        None,
    );
    assert_eq!(status, 200);
    assert_eq!(body["data"]["instruments"], json!([]));
}

#[test]
fn error_statuses_and_codes_match_the_served_api() {
    let dir = data_dir("error_statuses_and_codes_match_the_served_api");
    let (status, body) = call(&dir, "GET", "/instruments/TCS.NSE", None, None);
    assert_eq!(
        (status, &body["error"]["code"]),
        (404, &json!("instrument_not_found"))
    );
    let (status, body) = call(&dir, "GET", "/bars/TCS.NSE", Some(r#"{"tf": "x"}"#), None);
    assert_eq!(
        (status, &body["error"]["code"]),
        (422, &json!("validation_invalid_request"))
    );
    // POST /backtests is built (ADR 0017): an empty body fails resolution.
    let (status, body) = call(&dir, "POST", "/backtests", None, Some("{}"));
    assert_eq!(
        (status, &body["error"]["code"]),
        (422, &json!("validation_invalid_request"))
    );
    // Sweeps stay unbuilt until E4-S5.
    let (status, body) = call(&dir, "POST", "/sweeps", None, Some("{}"));
    assert_eq!(
        (status, &body["error"]["code"]),
        (501, &json!("not_implemented"))
    );
}

#[test]
fn a_post_body_reaches_the_handler() {
    let dir = data_dir("a_post_body_reaches_the_handler");
    let (status, body) = call(&dir, "POST", "/strategies/verify", None, Some("{}"));
    assert_eq!(status, 422);
    assert_eq!(body["error"]["code"], "validation_invalid_request");
}

const SUBMIT: &str = r#"{"strategy":"buy_and_hold","universe":"TCS.NSE","start":"2024-01-01","end":"2024-02-01","seed":7}"#;

fn journals(label: &str) -> String {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("honba-py-api-request-journals")
        .join(label);
    let _ = std::fs::remove_dir_all(&dir);
    dir.to_str().unwrap().to_owned()
}

#[test]
fn without_a_journals_dir_a_valid_submit_is_503_unsupported() {
    let dir = data_dir("runs_off");
    let (status, body) = call(&dir, "POST", "/backtests", None, Some(SUBMIT));
    assert_eq!(status, 503, "{body}");
    assert_eq!(body["error"]["context"]["reason"], "no_journals_dir");
}

#[test]
fn with_a_journals_dir_a_valid_submit_is_accepted_and_pollable() {
    let dir = data_dir("runs_on");
    let runs = RunsOptions {
        journals_dir: Some(journals("runs_on")),
        max_concurrent: Some(1),
        max_queued: Some(4),
    };
    let post = |method: &str, path: &str, body: Option<&str>| {
        let (status, text) = request_with(&dir, &runs, method, path, None, body).unwrap();
        (status, serde_json::from_str::<Value>(&text).unwrap())
    };
    let (status, body) = post("POST", "/backtests", Some(SUBMIT));
    assert_eq!(status, 200, "{body}");
    let id = body["data"]["run_id"].as_str().unwrap().to_owned();
    assert_eq!(body["data"]["status"], "pending");
    // No bars for TCS.NSE in the empty directory: the run reaches a terminal state, failed.
    let mut last = Value::Null;
    for _ in 0..2000 {
        let (status, got) = post("GET", &format!("/backtests/{id}"), None);
        assert_eq!(status, 200, "{got}");
        last = got;
        if matches!(
            last["data"]["status"].as_str(),
            Some("completed" | "failed")
        ) {
            break;
        }
        std::thread::sleep(std::time::Duration::from_millis(5));
    }
    let status = last["data"]["status"].as_str().unwrap_or("?");
    assert!(matches!(status, "completed" | "failed"), "{last}");
}

#[test]
fn an_uncreatable_journals_dir_is_an_error_not_a_503() {
    let dir = data_dir("runs_bad_root");
    let blocker = PathBuf::from(&dir).join("file");
    std::fs::write(&blocker, "x").unwrap();
    let runs = RunsOptions {
        journals_dir: Some(blocker.join("sub").to_str().unwrap().to_owned()),
        ..RunsOptions::default()
    };
    let err = request_with(&dir, &runs, "GET", "/health", None, None).unwrap_err();
    assert!(matches!(err, ApiRequestError::Runs(_)), "{err}");
}
