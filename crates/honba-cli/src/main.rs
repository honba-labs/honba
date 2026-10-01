mod backtest;
mod calendars;
mod data;
#[cfg(test)]
mod tests;

use anyhow::Result;
use clap::{Parser, Subcommand};
use std::path::PathBuf;

#[derive(Parser)]
#[command(name = "honba", version, about = "Honba trading/simulation CLI")]
struct Cli {
    #[command(subcommand)]
    command: Commands,
}

#[derive(Subcommand)]
enum Commands {
    /// Run a Rust-native backtest from a TOML config
    Backtest {
        config: PathBuf,
        #[arg(short, long)]
        output: Option<PathBuf>,
    },
    /// Calendar utilities
    Calendars {
        #[command(subcommand)]
        command: CalendarCommands,
    },
    /// Import or inspect market data
    Data {
        #[command(subcommand)]
        command: DataCommands,
    },
}

#[derive(Subcommand)]
enum CalendarCommands {
    Show {
        #[arg(long)]
        year: i32,
    },
}

#[derive(Subcommand)]
enum DataCommands {
    /// Load a data source and print a summary
    Load { source: String, symbol: String },
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Backtest { config, output } => backtest::run(&config, output.as_deref()),
        Commands::Data { command } => match command {
            DataCommands::Load { source, symbol } => data::load(&source, &symbol),
        },
        Commands::Calendars { command } => match command {
            CalendarCommands::Show { year } => calendars::show(year),
        },
    }
}
