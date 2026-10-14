# ADR 0028: NSE bhavcopy connector in Rust (D10 default)

Date: 2026-10-14. Status: **accepted**. Decides D10 for the E10-S8 scope and implements the first
Rust-native market-data connector as `honba-ports::MarketDataConnector`. Follows up on
`docs/archive/plan.md` §E10 (connectors), the deferred E10-S8 in `docs/ROADMAP.md:69,370`, and the
refresh at v7 that scheduled it.

## Context

The `MarketDataConnector` contract shipped in `honba-ports` (E1-S6, commit `bc3b20b`:
`MarketDataConnector`, `GapMarker`, `ExponentialBackoff`, `FeedWatchdog`, `PortError/PortResult`).

- The Python tree already speaks NSE bhavcopy: `python/src/honba/data/loaders/nse.py`
  (`parse_bhavcopy_csv`, `NseBhavcopyProvider`) parses the three NSE CSV shapes
  (modern UDiFF, Sec Bhavdata full, and Old Bhavcopy), keeps only equity series
  (`EQ`/`BE`/`BZ`), and stamps each bar at the exact NSE open — `09:15 IST`
  (`python/tests/unit/test_nse_bhavcopy_tz.py` pins `EPOCH_NS_2024_01_02_0915_IST`).
- No Rust code parses a bhavcopy. There is no `MarketDataConnector` impl in `honba-feed` or
  `honba-ports` (the `crates/honba-engine/src/connector/` module is still a one-line stub).
- The `feed_into_engine_run` harness shows the pattern: a connector `subscribe`s, the loop pulls
  `next_event()`, and every `MarketEvent::Bar` is lifted into a kernel `Message::Bar` and fed to an
  `Engine`.
- `honba-feed` (catalog, Parquet read/write, report exporters) is where static-file data sources
  belong, but it is not itself the connector seam — `honba-ports` is. A bhavcopy source is a
  file-based replay source, so it should live as a `honba-feed` connector module that *implements*
  the `honba-ports` connector trait (like `CatalogInstrumentMaster` implements the `MasterFeed` port).
  Its acceptance witnesses are the roadmap's `bhavcopy_connector_parses_fixture` (unit) and a
  `feed_into_engine_run`-style integration (bhavcopy connector drives an engine run).

The open design decision is **D10** — "Python adapters (ADR 0010) vs Rust `ExecutionGateway` /
`MarketDataFeed` ports: who bridges, which side owns the loop". A full bridge (everything in Rust,
a Python-side shim, or a hybrid) is a much larger decision than one EOD connector warrants.

## Decision

**1. D10 default (deferred full bridge).** Until a second Rust-native session proves a pattern, the
split stays: Python `honba-adapters` own broker *execution* (REST, sessions, rate limits), and Rust
owns the first read-only *market-data* connector. That connector is the NSE **bhavcopy connector**.
The general "which side bridges which loop" question stays open and will be decided once a live
market-data session (broker WebSocket/REST polling) is proposed — not in this ADR.

**2. Bhavcopy connector shape.** New crate module `honba-feed::bhavcopy` exposing
`BhavcopyConnector`:

```
BhavcopyConnector::from_csv(csv: impl BufRead, exchange: Exchange) -> Result<Self>
BhavcopyConnector::from_path(path: impl AsRef<Path>, exchange: Exchange) -> Result<Self>
```

It parses a CSV on construction and holds a `Vec<MarketEvent::Bar>` in its subscription set.
`subscribe(SubscriptionParams)` records which `InstrumentId` the caller wants; if non-empty, the
filtered list is delivered, otherwise every equity symbol. `next_event()` returns `Bar`s in file
order (one 1-day bar per symbol) and `Ok(None)` at EOF. It never emits `GapMarker` or
`Reconnecting` and never returns `Retriable` (a bhavcopy is a static file), but its refusal path is
typed: a missing file or a header the connector cannot map is `PortError::Unrecoverable`.

The unit it emits is `MarketEvent::Bar(Bar)` where `Bar` is

```
BarType {
  instrument: InstrumentId(symbol.strip(), exchange),  // NSE by default, configurable
  spec: BarSpecification::new(1, BarAggregation::Day, PriceType::Last),
}
```

and every bar is stamped at **09:15 IST** on the row's trade date:

- Input `date_col` is mapped permissively (`TRADDT` / `DATE1` / `TIMESTAMP`; for UDiFF the trad/biz
  dates are identical) and parsed with the three forms (`YYYY-MM-DD`, `DD-MMM-YYYY`, `DD-MMM-YY`).
- The bar timestamp is `date` at `09:15:00 IST` (offset `+05:30`), i.e. `03:45:00Z`. Numeric
  equivalence to `python/tests/unit/test_nse_bhavcopy_tz.py`'s
  `EPOCH_NS_2024_01_02_0915_IST = 1_704_167_100 * 1e9` is a binding test.
- On the Python side this is the `"1D"` interval (the Rust `Day` aggregation is the same interval).

**3. Slice and format fidelity.** The Python filter is preserved: only `EQ`/`BE`/`BZ` rows survive,
and the column map covers the union of the three NSE shapes (UDiFF / Sec Bhavdata / Old Bhavcopy)
under uppercase-normalised headers:

```
sym_col          TCKRSYMB | SYMBOL
series_col       SCTYSRS  | SERIES
open_col         OPNPRIC  | OPEN_PRICE  | OPEN
high_col         HGHPRIC  | HIGH_PRICE  | HIGH
low_col          LWPRIC   | LOW_PRICE   | LOW
close_col        CLSPRIC  | CLOSE_PRICE | CLOSE
qty_col          TTLTRADGVOL | TTLTRADQTY | TTL_TRD_QNTY | TOTTRDQTY
date_col         TRADDT   | DATE1       | TIMESTAMP
```

An unknown header is not an error — the connector construction returns `Ok(Self)` with no bars
when its shape cannot be identified, and the caller sees absence rather than a partial read. A parse
failure (I/O, invalid UTF-8) is `Unrecoverable`.

**4. Scope.** Instrument master hydration is not in this story: the bhavcopy cross-checks the catalog
in some `honba-ports` instrument-master tests, but the rows only yield `Bar`s here. End-of-day
settlement (India T+1, `honba-market::india`) and the simulator remain elsewhere (E3-S1 expected
the same settlement, ADR 0016 decision). Where a volume Parse hides inside a float field (the Python
`Bar.volume` is a float), Rust's `Bar.volume` is also a `Quantity` float.

## Consequences

- The tree has a native `MarketDataConnector` witness, and its parsing matches the Python source it
  replaces — the shared fixture (UDiFF + Sec) replays identically in Python and Rust (the acceptance
  suite is defined over that fixture).
- `honba-feed` remains the catalog layer and `honba-ports` the seam; no new crate is introduced.
- The broker-adapter boundary (Python adapters → `honba` core) is untouched; broker sessions keep
  their `honba-adapters/shared` session/rate-limit/secret machinery. A live broker connector will
  carry a new D10 decision.

## Acceptance (unit / integration)

Unit (`crates/honba-feed/src/tests/bhavcopy.rs`, wired via `crates/honba-feed/src/tests/mod.rs`):

- `bhavcopy_connector_parses_fixture` — the UDiFF + Sec fixture parses to the expected `Bar`s
  (RELIANCE open `2900.0`, close `2940.0`, quantity `5_000_000`; TCS open `4200.0`, close `4230.0`).
- `bar_ts_is_ist_open_independent_of_local_tz` — the bar timestamp is `09:15 IST` regardless of the
  process timezone (assert against the same epoch `1_704_167_100 * 1e9`).

Integration (`crates/honba-feed/tests/bhavcopy_feed_into_engine.rs`):

- `bhavcopy_feed_into_engine_run` — a `BhavcopyConnector` subscribed for `RELIANCE`/`TCS`, each
  `MarketEvent::Bar` wrapped as a kernel `Message::Bar`, drives an `Engine` whose strategy counts
  bars and exits. The run sees the same bars the fixture contains. `ConnectorState` progresses
  `Disconnected → Connecting → Connected` and the stream terminates cleanly.

## Deferred

| Item | Disposition |
|---|---|
| Live broker data connectors (historical + WebSocket) | An E10-S8 follow-on; needs a new D10 slice and a market-data REST/WS fixture. |
| General bridge (Python adapters ↔ Rust ports/loop) | Leave open until a session broker connector forces the shape. |
| End-to-end Parquet round-trip of a bhavcopy import | Covered by `honba-data`/`honba-feed::dataset`; not needed for connector acceptance. |

## Known limits

- The connector delivers EOD bars only; no intraday tick or depth. L2 tick/depth stays deferred
  (ADR 0025).
- A compressed `*.csv.zip` or `*.csv.xz` archive is not accepted directly — the caller must unzip
  first (the Python `NseBhavcopyProvider` already does this; the Rust connector mirrors the parsed
  bhavcopy text, not the archive).
- A bhavcopy with a non-equity series or a missing volume field is dropped and ignored (the Python
  loader skips `IL`, `FO` and `MF`; so does this connector).

## References

- `docs/archive/plan.md` §E10, `docs/ROADMAP.md:69,188,214,370,384` (E10-S8, D10, deferred E3-S1b/S1d,
  Rust connectors), `docs/ROADMAP.md:363` (E7/E10-S8 deferred row), `docs/ROADMAP.md:398` (D10).
- `docs/adr/0010-adapter-contract.md` (adapter contract), `docs/adr/0025-tick-depth-data-source-and-l2-matching-scope.md` (L2 deferred),
  `docs/adr/0014-json-schema-from-rust.md` (generated artifacts, not relevant here).
- `crates/honba-ports/src/connector.rs`, `crates/honba-ports/src/backoff.rs` (`ExponentialBackoff`),
  `crates/honba-ports/src/watchdog.rs` (`FeedWatchdog`), `crates/honba-ports/src/error.rs`
  (`PortError::Unrecoverable`), `crates/honba-feed/tests/feed_into_engine_run.rs` (harness),
  `crates/honba-feed/Cargo.toml`, `crates/honba-feed/src/tests/mod.rs`.
- `python/src/honba/data/loaders/nse.py` (`parse_bhavcopy_csv` + tri-format column map),
  `python/tests/unit/test_nse_bhavcopy.py` (UDiFF/SEC fixture vectors),
  `python/tests/unit/test_nse_bhavcopy_tz.py` (`EPOCH_NS_2024_01_02_0915_IST` pin).
