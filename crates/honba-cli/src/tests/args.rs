//! Unit tests for the command-line grammar in `main.rs`.

use std::path::PathBuf;

use clap::Parser;

use crate::{CalendarCommands, Cli, Commands, DataCommands};

fn parse(args: &[&str]) -> Result<Cli, clap::Error> {
    Cli::try_parse_from(std::iter::once("honba").chain(args.iter().copied()))
}

#[test]
fn backtest_takes_a_config_and_an_optional_output() {
    match parse(&["backtest", "cfg.toml", "-o", "out.md"])
        .unwrap()
        .command
    {
        Commands::Backtest { config, output } => {
            assert_eq!(config, PathBuf::from("cfg.toml"));
            assert_eq!(output, Some(PathBuf::from("out.md")));
        }
        _ => panic!("expected backtest"),
    }
    match parse(&["backtest", "cfg.toml"]).unwrap().command {
        Commands::Backtest { output, .. } => assert_eq!(output, None),
        _ => panic!("expected backtest"),
    }
}

#[test]
fn calendars_show_requires_a_year() {
    match parse(&["calendars", "show", "--year", "2025"])
        .unwrap()
        .command
    {
        Commands::Calendars {
            command: CalendarCommands::Show { year },
        } => assert_eq!(year, 2025),
        _ => panic!("expected calendars show"),
    }
    assert!(parse(&["calendars", "show"]).is_err());
    assert!(parse(&["calendars", "show", "--year", "twenty"]).is_err());
}

#[test]
fn data_load_takes_source_and_symbol() {
    match parse(&["data", "load", "bars.parquet", "TCS"])
        .unwrap()
        .command
    {
        Commands::Data {
            command:
                DataCommands::Load {
                    source,
                    symbol,
                    exchange,
                },
        } => {
            assert_eq!((source.as_str(), symbol.as_str()), ("bars.parquet", "TCS"));
            assert_eq!(exchange, None);
        }
        _ => panic!("expected data load"),
    }
}

#[test]
fn data_load_accepts_an_exchange_flag() {
    match parse(&["data", "load", "b.parquet", "TCS", "--exchange", "BSE"])
        .unwrap()
        .command
    {
        Commands::Data {
            command: DataCommands::Load { exchange, .. },
        } => assert_eq!(exchange.as_deref(), Some("BSE")),
        _ => panic!("expected data load"),
    }
}

#[test]
fn a_subcommand_is_required_and_unknown_ones_are_rejected() {
    assert!(parse(&[]).is_err());
    assert!(parse(&["optimize"]).is_err());
}

#[test]
fn clap_definition_is_consistent() {
    use clap::CommandFactory;
    Cli::command().debug_assert();
}

#[test]
fn serve_defaults_to_loopback_and_needs_a_data_dir() {
    match parse(&["serve", "--data-dir", "bars"]).unwrap().command {
        Commands::Serve { data_dir, addr } => {
            assert_eq!(data_dir, PathBuf::from("bars"));
            assert_eq!(addr, "127.0.0.1:8080".parse().unwrap());
        }
        _ => panic!("expected serve"),
    }
    match parse(&["serve", "--data-dir", "d", "--addr", "127.0.0.1:0"])
        .unwrap()
        .command
    {
        Commands::Serve { addr, .. } => assert_eq!(addr.port(), 0),
        _ => panic!("expected serve"),
    }
    assert!(parse(&["serve"]).is_err());
    assert!(parse(&["serve", "--data-dir", "d", "--addr", "not-an-addr"]).is_err());
}
