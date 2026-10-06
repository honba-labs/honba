//! `honba serve` – the read-only REST API over a directory of Parquet bar files.
//!
//! The data is loaded once at start-up (a bad directory fails before anything
//! listens), then `honba-api-rest` serves it until ctrl-c. The first stdout line
//! is `listening on http://<addr>` with the address actually bound, so
//! `--addr 127.0.0.1:0` is usable by scripts and tests. Handlers read no clock.

use anyhow::{Context, Result};
use std::io::Write;
use std::net::SocketAddr;
use std::path::Path;

use honba_api_rest::AppState;

pub fn run(data_dir: &Path, addr: SocketAddr) -> Result<()> {
    let state = AppState::from_parquet_dir(data_dir)
        .with_context(|| format!("loading data directory {}", data_dir.display()))?;
    let runtime = tokio::runtime::Builder::new_multi_thread()
        .enable_all()
        .build()
        .context("starting the async runtime")?;
    runtime.block_on(async {
        let listener = tokio::net::TcpListener::bind(addr)
            .await
            .with_context(|| format!("binding {addr}"))?;
        println!("listening on http://{}", listener.local_addr()?);
        std::io::stdout().flush()?;
        honba_api_rest::serve(listener, state, async {
            let _ = tokio::signal::ctrl_c().await;
        })
        .await
        .context("serving")
    })
}
