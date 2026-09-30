mod py;

use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "honba", version, about = "Honba simulation CLI")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Execute a honba-style Python script
    Run {
        /// Path to the Python script
        script: PathBuf,
        /// Optional cap on dispatched events
        #[arg(long)]
        max_events: Option<u64>,
    },
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Run { script, max_events } => py::run_script(&script, max_events),
    }
}
