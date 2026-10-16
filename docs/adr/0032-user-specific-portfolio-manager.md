# ADR 0032: User-Specific Portfolio Manager

Date: 2026-10-16. Status: **accepted** (2026-10-16). Roadmap stories: E13-S0..E13-S6. Owner: `honba` (+ `honba-frontend`).

## Context

The product needs a **portfolio per user**: the accounts they hold, their positions and delivery
holdings, cash and equity, realized and unrealized PnL, their trade history, and performance
analytics — shown on the chart and in the workbench, computed the same way on the server and in the
browser. "User-specific" is the point: one person's positions never appear for another.

What already exists, and where it stops:

- **Money and position primitives.** `honba-entities` (L1) already has `Money`/`Currency` in
  integer minor units (ADR 0011), `Position` (net quantity, volume-weighted average price, realized
  PnL accrued per fill, `unrealized_pnl(mark)` computed on demand), `Account { cash, positions }`
  and `Portfolio { accounts }`. But `Account`/`Portfolio` are **in-memory, not serializable and
  unscoped** — there is no owner and no wire form.
- **The per-user profile system** is the only thing already scoped to a person. A request carries a
  `Caller` (the validated bearer token's `token_id`, or `default` when auth is off); `ProfileStore`
  keeps **one JSON file per user** under a profiles directory, with a revision compare-and-swap,
  atomic writes (temp + rename + fsync), size and count caps, and corrupt-file quarantine
  (`honba-api-rest::{profile,profile_store}`). It holds layouts, modules, watchlists and
  preferences — but **no money**.
- **Orders and fills are unscoped and volatile.** `OrderLedger` (`honba-api-rest::orders`) is
  in-memory, process-wide, and empty on restart; the execution path and the operator journal
  (ADR 0018) exist. There is **no `GET /positions`** (only `POST /positions/close`), and **no**
  `/accounts`, `/portfolio` or `/holdings` route. `PositionsResponse` is defined but unused. The
  **`read:account` scope is reserved and unused** (ADR 0023).
- **The frontend shows mock data.** The workbench store holds hard-coded `INITIAL_POSITIONS`, and
  `UserPreferences` calls a `/api/user/preferences` that does not exist server-side.

Prior art, researched 2026-10-16:

- **OpenAlgo** separates `/holdings` (delivery, with per-line `pnl`/`pnlpercent` and totals
  `totalholdingvalue`/`totalinvvalue`/`totalprofitandloss`/`totalpnlpercentage`) from
  `/positionbook` (intraday: `quantity`, `average_price`, `ltp`, `pnl`, and closed rows with
  quantity `0`), and exposes funds, order book and trade book. Its sandbox is a **separate set of
  tables** (`SandboxOrders/Trades/Positions/Holdings/Funds/DailyPnL`) so paper money never mixes
  with live. `openalgo-charts`' **`AccountManager`** is **read-only**: it lists the provider's
  accounts, loads the selected account's snapshot (balance, equity, margin used/available,
  unrealized and realized PnL, leverage) stamped with the provider's `asOf`, and reads positions,
  executions and order history; orders name the selected account, and a sandbox engine wired to a
  live view refuses to stamp a live account.
- **TradingView** has **Portfolios** (up to three; portfolio value, unrealized gain, total gain,
  annualized yield, value/performance vs a benchmark, holdings grouped by currency/symbol
  type/sector/exchange, a sold-holdings history, and a transaction log with deposits/withdrawals)
  and an **account manager** (balance, equity, realized/unrealized PnL, margin, available funds),
  with **paper-trading sub-accounts** and an import-from-paper-trading. Portfolios are analytical
  hubs owned by the user; a portfolio is not a broker account.

The gap: there is no valued, per-user portfolio, no read surface for positions/holdings, and
nothing binds positions or PnL to a user.

## Decision

### 1. A portfolio is owned by the authenticated user

Ownership reuses the profile scoping **exactly**: the owner is the request's `Caller` — the
`token_id` when auth is on, `default` otherwise. One portfolio document per user. No new identity
system: profiles answer *who*, portfolios answer *what they hold*, and the two use the same
subject. `read:account` (reserved until now) governs read; `write:orders` governs writes that move
money; `operator` governs configuration.

### 2. The wire contract lives in `honba-entities` (L1)

Beside the screener and signal contracts (ADR 0006/0014/0031 pattern), so `honba-codegen` (L5) and
`honba-api` (L6) name it without the engine:

```text
PortfolioSnapshot { owner, revision, as_of, base_currency, accounts[], totals }
PortfolioAccount  { id, name, mode: Live|Sandbox, currency, cash, equity,
                    market_value, margin_used, margin_available, leverage,
                    realized_pnl, unrealized_pnl, positions[], holdings[] }
PortfolioPosition { instrument_id, side, quantity, avg_price, mark, market_value,
                    unrealized_pnl, realized_pnl, product }   // open / intraday
PortfolioTrade    { ts, instrument_id, side, quantity, price, fees, order_id }  // history
PortfolioTotals   { market_value, cash, equity, realized_pnl, unrealized_pnl, total_pnl }
```

- **Holdings vs positions** mirror OpenAlgo: holdings are `Product::Delivery` (carried), positions
  are intraday/open. Both are projections of the same `Position` primitive.
- Money is integer minor units (ADR 0011); `as_of` is a `UnixNanos`; the change is **additive**
  (ADR 0012). `PortfolioPosition` is the *valued* projection of the existing serializable
  `Position`; the in-memory `Account`/`Portfolio` remain the engine's working type.

### 3. Valuation is a pure function; no clock, no I/O

A new pure crate **`honba-portfolio` (L4, beside `honba-signals`)** turns positions plus a mark
source into a `PortfolioSnapshot`: realized PnL reuses `Position::apply_fill`'s per-fill accounting,
unrealized reuses `Position::unrealized_pnl(mark)`, market value and equity are integer money. It
takes `as_of` as an argument, never reads a clock, and holds no network. Same positions and marks
give a byte-identical snapshot, so the server, a backtest and the browser agree (the ADR 0031 §1
discipline). Marks come from the same last quote/close the chart uses.

### 4. `mode: Live | Sandbox` mirrors OpenAlgo

Sandbox (paper) money must never mix with live money. An account carries its mode; totals are
per-mode; an order names the account it belongs to, and the engine refuses an order that names an
account from the other mode (the `AccountManager` rule). **Default is `Sandbox`**: there is no live
venue yet (E3-S7 runs the sandbox), so nothing can silently present paper money as live.

### 5. Persistence mirrors the profile store

A `PortfolioStore` in `honba-api-rest` beside `ProfileStore`: one JSON file `<dir>/<user_id>.json`,
revision CAS, atomic temp+rename+fsync, size/count caps, corrupt-file quarantine — the same code
shape and the same operational caveat (one `honba serve` per directory; in-process lock only). This
is **not** a database: a portfolio is a small document. The *live* ledger (fills arriving from the
execution path) is separate and may persist as NDJSON like the operator journal; the store holds
the durable snapshot a user authored or imported.

### 6. Read-first, wired to fills and marks

- `GET /portfolio` — snapshot, valuation and totals (the read-only `AccountManager` model).
- `GET /portfolio/accounts`, `/positions`, `/holdings`, `/trades`.
- The previously missing **`GET /positions`** lands here as an alias of `/portfolio/positions`.
- Fills from the execution path update positions; a mark refresh revalues. Nothing here places an
  order.

A later slice adds the **write** path — manual transactions and CSV import (TradingView) and
import-from-paper-trading — through the same journal and risk discipline.

### 7. Scope boundary (unchanged)

A portfolio is **research and presentation**: it never places an order, never invents an execution
column, and is not a strategy. It is *fed* by fills and marks. Live-broker reconciliation stays in
`honba-engine` (E2-S7). This is the same boundary the render contract and the signal contract draw
(ADR 0031 §9). A live broker account, when it exists, is a *source* of fills and marks, not a new
portfolio owner.

### 8. Analytics is a later slice

Portfolio value over time, annualized yield, benchmark comparison and risk-adjusted metrics
(Beta/Sharpe/Sortino) are TradingView-parity analytics; they need an equity series and a benchmark.
They land last, so the contract and valuation are fixed first.

### 9. Frontend

A **Portfolio** panel replaces the mock `INITIAL_POSITIONS`: an account switcher showing equity and
margin with a mode tag, a holdings/positions table (grouping, PnL columns), a trade history, and
portfolio cards — all from the generated client, bound to the profile store for the selected
account and layout. It reuses the workbench drawer already typed for `positions`/`orders`/`trades`.

## Story split (E13)

| ID | Scope | Deps |
|---|---|---|
| E13-S0 | Contract: `PortfolioSnapshot/Account/Position/Trade/Totals` in `honba-entities`; register in codegen; regenerate every artifact (additive, ADR 0012) | ADR 0032 |
| E13-S1 | `honba-portfolio` (L4): pure valuation/aggregation engine + golden vectors; layering + workspace | E13-S0 |
| E13-S2 | `PortfolioStore` (per-user JSON, mirroring `ProfileStore`) + `Caller` wiring + read routes (`GET /portfolio*`), activating `read:account` | E13-S1 |
| E13-S3 | Fill ingest: the execution path and `OrderLedger` update positions; `Live`/`Sandbox` separation; the missing `GET /positions` | E13-S2 |
| E13-S4 | Write path: manual transactions + CSV import (+ import-from-paper-trading), journaled | E13-S3 |
| E13-S5 | Frontend Portfolio panel replacing the mock positions | E13-S2 |
| E13-S6 | Analytics: value series, annualized yield, benchmark compare, Beta/Sharpe/Sortino | E13-S3 |

Slice order: **S0 → S1 → S2**, then **S3/S5** in parallel, then **S4**, then **S6**. E13 is
pull-based, like E7/E8/E12.

## Consequences

- **A portfolio is first-class and per-user**, so the chart, the workbench and any future alert all
  read the same snapshot for the same person.
- **Additive wire/schema change** only; existing indicator, screener, signal and profile contracts
  are untouched. The reserved `read:account` scope is finally used (no scope-map drift).
- **No database, no clock**: portfolios are documents and valuation is pure, so snapshots are
  reproducible and testable, and the operational story stays "one `serve` per profiles dir".
- **Live money stays out until a live venue exists**: `Sandbox` is the default mode, so a paper
  position can never be shown as live.
- **Frontend work is bounded**: the drawer and PnL columns already exist; the new work is the
  panel, the account switcher and the client wiring (plus the pending profile store).
- The existing in-memory `Account`/`Portfolio` are not deleted; they remain the engine's working
  type and the wire types are their serializable, valued projection.

## Open items for code review

- Persistence medium: JSON per user (recommended, matches profiles) vs SQLite once histories grow.
- Whether the live fill ledger persists in the portfolio document or beside it as NDJSON.
- Multi-account default: a single implicit `default` account per user vs an explicit account list
  from day one (TradingView allows up to three portfolios; OpenAlgo allows many broker accounts).
- Whether the `honba-portfolio` engine should also feed the WASM surface (client-side valuation) in
  this epic or in a follow-up.
- Currency handling for a multi-currency portfolio (base currency and FX) — out of scope for v1,
  which assumes one settlement currency per account.

## Decision log

- 2026-10-16: proposed (research: OpenAlgo `/holdings`/`/positionbook`/sandbox and `AccountManager`;
  TradingView Portfolios and the account manager).
- 2026-10-16: **accepted**; E13-S0..S6 authorized. Refinement recorded on acceptance: **both
  position sources ship in this epic** — an account carries a `source: PortfolioSource`
  (`Ledger` = honba's own fills/simulator, `Broker` = read from an adapter), so the local ledger
  and broker-synced holdings coexist behind one contract from the start (a `Broker` account also
  names its `broker`). The existing `honba_entities::Trade` is reused as the trade-history record
  rather than duplicated. The wire types are `PortfolioSnapshot`, `PortfolioAccount`,
  `PortfolioPosition`, `PortfolioHolding`, `PortfolioTotals` and the enums `PortfolioMode`,
  `PortfolioSource`.
