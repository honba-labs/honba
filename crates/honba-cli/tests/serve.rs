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
