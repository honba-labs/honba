# ADR 0010: The Adapter Contract (E1-S1)

## Status
Accepted

## Context
Story E1-S1 in [docs/archive/ROADMAP-borrowed-ideas.md](../archive/ROADMAP-borrowed-ideas.md) asks for the formal
broker-adapter surface: session/auth, order placement and cancellation, the account books,
market data and the instrument master, a capability descriptor and lazy registry discovery,
all behind a shared contract suite. Before this ADR:

- `honba.adapters.{base,registry}` were 0-byte files no code imported.
- Every module in `honba-adapters/` (8 broker packages plus `shared`) was a 0-byte stub, so
  the contract lived in prose only, in two places that disagreed: `honba-docs` described two
  async ABCs (`DataClient` + `ExecutionClient`) registered under
  `honba_adapters_shared/registry.py`, while the roadmap described one Protocol registered
  under `honba.adapters.registry`.
- The `ExecutionPort` protocol in `honba.strategies.runner` (submit intent, drain fills)
  already said "a simulator in backtest, a broker adapter in live", but no interface made
  that interchangeability real.

## Decisions
1. **Facade + two roles, not one wide ABC and not two ABCs.** `Adapter` (the facade) owns
   identity (`name`), the capability descriptor, and lifecycle (`connect / disconnect /
   session / is_connected`). `MarketDataAdapter` owns quotes, depth, history, the instrument
   master fetch and subscriptions; `ExecutionAdapter` owns placement, books, funds and margin.
   A caller depends only on the role it uses. Two ABCs without a facade (the docs sketch)
   would leave capabilities, mode and error semantics homeless.
2. **Async throughout.** Broker I/O is concurrent and a stream must not block the caller.
   `asyncio_mode = "auto"` is already in the pytest config, so contract tests need no markers.
3. **Market-neutral product vocabulary: `intraday / delivery / carry`.** Indian broker product
   codes (CNC, NRML, MIS) are E1-S4 mapping tables, not core enums: CNC maps to intraday or
   delivery depending on the order, NRML to carry, MIS to intraday. The core must not name a
   broker's product codes, the same way it does not name a pack.
4. **Refusals are data, capabilities are errors.** A broker refusing an order returns an
   `OrderReport` with `status=REJECTED` and a `reject_reason` (journaled, reconcilable).
   An adapter without the capability raises `CapabilityError` before any request, checked
   against the descriptor it published. Cancelling a terminal order is a no-op (cancel is
   idempotent); looking up an unknown order or instrument raises `AdapterError`.
5. **The contract suite is behavioural, not presence-only.** It connects a real instance,
   refuses work before connect, probes every declared-unsupported method for
   `CapabilityError`, checks every declared-supported method returns its canonical type,
   places, retrieves, cancels and reconciles a probe order, and verifies fill reports reach
   the trade and position books. The suite is pytest-free (no imports, deterministic, no
   network) so every adapter package runs it unchanged.
6. **The registry mirrors `honba_market::MarketRegistry`.** Explicit registration,
   `create(name, **config)`, sorted `available()`, one-shot lazy entry-point discovery under
   group `"honba.adapters"`. Explicit registration wins over discovery.
7. **Mode is per-run, on the session.** `SessionInfo.mode` is `backtest | paper | live`; a
   strategy never reads it. The E3-S7 paper adapter is one adapter implementation among
   others, selected by run config like any broker.
8. **Only `AdapterError` subclasses may escape.** Anything else (a `KeyError` from a dict
   lookup, a broker SDK exception, a bare `ValueError`) is a contract violation: it means
   the adapter did not translate the broker's vocabulary at the boundary.

## Consequences
- Adapter authors depend on `honba.adapters` (`base`, `models`, `capabilities`, `contract`,
  `testing`, `boundary`) and on nothing broker-specific; broker code lives in
  `honba-adapters/`, discovered by entry point or registered by config.
- `FakeAdapter` (in-memory, deterministic, reduced capabilities on purpose) certifies the
  suite and documents the contract; it is not a paper-trading adapter (that is E3-S7).
- `honba-adapters/shared` ships the suite to the broker packages (`contract.py`,
  `fixtures.py`) and the boundary test over its own tree; P2 publishes the schemas.
- Known follow-ups recorded as tickets: a second real adapter must pass the suite
  (acceptance deferred — no broker code exists yet); streaming delivery, reconnect and gap
  markers belong to E1-S6; E2-S6 adds the account-event push form next to this query form.
