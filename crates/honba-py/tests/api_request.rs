//! `honba::pyclasses::api::request` end to end over the real REST router, without a Python
//! interpreter or a socket: statuses and envelopes match what `honba serve` answers.

use std::path::PathBuf;

use honba::pyclasses::api::request;
use serde_json::{json, Value};

fn data_dir() -> String {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR")).join("honba-py-api-request");
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    dir.to_str().unwrap().to_owned()
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
    let dir = data_dir();
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
    let dir = data_dir();
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
    let (status, body) = call(&dir, "POST", "/backtests", None, Some("{}"));
    assert_eq!(
        (status, &body["error"]["code"]),
        (501, &json!("not_implemented"))
    );
}

#[test]
fn a_post_body_reaches_the_handler() {
    let dir = data_dir();
    let (status, body) = call(&dir, "POST", "/strategies/verify", None, Some("{}"));
    assert_eq!(status, 422);
    assert_eq!(body["error"]["code"], "validation_invalid_request");
}
