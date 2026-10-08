//! `honba serve` – the REST API over a directory of Parquet bar files, with backtest runs
//! journaled under `--journals-dir`.
//!
//! The data is loaded once at start-up (a bad directory fails before anything
//! listens), then `honba-api-rest` serves it until ctrl-c. The first stdout line
//! is `listening on http://<addr>` with the address actually bound, so
//! `--addr 127.0.0.1:0` is usable by scripts and tests. Handlers read no clock.

use anyhow::{Context, Result};
use std::io::Write;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};

use std::time::Duration;

use honba_api_rest::{ApiConfig, AppState, RetentionPolicy, RunServiceConfig};

/// The stderr warning for an `--addr` that is not loopback, or `None` when it is.
pub(crate) fn non_loopback_warning(addr: SocketAddr) -> Option<String> {
    if addr.ip().is_loopback() {
        return None;
    }
    Some(format!(
        "warning: listening on {addr}, which is not loopback; the API has no auth or TLS, \
         so anyone who can reach it can read the data"
    ))
}

/// Where runs live and how many may run (ADR 0017 decisions 3 to 5).
#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub(crate) struct RunsArgs {
    /// Directory holding one sub-directory per run (manifest.json + events.ndjson)
    #[arg(long, default_value = "data/journals")]
    pub journals_dir: PathBuf,
    /// Worker threads, one backtest each (default: available parallelism)
    #[arg(long, value_parser = clap::value_parser!(u64).range(1..))]
    pub max_concurrent_runs: Option<u64>,
    /// Most pending runs the queue holds before POST /backtests answers 429
    #[arg(long, alias = "max-queued", default_value_t = 64, value_parser = clap::value_parser!(u64).range(1..))]
    pub max_queued_runs: u64,
    /// Seconds a stopping server waits for running runs before cancelling them
    #[arg(long, default_value_t = 10)]
    pub shutdown_grace_secs: u64,
    /// Finished runs that always survive start-up retention
    #[arg(long, default_value_t = 1_000)]
    pub keep_runs: u64,
    /// Finished runs younger than this many days survive start-up retention
    #[arg(long, default_value_t = 30)]
    pub keep_days: u64,
}

impl RunsArgs {
    /// The run-service tunables these flags spell.
    pub(crate) fn service_config(&self) -> RunServiceConfig {
        let defaults = RunServiceConfig::default();
        RunServiceConfig {
            max_concurrent: self
                .max_concurrent_runs
                .map_or(defaults.max_concurrent, clamp_usize),
            max_queued: clamp_usize(self.max_queued_runs),
            shutdown_grace: Duration::from_secs(self.shutdown_grace_secs),
            retention: RetentionPolicy {
                keep_runs: clamp_usize(self.keep_runs),
                keep_days: self.keep_days,
            },
        }
    }
}

fn clamp_usize(n: u64) -> usize {
    usize::try_from(n).unwrap_or(usize::MAX)
}

pub fn run(
    data_dir: &Path,
    addr: SocketAddr,
    cors_origins: &[String],
    runs: &RunsArgs,
) -> Result<()> {
    let config = ApiConfig::default()
        .with_cors_origins(cors_origins)
        .map_err(|e| anyhow::anyhow!(e))
        .context("invalid --cors-origin (cors)")?;
    let state = AppState::from_parquet_dir(data_dir)
        .with_context(|| format!("loading data directory {}", data_dir.display()))?
        .with_journals_dir(&runs.journals_dir, runs.service_config())
        .map_err(|e| anyhow::anyhow!("{}", e.message))
        .with_context(|| format!("opening journals directory {}", runs.journals_dir.display()))?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .context("starting the async runtime")?;
    let served = runtime.block_on(async {
        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .with_context(|| format!("binding {addr}"))?;
        if let Some(warning) = non_loopback_warning(addr) {
            eprintln!("{warning}");
        }
        println!("listening on http://{}", listener.local_addr()?);
        std::io::stdout().flush()?;
        honba_api_rest::serve_with_config(listener, state.clone(), &config, stop_signal())
            .await
            .context("serving")
    });
    // Graceful run shutdown (ADR 0017 decision 5): cancel pending runs, wait the grace
    // period for running ones, cancel the rest. Blocking, so after the runtime stopped.
    if let Some(report) = state.shutdown_runs() {
        if report.cancelled_pending + report.cancelled_running > 0 {
            eprintln!(
                "cancelled {} pending and {} running runs",
                report.cancelled_pending, report.cancelled_running
            );
        }
    }
    served
}

/// Resolves on ctrl-c (SIGINT) or, on unix, SIGTERM.
async fn stop_signal() {
    #[cfg(unix)]
    {
        use tokio::signal::unix::{signal, SignalKind};
        match signal(SignalKind::terminate()) {
            Ok(mut term) => tokio::select! {
                _ = tokio::signal::ctrl_c() => {}
                _ = term.recv() => {}
            },
            Err(_) => {
                let _ = tokio::signal::ctrl_c().await;
            }
        }
    }
    #[cfg(not(unix))]
    {
        let _ = tokio::signal::ctrl_c().await;
    }
}
