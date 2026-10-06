# ADR 0011: Integer Money in Minor Units with Conservative Rounding

Date: 2026-10-05. Status: accepted. Roadmap story: E0-S6.

## Context

`Money` was `f64` plus a `Currency` (ADR 006 explicitly deferred the decision:
"Money stays `f64` here; the integer-money decision is E0-S6"). `f64` cannot
represent most rupee values exactly: `0.1 + 0.2 != 0.3`, and a `0.95` rounding
applied per-fill accumulates into a ledger that disagrees with the broker's by
paise-per-fill, compounding silently across a run. Prices stay `f64` — they are
market observations with tick sizes, not exact ledger entries — but anything
that is settled, summed, or compared must be exact.

## Decision

**Amounts are `i64` minor units of the money's currency.**
`Money` stores a signed integer and a currency; fractional input is rejected at
construction. Serialization emits the integer — never a JSON float — so the
wire is exact.

Conversion is explicit and conservative:

| Direction | Rule |
|---|---|
| Major → minor | round half away from zero, reject NaN/infinite, reject overflow |
| Minor → major | exact division; `to_major_f64` is lossy by definition and exists only for display/research |
| `mul_qty(qty, px)` | `round(qty * px_scaled)` — multiplication rounds to the nearest minor unit |
| Rounding mode | **half away from zero**, symmetric so buys and sells bias identically |

Conservative edges, enforced where money leaves the model:

- **Floor payouts, round stakes up to lot multiples.** A payout that rounds
  against the portfolio understates equity; a stake that rounds down silently
  under-sizes. Both round against the house, never in favour of the reporter.
- **Tick-size alignment.** A price that does not sit on a tick is rejected when
  it is used to settle, not silently snapped.
- **Per-leg rounding before summation.** Costs are computed per leg and each
  leg rounds once; summing unrounded legs and rounding once would report a
  total the broker's ledger cannot reproduce.

`quantity` stays `f64` (fractional shares exist; lot multiples are a market rule,
not a money rule). `Position.avg_price` is a *statistic* — it exists to compute
unrealized PnL, not to settle — so it rounds to the nearest minor unit on every
fill and never accumulates in f64. `realized_pnl` and `Trade.costs` are ledger
entries and are exact `i64`.

## Minor units per currency

One major unit is `10^exponent` minor units, where the exponent is a property
of the currency, not a global constant. Rust owns the table
(`Currency::minor_exponent`, `Currency::minor_unit`); Python reads it through
`honba._honba.currency_minor_units` (`Currency.minor_exponent`,
`Currency.minor_unit`) and never keeps a second copy. The shared vector
`schema/conformance/currency_minor_units.json` is read by both test suites.

| Currency | Minor unit (singular / plural) | Exponent |
|---|---|---|
| INR | paisa / paise | 2 |
| USD | cent / cents | 2 |
| EUR | cent / cents | 2 |
| GBP | penny / pence | 2 |

Every conversion (`from_major*`, `to_major`, payout floor, stake ceil, display,
position price rounding, notional) scales by `10^exponent` of the currency in
play. The exponent logic is proven at exponents 0 and 3 through crate-internal
generic functions, so adding a currency with another exponent is a table row
(and a wire variant), not a rewrite. `Money::format_minor` renders an amount
with the unit name (`1,250 paise`, `1 cent`, `300 pence`).

**Naming rule.** Generic code, tests and docs say "minor" / "minor unit". The
words paise and paisa appear only in `python/src/honba/markets/india/**`
(India cost code), in the table above and `Currency::minor_unit`, and where a
document deliberately names the INR minor unit.

## Scope

- `honba-entities`: `Money`, and the money-typed fields of `Account`,
  `Position` (`realized_pnl`), `Trade` (`costs`). The wire version bumps: the
  envelope's `ResponseEnvelope` semantics are unchanged, but amounts crossing
  the wire are integers where they were floats.
- Analytics (Sharpe, drawdown, ratios) stays `f64` — statistics, not ledger.
- `Trade.price`, bar OHLC, mark prices: observations, `f64` per ADR 006.
- Python gets `honba.entities.Money` mirroring the minor-unit contract, one
  class, with the same rounding helpers; strategies display floats, settle ints.

Alternatives rejected: `rust_decimal` adds a dependency and a non-JSON-native
wire form for no gain over `i64` minor units (Indian equities need 2 decimals;
2-decimal FX needs 2); keeping `f64` keeps the per-fill drift the audit found.

## Consequences

- `Trade.costs` and `Position.realized_pnl` serialize as JSON integers. Any
  reader that treats them as floats still parses, but producers must emit ints.
- `Money::new` takes `i64`. Call sites that held floats use
  `from_major_f64` (round half away, reject non-finite) at the boundary.
- Golden vectors `trade.json` / `position.json` record the integer form.

## Known limits

- The directional-rounding snap constant (1e-6 minor units) is below one f64 ulp
  beyond roughly 1e10 minor units, so at those magnitudes it no longer snaps
  float noise to the integer before a floor or ceiling.
