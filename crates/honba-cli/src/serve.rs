//! `honba serve` – the REST API over a directory of Parquet bar files, with backtest runs
//! journaled under `--journals-dir`.
//!
//! The data is loaded once at start-up (a bad directory fails before anything
//! listens), then `honba-api-rest` serves it until ctrl-c. The first stdout line
//! is `listening on http://<addr>` with the address actually bound, so
//! `--addr 127.0.0.1:0` is usable by scripts and tests. Handlers read no clock.
//!
//! With `--bar-ingest`, `POST /bars` feeds a bounded queue drained by the bar pump (fills and
//! quotes follow). Shutdown drops the sender, waits for the pump to drain, and reports its
//! counts on stderr.

use anyhow::{Context, Result};
use std::io::Write;
use std::net::SocketAddr;
use std::path::{Path, PathBuf};

use std::time::Duration;

use honba_api_rest::{ApiConfig, AppState, RetentionPolicy, RunServiceConfig};
use honba_messages::TradingState;

/// Parses the `--trading-state` value; the spellings are the wire form (ADR 0018 decision 2).
pub(crate) fn parse_trading_state(raw: &str) -> Result<TradingState, String> {
    match raw {
        "active" => Ok(TradingState::Active),
        "reducing" => Ok(TradingState::Reducing),
        "halted" => Ok(TradingState::Halted),
        other => Err(format!(
            "unknown trading state {other:?}; expected active, reducing or halted"
        )),
    }
}

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

/// Auth configuration (ADR 0023, E11-S8).
#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub(crate) struct AuthArgs {
    /// Path to tokens.toml file. When provided, Bearer token auth is enabled.
    /// The file is hot-reloaded on changes.
    #[arg(long)]
    pub tokens_file: Option<PathBuf>,
    /// Path to audit log file. If provided, auth decisions are logged here (NDJSON).
    /// Defaults to `audit.log` in the journals directory if `--tokens-file` is set.
    #[arg(long)]
    pub audit_log_file: Option<PathBuf>,
}

/// The live bar feed behind `POST /bars` (E3-S7 follow-up). Off by default: with it off the
/// route answers 503 `bar_ingest_disabled`.
#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub(crate) struct IngestArgs {
    /// Accept bars on POST /bars and feed them to the simulated market (fills, quotes)
    #[arg(long)]
    pub bar_ingest: bool,
    /// Bars the ingest queue holds before POST /bars answers 429
    #[arg(long, default_value_t = 1_024, value_parser = clap::value_parser!(u64).range(1..))]
    pub bar_ingest_capacity: u64,
}

/// Pre-trade risk limits for the write routes (ADR 0018 decision 8, collar addendum).
#[derive(clap::Args, Debug, Clone, PartialEq, Eq)]
pub(crate) struct RiskArgs {
    /// TOML file with a `[risk]` table, the same shape as a backtest config's `[risk]`
    /// (max_notional, max_participation, stale_after_ms, price_collar_bps, [risk.order_rate]).
    /// Default: no limits
    #[arg(long)]
    pub risk_config: Option<PathBuf>,
}

/// The file shape behind `--risk-config`: the `[risk]` table of `BacktestRunConfig`.
#[derive(serde::Deserialize)]
#[serde(deny_unknown_fields)]
struct RiskFile {
    #[serde(default)]
    risk: honba_risk::RiskLimits,
}

/// Reads and validates a `--risk-config` file. Errors carry the path and name the field.
pub(crate) fn load_risk_limits(path: &Path) -> Result<honba_risk::RiskLimits> {
    let text = std::fs::read_to_string(path)
        .with_context(|| format!("reading --risk-config {}", path.display()))?;
    let file: RiskFile = toml::from_str(&text)
        .with_context(|| format!("parsing --risk-config {}", path.display()))?;
    file.risk
        .validate()
        .with_context(|| format!("invalid [risk] in --risk-config {}", path.display()))?;
    Ok(file.risk)
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

#[allow(clippy::too_many_arguments)] // one parameter per flag group of `honba serve`
pub fn run(
    data_dir: &Path,
    addr: SocketAddr,
    cors_origins: &[String],
    trading_state: TradingState,
    runs: &RunsArgs,
    auth: &AuthArgs,
    ingest: &IngestArgs,
    risk: &RiskArgs,
) -> Result<()> {
    let risk_limits = match &risk.risk_config {
        Some(path) => load_risk_limits(path)?,
        None => honba_risk::RiskLimits::default(),
    };
    let mut config = ApiConfig::default()
        .with_cors_origins(cors_origins)
        .map_err(|e| anyhow::anyhow!(e))
        .context("invalid --cors-origin (cors)")?;

    if let Some(tokens_file) = &auth.tokens_file {
        config.auth.tokens_file = Some(tokens_file.clone());
        if let Some(audit_log_file) = &auth.audit_log_file {
            config.auth.audit_log_path = Some(audit_log_file.clone());
        } else if auth.tokens_file.is_some() {
            // Default audit log next to journals dir
            config.auth.audit_log_path = Some(runs.journals_dir.join("audit.log"));
        }
    }

    let state = AppState::from_parquet_dir(data_dir)
        .with_context(|| format!("loading data directory {}", data_dir.display()))?
        .with_trading_state(trading_state)
        .with_risk_limits(risk_limits)
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
        // Only warn about non-loopback if auth is NOT enabled
        if auth.tokens_file.is_none() {
            if let Some(warning) = non_loopback_warning(addr) {
                eprintln!("{warning}");
            }
        }
        // Started inside the runtime, before the first request can arrive.
        let pump = ingest
            .bar_ingest
            .then(|| state.start_bar_ingest(clamp_usize(ingest.bar_ingest_capacity)));
        println!("listening on http://{}", listener.local_addr()?);
        std::io::stdout().flush()?;
        let served =
            honba_api_rest::serve_with_config(listener, state.clone(), &config, stop_signal())
                .await
                .context("serving");
        // Drop the sender so the pump drains what is queued and ends, then report.
        state.stop_bar_ingest();
        if let Some(pump) = pump {
            match pump.await {
                Ok(report) => {
                    eprintln!("bar ingest: {} bars, {} errors", report.bars, report.errors)
                }
                Err(e) => eprintln!("bar ingest: pump task failed: {e}"),
            }
        }
        served
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
