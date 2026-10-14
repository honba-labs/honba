# ADR 0029: StrategyGraph v1 and the AlgoDesigner (E9-S1..S7)

Date: 2026-10-14. Status: **accepted**. Decides **D16** and the E9-S1..S7 scope.

## Context

The AlgoDesigner (frontend `algodesigner-app`) is a placeholder; authors write strategies as raw
Python while the catalogue's indicators, verifier and backtest engine already exist. A visual
composer must not become a second source of truth: whatever it produces has to verify, run and
publish exactly like hand-written code. That raises one design decision, **D16**: where a designed
strategy lives — a JSON graph document, a Python file, or both.

Meanwhile E9-S6 (ops console) and E9-S7 (result views) need run/verify surfaces that already
exist: the REST read API (Inproc/Http transports, backtests, sweeps, orders, positions, journals)
and the Rust ``ValidationBlock`` (DSR/PBO/holdout/walk-forward/sweeps). The ops console must honour
the kill switch and approval queue; the result views must show equity/drawdown, the trade log, the
``not_modelled`` assumptions and trust badges.

## Decision

**D16: Python file + optional ``.strategy.json`` sidecar.** The strategy is a generated,
runnable ``.py`` module; a ``.strategy.json`` graph document may sit beside it. The graph is the
lossless, machine-readable core (a builder block embedded in the module); the module is what
verifies and runs. ``source_hash`` covers both files; the graph keeps a ``graph_hash``.

**S1 — Core graph model** (`python/honba/strategies/graph.py`). Versioned StrategyGraph JSON v1
(``schema_version`` 1) with typed nodes (``universe``, ``indicator``, ``transform``,
``condition``, ``logic``, ``sizing``, ``risk``, ``order``, ``schedule``, ``benchmark``), typed
ports and edges, params and a manifest bridge. The node catalogue is **derived from the registered
Python indicators** (kind, family, inputs, outputs, params, defaults, types and deterministic
sweep ranges are introspected, never hand-listed). Validation returns ``honba verify``'s error
codes for manifest-level defects, mirrored so a graph and its manifest fail with the same code.

**S2 — Graph -> code** (`python/honba/strategies/graph_codegen.py`). Deterministic codegen to
builder-style Python plus an ``on_bar`` strategy class: stable node ordering and names,
parameters as one-line ``Parameter`` declarations, and ``reason=`` strings that carry the order
node labels. The builder block is the lossless core; generation is byte-deterministic.

**S3 — Code -> graph** (`python/honba/strategies/graph_recognize.py`). An AST recogniser that reads
exactly the generated subset plus the documented idiom (a generated module with hand-written
additions). Graph->code->graph identity holds over the whole indicator catalogue; unsupported
spans are returned with line ranges; non-subset code is marked ``code_only`` and never
reconstructed lossily.

**S4 — Verify/run from designer** (`python/honba/strategies/designer.py`). ``verify_graph``
(validates, compiles to the IR, locates issues per node and generated-source line) and
``verify_and_run`` (verify, generate, and run through the existing ``Honba.backtest`` session
engine — the event-driven runner and simulated port; nothing about the engine is reimplemented).
The run handle is deterministic for fixed inputs. Publish-readiness checklist plus version
diff/restore helpers live here. Manifest integration: ``StrategyManifest`` gains Python-only
``graph_hash`` + optional ``sidecar``, stripped from the Rust serde shape (``manifest.rs``
untouched); ``verify_strategy_source`` accepts a Python file plus an optional ``.strategy.json``
and hashes both into ``source_hash``.

**S5 — AI assistant hook** (`python/honba/ai/designer_assistant.py`). The assistant proposes
graph/code diffs only. Every candidate is routed through the E5-S5 verifier and E5-S7 typed
decisions; the outcome is typed ``discussion`` | ``candidate`` | ``validation_error`` (findings
and, for order-level attachments, veto/size-down decisions ride along). The frontend mirrors this
as the assistant panel.

**S6 — Ops console (frontend).** Run-mode badge (paper/signal-only/live), positions, orders,
executions, logs and AI decisions; pause/stop/close wired to the existing generated client
endpoints; a blocked live badge unless the order is approved and the kill switch is off. Tested
against Inproc/Http transport mocks.

**S7 — Result views (frontend).** Equity/drawdown SVG (no new chart library), trade log, the
run's ``assumptions.not_modelled`` block, trust badges from the ``ValidationBlock``
(DSR/PBO/holdout/walk-forward), and sweeps + walk-forward as async jobs that persist in
zustand/localStorage so the user can leave and return.

## Consequences

* The graph, the generated Python and the IR share one validation vocabulary, so designer errors
  and ``honba verify`` errors carry the same codes.
* Round-trip identity is a tested property over the catalogue, not an aspiration: the builder
  block is the contract both codegen and the recogniser honour.
* The frontend never executes strategy code and never adds a chart library: it shells to the
  existing client endpoints and drawn-SVG views.
* No Rust changes and no new engine: the verifier, session engine and transports are reused.
