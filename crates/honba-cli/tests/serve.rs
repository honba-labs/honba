//! `honba serve` end to end: the real binary over a Parquet data directory,
//! bound to an ephemeral loopback port, called with a plain TCP client and
//! stopped with SIGINT. Hermetic: loopback only, files under Cargo's scratch dir.
#![cfg(unix)]

use std::fs::File;
use std::io::{BufRead, BufReader, Read, Write};
use std::net::{SocketAddr, TcpStream};
use std::path::PathBuf;
use std::process::{Child, Command, Stdio};
use std::sync::Arc;

use arrow::array::{ArrayRef, Float64Array, Int64Array};
use arrow::record_batch::RecordBatch;
use honba_data::import::parquet_source::bar_schema;
use parquet::arrow::ArrowWriter;
use serde_json::Value;

fn data_dir(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("honba-cli-serve")
        .join(name);
    let _ = std::fs::remove_dir_all(&dir);
    std::fs::create_dir_all(&dir).unwrap();
    let schema = Arc::new(bar_schema());
    let col = |v: f64| -> ArrayRef { Arc::new(Float64Array::from(vec![v; 2])) };
    let columns: Vec<ArrayRef> = vec![
        Arc::new(Int64Array::from(vec![60_000_000_000_i64, 120_000_000_000])),
        col(10.0),
        col(12.0),
        col(9.0),
        col(11.0),
        col(100.0),
    ];
    let batch = RecordBatch::try_new(schema.clone(), columns).unwrap();
    let file = File::create(dir.join("TCS.NSE.parquet")).unwrap();
    let mut writer = ArrowWriter::try_new(file, schema, None).unwrap();
    writer.write(&batch).unwrap();
    writer.close().unwrap();
    dir
}

/// Starts `honba serve` and returns the child plus the address it announced.
fn start(args: &[&str]) -> (Child, SocketAddr) {
    let mut child = Command::new(env!("CARGO_BIN_EXE_honba"))
        .arg("serve")
        .args(args)
        .stdout(Stdio::piped())
        .stderr(Stdio::piped())
        .spawn()
        .unwrap();
    let mut line = String::new();
    BufReader::new(child.stdout.as_mut().unwrap())
        .read_line(&mut line)
        .unwrap();
    let addr = line
        .trim()
        .strip_prefix("listening on http://")
        .unwrap_or_else(|| panic!("unexpected banner {line:?}"))
        .parse()
        .unwrap();
    (child, addr)
}

fn get(addr: SocketAddr, path: &str) -> (u16, Value) {
    let mut stream = TcpStream::connect(addr).unwrap();
    write!(stream, "GET {path} HTTP/1.0\r\nHost: {addr}\r\n\r\n").unwrap();
    let mut text = String::new();
    stream.read_to_string(&mut text).unwrap();
    let (head, body) = text.split_once("\r\n\r\n").unwrap();
    let status = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, serde_json::from_str(body).unwrap_or(Value::Null))
}

#[test]
fn serve_answers_health_and_instruments_then_exits_cleanly_on_sigint() {
    let dir = data_dir("ok");
    let (mut child, addr) = start(&["--data-dir", dir.to_str().unwrap(), "--addr", "127.0.0.1:0"]);

    let (status, body) = get(addr, "/health");
    assert_eq!((status, &body["data"]["status"]), (200, &Value::from("ok")));
    let (status, body) = get(addr, "/instruments");
    assert_eq!(status, 200);
    assert_eq!(body["data"]["instruments"][0]["id"]["symbol"], "TCS");
    let (status, body) = get(addr, "/quotes?symbols=TCS");
    assert_eq!(status, 200);
    assert_eq!(body["data"]["quotes"][0]["bid_price"], 11.0);

    let sent = Command::new("kill")
        .args(["-INT", &child.id().to_string()])
        .status()
        .unwrap();
    assert!(sent.success());
    let exit = child.wait().unwrap();
    assert!(exit.success(), "graceful shutdown exits 0, got {exit:?}");
}

#[test]
fn a_missing_data_dir_fails_before_listening() {
    let output = Command::new(env!("CARGO_BIN_EXE_honba"))
        .args(["serve", "--data-dir", "/nonexistent/honba-bars"])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("data directory"));
}

/// Sends `GET /health` with an `Origin` header and returns the raw response head.
fn head_with_origin(addr: SocketAddr, origin: &str) -> String {
    let mut stream = TcpStream::connect(addr).unwrap();
    write!(
        stream,
        "GET /health HTTP/1.0\r\nHost: {addr}\r\nOrigin: {origin}\r\n\r\n"
    )
    .unwrap();
    let mut text = String::new();
    stream.read_to_string(&mut text).unwrap();
    text.split_once("\r\n\r\n").unwrap().0.to_ascii_lowercase()
}

fn stop(mut child: Child) {
    Command::new("kill")
        .args(["-INT", &child.id().to_string()])
        .status()
        .unwrap();
    child.wait().unwrap();
}

#[test]
fn cors_is_off_by_default_and_opt_in_per_origin() {
    let dir = data_dir("cors");
    let d = dir.to_str().unwrap();
    let (child, addr) = start(&["--data-dir", d, "--addr", "127.0.0.1:0"]);
    let head = head_with_origin(addr, "https://app.example");
    assert!(!head.contains("access-control-allow-origin"), "{head}");
    stop(child);

    let (child, addr) = start(&[
        "--data-dir",
        d,
        "--addr",
        "127.0.0.1:0",
        "--cors-origin",
        "https://app.example",
    ]);
    let head = head_with_origin(addr, "https://app.example");
    assert!(
        head.contains("access-control-allow-origin: https://app.example"),
        "{head}"
    );
    let head = head_with_origin(addr, "https://evil.example");
    assert!(!head.contains("access-control-allow-origin"), "{head}");
    stop(child);
}

#[test]
fn an_invalid_cors_origin_fails_before_listening() {
    let dir = data_dir("badcors");
    let output = Command::new(env!("CARGO_BIN_EXE_honba"))
        .args([
            "serve",
            "--data-dir",
            dir.to_str().unwrap(),
            "--cors-origin",
            "bad\norigin",
        ])
        .output()
        .unwrap();
    assert!(!output.status.success());
    assert!(output.stdout.is_empty());
    assert!(String::from_utf8_lossy(&output.stderr).contains("cors"));
}

#[test]
fn a_non_loopback_address_warns_on_stderr_and_loopback_does_not() {
    let dir = data_dir("warn");
    let d = dir.to_str().unwrap();
    let (mut child, _) = start(&["--data-dir", d, "--addr", "0.0.0.0:0"]);
    // The banner is printed after the warning, so the warning is already written.
    Command::new("kill")
        .args(["-INT", &child.id().to_string()])
        .status()
        .unwrap();
    child.wait().unwrap();
    let mut err = String::new();
    child
        .stderr
        .take()
        .unwrap()
        .read_to_string(&mut err)
        .unwrap();
    assert!(
        err.contains("warning") && err.contains("auth") && err.contains("TLS"),
        "{err}"
    );

    let (mut child, _) = start(&["--data-dir", d, "--addr", "127.0.0.1:0"]);
    Command::new("kill")
        .args(["-INT", &child.id().to_string()])
        .status()
        .unwrap();
    child.wait().unwrap();
    let mut err = String::new();
    child
        .stderr
        .take()
        .unwrap()
        .read_to_string(&mut err)
        .unwrap();
    assert!(!err.contains("warning"), "{err}");
}

fn post(addr: SocketAddr, path: &str, body: &str) -> (u16, Value) {
    let mut stream = TcpStream::connect(addr).unwrap();
    write!(
        stream,
        "POST {path} HTTP/1.0\r\nHost: {addr}\r\nContent-Type: application/json\r\n\
         Content-Length: {}\r\n\r\n{body}",
        body.len()
    )
    .unwrap();
    let mut text = String::new();
    stream.read_to_string(&mut text).unwrap();
    let (head, body) = text.split_once("\r\n\r\n").unwrap();
    let status = head.split_whitespace().nth(1).unwrap().parse().unwrap();
    (status, serde_json::from_str(body).unwrap_or(Value::Null))
}

#[test]
fn serve_runs_backtests_into_the_journals_dir_and_stops_with_no_open_run() {
    let dir = data_dir("runs");
    let journals = dir.join("journals");
    let (mut child, addr) = start(&[
        "--data-dir",
        dir.to_str().unwrap(),
        "--addr",
        "127.0.0.1:0",
        "--journals-dir",
        journals.to_str().unwrap(),
        "--max-concurrent-runs",
        "1",
        "--shutdown-grace-secs",
        "0",
    ]);

    let (status, body) = get(addr, "/capabilities");
    assert_eq!(status, 200);
    let flagged = body["data"]["capabilities"]["not_implemented"].to_string();
    assert!(!flagged.contains("/backtests"), "{flagged}");
    assert!(!flagged.contains("/journals"), "{flagged}");
    let (status, body) = post(addr, "/backtests", "{}");
    assert_eq!(status, 422, "{body}");
    let (status, body) = get(addr, "/backtests/01ARZ3NDEKTSV4RRFFQ69G5FAV");
    assert_eq!(status, 404, "{body}");
    assert!(!journals.exists(), "rejected requests must write nothing");

    let (status, body) = post(
        addr,
        "/backtests",
        r#"{"strategy":"buy_and_hold","universe":"TCS.NSE","start":"1970-01-01","end":"1970-01-02","bar_spec":"1m","seed":1}"#,
    );
    assert_eq!(status, 200, "{body}");
    let id = body["data"]["run_id"].as_str().unwrap().to_owned();
    let manifest = journals.join(&id).join("manifest.json");
    assert!(
        manifest.exists(),
        "the run is journaled under --journals-dir"
    );

    Command::new("kill")
        .args(["-INT", &child.id().to_string()])
        .status()
        .unwrap();
    assert!(child.wait().unwrap().success());
    // Graceful shutdown leaves no pending or running manifest behind (decision 5).
    let text = std::fs::read_to_string(manifest).unwrap();
    let status = serde_json::from_str::<Value>(&text).unwrap()["status"]
        .as_str()
        .unwrap()
        .to_owned();
    assert!(
        ["completed", "failed", "cancelled"].contains(&status.as_str()),
        "left {status}"
    );
}

#[test]
fn trading_state_gates_post_orders() {
    let dir = data_dir("trading-state");
    let d = dir.to_str().unwrap();
    let order = r#"{"instrument_id":{"symbol":"TCS","exchange":"NSE"},"side":"buy","qty":1.0}"#;

    // Default state is active: an order passes the risk stage and is acknowledged.
    let (child, addr) = start(&["--data-dir", d, "--addr", "127.0.0.1:0"]);
    let (status, body) = post(addr, "/orders", order);
    assert_eq!(status, 200, "{body}");
    assert_eq!(body["data"]["status"], Value::from("acknowledged"));
    stop(child);

    // Halted: the same order is refused by the state rule before any shape rule.
    let (child, addr) = start(&[
        "--data-dir",
        d,
        "--addr",
        "127.0.0.1:0",
        "--trading-state",
        "halted",
    ]);
    let (status, body) = post(addr, "/orders", order);
    assert_eq!(status, 422, "{body}");
    assert_eq!(body["error"]["code"], Value::from("risk_trading_halted"));
    assert_eq!(
        body["error"]["context"]["rule"],
        Value::from("trading_halted")
    );
    stop(child);
}
