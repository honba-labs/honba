#!/usr/bin/env python3
"""Enforce the Honba crate dependency hierarchy."""
import sys, tomllib
from pathlib import Path

ALLOWED = {
    "honba-messages": set(),
    "honba-entities": {"honba-messages"},
    "honba-market": {"honba-entities", "honba-messages"},
    "honba-engine": {"honba-messages", "honba-entities"},
    "honba-indicators": {"honba-messages", "honba-entities"},
    "honba-sim": {"honba-messages", "honba-entities", "honba-engine"},
    "honba-strategy": {"honba-engine", "honba-indicators", "honba-messages", "honba-entities"},
    "honba-testing": {"honba-engine", "honba-messages", "honba-entities", "honba-sim"},
    "honba-analytics": {"honba-messages", "honba-entities"},
    "honba-data": {"honba-messages", "honba-entities", "honba-analytics"},
    "honba-py": {"honba-messages", "honba-entities", "honba-engine", "honba-strategy", "honba-sim"},
    "honba-cli": {"honba-messages", "honba-entities", "honba-market", "honba-engine", "honba-analytics", "honba-data", "honba-strategy", "honba-testing", "honba-sim"},
}

def main() -> int:
    root = Path(__file__).resolve().parent.parent / "crates"
    errs = 0
    for d in sorted(root.iterdir()):
        f = d / "Cargo.toml"
        if not f.exists():
            continue
        data = tomllib.loads(f.read_text())
        name = data.get("package", {}).get("name", d.name)
        deps = {k for k in data.get("dependencies", {}) if k.startswith("honba-")}
        bad = deps - ALLOWED.get(name, set())
        if bad:
            print(f"VIOLATION: {name} -> {sorted(bad)}")
            errs += 1
    if errs:
        return 1
    print("Dependency hierarchy OK.")
    return 0

if __name__ == "__main__":
    sys.exit(main())

SETUP_SH = """#!/usr/bin/env bash
set -euo pipefail
cargo build --workspace
(cd python && pip install -e '.[dev]')
echo 'Honba dev environment ready.'
"""

PYPKG = ["core", "entities", "strategies", "backtest", "research",
         "india", "adapters", "ai", "cli"]

PYSUB = {
    "core": ["engine", "clock", "cache", "config"],
    "entities": ["instrument", "order", "position", "portfolio", "trade"],
    "strategies": ["base", "config"],
    "backtest": ["node", "config", "result", "runner"],
    "india": ["calendar", "costs", "universes", "mutual_funds", "etf", "options", "equities"],
    "adapters": ["registry", "base"],
    "strategies/templates": ["momentum_template", "mean_reversion_template", "options_template"],
    "research/vectorized": ["engine", "signals", "portfolio"],
    "research/data_loader": ["nse", "bse", "amfi", "broker", "cache"],
    "research/notebook": ["display", "plotting"],
    "ai/llm": ["provider", "tools"],
    "ai/llm/prompts": ["research_analyst", "strategy_critic", "hypothesis_generator"],
    "ai/autoresearch": ["loop", "journal", "hypothesis", "evaluator"],
    "ai/rl": ["environment", "agent", "trainer", "policies"],
    "ai/mcp": ["server", "tools", "resources", "handlers"],
    "ai/verification": ["nl_to_strategy", "critic", "regime_report"],
    "cli": ["backtest", "optimize", "research", "data", "strategy", "ai"],
}

CATS = {
    "01_momentum": ["sma_crossover", "rsi_divergence", "macd_trend", "supertrend_follow", "breakout_52week"],
    "02_mean_reversion": ["bollinger_bounce", "pairs_trading", "vwap_reversion", "rsi_oversold"],
    "03_alpha_universe": ["alpha30_momentum", "alpha30_mean_reversion", "alpha30_factor", "alpha30_low_vol", "alpha30_quality"],
    "04_options": ["nifty_short_straddle", "banknifty_iron_condor", "expiry_day_scalp", "calendar_spread", "covered_call"],
    "05_mutual_funds": ["sip_optimizer", "nav_momentum", "category_rotation", "expense_ratio_arb"],
    "06_etf": ["tracking_error_arb", "sector_rotation", "gold_equity_rotation", "international_diversification"],
    "07_intraday": ["opening_range_breakout", "vwap_scalp", "gap_fill", "pivot_bounce"],
    "08_swing": ["earnings_momentum", "sector_leadership", "relative_strength"],
}

EX = {
    "basic": ["01_connect_dhan", "02_fetch_instruments", "03_subscribe_quotes", "04_place_order_paper", "05_check_positions"],
    "candles": ["01_historical_candles", "02_realtime_candles", "03_custom_aggregation", "04_multi_timeframe"],
    "storage": ["01_parquet_catalog", "02_import_nse_bhavcopy", "03_import_amfi_nav", "04_export_tearsheet"],
    "universes": ["01_nifty50_constituents", "02_banknifty_constituents", "03_alpha30_constituents", "04_universe_rebalance", "05_custom_universe"],
    "strategies": ["01_first_strategy", "02_with_indicators", "03_position_sizing", "04_risk_management", "05_multi_instrument"],
    "backtesting": ["01_first_backtest", "02_cost_modeling", "03_latency_modeling", "04_walk_forward", "05_monte_carlo", "06_concurrent_backtest"],
    "mutual_funds": ["01_fetch_nav", "02_sip_backtest", "03_category_analysis", "04_portfolio_optimization"],
    "options": ["01_option_chain", "02_greeks_calculation", "03_backtest_straddle", "04_backtest_iron_condor", "05_expiry_day_strategy"],
    "ai_research": ["01_llm_research_analyst", "02_autoresearch_loop", "03_nl_to_strategy", "04_rl_training", "05_mcp_integration", "06_natural_language_critique"],
    "production": ["01_paper_trading", "02_live_trading_dhan", "03_multi_account", "04_monitoring", "05_deployment"],
}

BROKERS = ["dhan", "zerodha", "angelone", "fyers", "upstox", "kotak", "iifl", "motilal"]
ADAPTER_MODS = ["config", "data", "execution", "instruments", "websocket", "http", "parsing", "constants"]
SHARED_MODS = ["auth", "rate_limit", "websocket", "parsing", "errors"]
