# honba

Honba is an algorithmic trading, strategy research, and screener library.

## Layout

- `honba.domain`: Pure domain models (`Bar`, `Instrument`, `Order`, `Position`, `Portfolio`, `Trade`, `Tick`)
- `honba.wire`: Serialization models and wire contracts (ADR 006)
- `honba.markets`: Market packs (e.g. `honba.markets.india`)
- `honba.data`: Loaders, query parser, and Parquet/ledger data store
- `honba.strategies`: Strategy framework (`Strategy`, `StrategyContext`, `indicators`)
- `honba.screener`: Screener catalog, evaluation, and scanning services
- `honba.cli`: CLI entry points

## Installation

```bash
pip install -e .
```
