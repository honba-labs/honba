mod backtest;
mod calendars;
mod data;
mod schema;
mod serve;
#[cfg(test)]
mod tests;
mod verify;

use anyhow::Result;
use clap::{Parser, Subcommand};
use honba_messages::TradingState;
use std::net::SocketAddr;
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
    /// Codegen: emit domain_schema.json / TypeScript / OpenAPI / Python .pyi / MCP (Rust is source of truth)
    Schema {
        #[command(subcommand)]
        command: SchemaCommands,
    },
    /// Emit every artifact (alias)
    Codegen {
        #[arg(long, default_value = "schema/domain")]
        schema_dir: PathBuf,
        #[arg(long)]
        typescript: Option<PathBuf>,
        #[arg(long)]
        openapi: Option<PathBuf>,
        #[arg(long)]
        pyi: Option<PathBuf>,
        #[arg(long)]
        mcp: Option<PathBuf>,
    },
    /// Serve the read-only REST API over a directory of SYMBOL.EXCHANGE.parquet bar files
    Serve {
        /// Directory holding the Parquet bar files
        #[arg(long)]
        data_dir: PathBuf,
        /// Address to listen on
        #[arg(long, default_value = "127.0.0.1:8080")]
        addr: SocketAddr,
        /// Allow this browser origin to read the API (CORS); repeatable. Default: none
        #[arg(long = "cors-origin")]
        cors_origin: Vec<String>,
        /// Operator trading state the write routes enforce: active, reducing or halted
        #[arg(long, default_value = "active", value_parser = serve::parse_trading_state)]
        trading_state: TradingState,
        #[command(flatten)]
        runs: serve::RunsArgs,
    },
    /// Verify a strategy manifest and print its compiled IR as JSON
    Verify {
        /// Path to the manifest file (JSON)
        manifest: PathBuf,
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
    Load {
        source: String,
        /// Bare symbol (`INFY`) or qualified `NSE:INFY` / `BSE:INFY`
        symbol: String,
        /// Exchange for a bare symbol: NSE (default) or BSE
        #[arg(long)]
        exchange: Option<String>,
    },
}

#[derive(Subcommand)]
enum SchemaCommands {
    /// Emit domain_schema.json, and optionally the derived artifacts
    Export {
        /// Domain schema output dir (default: ./schema/domain, relative to the working directory)
        #[arg(long)]
        output: Option<PathBuf>,
        /// Also render TypeScript into DIR (frontend)
        #[arg(long)]
        typescript: Option<PathBuf>,
        /// Also render OpenAPI into DIR
        #[arg(long)]
        openapi: Option<PathBuf>,
        /// Also render Python .pyi into DIR (wheel)
        #[arg(long)]
        pyi: Option<PathBuf>,
        /// Also render MCP tool schemas into DIR
        #[arg(long)]
        mcp: Option<PathBuf>,
    },
}

fn main() -> Result<()> {
    let cli = Cli::parse();
    match cli.command {
        Commands::Backtest { config, output } => backtest::run(&config, output.as_deref()),
        Commands::Data { command } => match command {
            DataCommands::Load {
                source,
                symbol,
                exchange,
            } => data::load(&source, &symbol, exchange.as_deref()),
        },
        Commands::Calendars { command } => match command {
            CalendarCommands::Show { year } => calendars::show(year),
        },
        Commands::Schema { command } => match command {
            SchemaCommands::Export {
                output,
                typescript,
                openapi,
                pyi,
                mcp,
            } => schema::export(&output, &typescript, &openapi, &pyi, &mcp),
        },
        Commands::Codegen {
            schema_dir,
            typescript,
            openapi,
            pyi,
            mcp,
        } => schema::export_all(&schema_dir, &typescript, &openapi, &pyi, &mcp),
        Commands::Serve {
            data_dir,
            addr,
            cors_origin,
            trading_state,
            runs,
        } => serve::run(&data_dir, addr, &cors_origin, trading_state, &runs),
        Commands::Verify { manifest } => verify::run(&manifest),
    }
}
