//! Runs the `honba` binary end to end: config file -> strategy -> engine ->
//! simulated fills -> analytics -> Markdown report.
//!
//! Hermetic: the backtest uses the CLI's built-in synthetic series, files go
//! under Cargo's per-target scratch directory, and nothing touches the
//! network or Python.

use std::fs;
use std::path::{Path, PathBuf};
use std::process::{Command, Output};

fn honba(args: &[&str]) -> Output {
    Command::new(env!("CARGO_BIN_EXE_honba"))
        .args(args)
        .output()
        .expect("failed to spawn the honba binary")
}

/// A fresh scratch directory for one test.
fn scratch(name: &str) -> PathBuf {
    let dir = PathBuf::from(env!("CARGO_TARGET_TMPDIR"))
        .join("honba-cli")
        .join(name);
    let _ = fs::remove_dir_all(&dir);
    fs::create_dir_all(&dir).unwrap();
    dir
}

fn write_config(dir: &Path, body: &str) -> String {
    let path = dir.join("backtest.toml");
    fs::write(&path, body).unwrap();
    path.to_str().unwrap().to_owned()
}

fn stdout(o: &Output) -> String {
    String::from_utf8_lossy(&o.stdout).into_owned()
}

fn stderr(o: &Output) -> String {
    String::from_utf8_lossy(&o.stderr).into_owned()
}

#[test]
fn backtest_prints_a_markdown_report() {
    let dir = scratch("stdout");
    let cfg = write_config(&dir, "[strategy]\nname = \"sma_crossover\"\n");
    let out = honba(&["backtest", &cfg]);
    assert!(out.status.success(), "{}", stderr(&out));
    let report = stdout(&out);
    assert!(report.starts_with("# Performance Report"), "{report}");
    assert!(report.contains("## Trades") && report.contains("## Equity"));
    assert!(report.contains("| Trades | 2 |"), "{report}");
}

#[test]
fn backtest_writes_the_report_to_the_output_file_deterministically() {
    let dir = scratch("output");
    let cfg = write_config(
        &dir,
        "[strategy]\nname = \"sma_crossover\"\nfast = 3\nslow = 8\ntrade_size = 10.0\n",
    );
    let path = |n: &str| dir.join(n).to_str().unwrap().to_owned();

    let first = honba(&["backtest", &cfg, "--output", &path("a.md")]);
    assert!(first.status.success(), "{}", stderr(&first));
    assert!(stdout(&first).is_empty());
    let second = honba(&["backtest", &cfg, "-o", &path("b.md")]);
    assert!(second.status.success(), "{}", stderr(&second));

    let a = fs::read_to_string(path("a.md")).unwrap();
    assert!(a.starts_with("# Performance Report"), "{a}");
    assert_eq!(a, fs::read_to_string(path("b.md")).unwrap());
    // Same defaults as the explicit parameters above.
    let defaults = write_config(
        &scratch("defaults"),
        "[strategy]\nname = \"sma_crossover\"\n",
    );
    assert_eq!(stdout(&honba(&["backtest", &defaults])), a);
}

#[test]
fn backtest_rejects_an_unknown_strategy() {
    let dir = scratch("unknown");
    let cfg = write_config(&dir, "[strategy]\nname = \"nope\"\n");
    let out = honba(&["backtest", &cfg]);
    assert!(!out.status.success());
    assert!(
        stderr(&out).contains("unknown strategy: nope"),
        "{}",
        stderr(&out)
    );
}

#[test]
fn backtest_reports_a_missing_config_file() {
    let dir = scratch("missing");
    let cfg = dir.join("absent.toml");
    let out = honba(&["backtest", cfg.to_str().unwrap()]);
    assert!(!out.status.success());
    assert!(stderr(&out).contains("reading config"), "{}", stderr(&out));
}

#[test]
fn data_load_rejects_unsupported_sources() {
    let out = honba(&["data", "load", "bars.csv", "TCS"]);
    assert!(!out.status.success());
    assert!(
        stderr(&out).contains("unsupported source extension"),
        "{}",
        stderr(&out)
    );
}

#[test]
fn calendars_show_echoes_the_year() {
    let out = honba(&["calendars", "show", "--year", "2025"]);
    assert!(out.status.success(), "{}", stderr(&out));
    assert_eq!(stdout(&out), "Trading calendar for year 2025\n");
}

#[test]
fn missing_subcommand_is_a_usage_error() {
    let out = honba(&[]);
    assert_eq!(out.status.code(), Some(2));
    assert!(stderr(&out).contains("Usage"), "{}", stderr(&out));
}
