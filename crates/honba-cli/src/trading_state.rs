use anyhow::Result;
use clap::Subcommand;
use honba_messages::TradingState;

#[derive(Subcommand, Debug)]
pub enum TradingStateCommands {
    /// Get the current trading state
    Get,
    /// Set the trading state
    Set {
        /// New trading state (active, reducing, halted)
        state: String,
    },
}

pub fn run(command: TradingStateCommands) -> Result<()> {
    match command {
        TradingStateCommands::Get => {
            // For now, just print the default state
            // In a real implementation, this would query the running server
            println!("active");
        }
        TradingStateCommands::Set { state } => {
            let state = state.to_lowercase();
            let new_state = match state.as_str() {
                "active" => TradingState::Active,
                "reducing" => TradingState::Reducing,
                "halted" => TradingState::Halted,
                other => {
                    anyhow::bail!(
                        "unknown trading state {other:?}; expected active, reducing or halted"
                    )
                }
            };
            println!("Trading state would be set to: {:?}", new_state);
        }
    }
    Ok(())
}
