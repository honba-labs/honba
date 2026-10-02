Where to keep this in honba-labs
Honba already separates decisions from detailed design:





























LocationRole todayhonba/doc/adr/Numbered ADRs (0001…0008) — decisions + rationalehonba/crates/honba-cli/Design.mdCLI/screener surface design (proposed)honba/docs/Source docs (published via honba-docs)honba-docsPublic site (MkDocs + mdbook)python/honba/ai/Planned home for MCP gateway, LLM, journal (per CLAUDE.md)
Recommended placement

ADR (decision)
honba/doc/adr/0009-agentic-mcp-and-local-llm-integration.md
Short: context, options, decision, consequences. Status: Proposed → Accepted.
Detailed design (implementation)
honba/docs/design/agentic-integration.md
Full surface: MCP tools, CLI contract, Colibri adapter, internal LLM strategy loop, safety.
Cross-link from ADR 0009 and from crates/honba-cli/Design.md §13 (MCP / skills already sketched there).
Later (public)
Mirror a polished version under honba-docs once the MCP server ships (same pattern as OpenAlgo’s agent/MCP pages).
Do not put the long design only under crates/honba-cli/ — agentic integration spans python/honba/ai, adapters, strategy host, and CLI. CLI Design.md should link to the broader doc and keep screener-specific detail.


Design document
Below is the full text ready to drop into the repo (as docs/design/agentic-integration.md, with a short ADR sibling).

Markdown# Honba Agentic Integration: MCP, Local LLM, and Execution Bridges

**Status:** Proposed  
**Related:** ADR 0005 (market packs), ADR 0006 (wire contract), ADR 0008 (strategy parity),  
`crates/honba-cli/Design.md` (CLI + screener + knowledge pack §13)  
**Scope:** Locally hosted trading-research system; AI agents (external + in-process); optional Colibri execution bridge.

---

## 1. Goals

1. **Agent-native research loop** — External coding agents (Cursor, Claude Code, Codex) and an optional in-process LLM can discover markets, screen, backtest, and author strategies without inventing APIs.
2. **One contract, three surfaces** — CLI, MCP tools, and HTTP/API all map to the same wire models (`honba.entities`) and the same domain services. No agent-only semantics.
3. **Local-first** — Default transport is stdio / localhost. No public bind without explicit auth. Broker and Colibri credentials never enter LLM context.
4. **Safe strategy authorship** — An internal LLM may generate Python strategy code against the Honba python-sdk; code is validated, sandboxed, journaled, and never auto-started live.
5. **Optional low-latency execution** — Colibri Local API as an execution/read bridge for scalping; Honba remains the research and strategy control plane.

### Non-goals

- Multi-tenant SaaS or cloud-hosted agent gateway.
- Replacing Honba’s own sim/paper path with Colibri for systematic strategies.
- Fine-tuning models on proprietary data (knowledge pack + constrained decoding only).
- General-purpose code execution outside the strategy allow-list sandbox.

---

## 2. Context and prior art

| System | Pattern we adopt |
|--------|------------------|
| **OpenAlgo** | Self-hosted MCP (stdio + optional remote); built-in agent with tool confirmation; Python strategy host with process isolation; CLI for agents. Indian-broker focus. |
| **QuantDinger MCP** | Thin MCP over Agent Gateway; scoped tokens; idempotency keys; confirm flags for live/stop; compile → save source → deploy → backtest job model; fire-and-poll jobs. |
| **HydraTrade agent** | Portable skill markdown + thin CLI; research skills model-invocable; live/order skills `disable-model-invocation` + `--yes`; public/private skill split. |
| **Colibri Local API** | Loopback HTTP/WS; Python SDK + discovery file; token only for money-moving routes; keys stay in terminal process. |
| **Honba today** | Rust engine + Python control plane; `honba-cli` rudimentary (backtest/calendars/data); CLI Design.md proposes rich screener + knowledge pack + MCP sketch (§13.9–13.10); `python/honba/ai` reserved for MCP/LLM/journal. |

---

## 3. Architecture

```
┌──────────────────────────────────────────┐
│  External agents (Cursor, Claude, …)     │
│  Skills (generated) + shell + MCP client │
└─────────────┬──────────────┬─────────────┘
│ stdio MCP    │ CLI (json)
▼              ▼
┌─────────────────────────────────────────────────────────────────┐
│ Honba control plane (Python)                                    │
│  honba.cli  │  honba.ai.mcp  │  honba.ai.llm  │  strategy host  │
│       │              │                │                │        │
│       └──────────────┴────────────────┴────────────────┘        │
│                         domain services                         │
│         (screener, data, strategy, research, journals)          │
│                         wire models (entities)                  │
└───────────────┬─────────────────────────────┬───────────────────┘
│ PyO3 / engine API             │ optional adapter
▼                               ▼
Rust workspace                    Colibri Local API
(engine, data, sim,               (127.0.0.1 only)
strategy, analytics)             order book / exec
text**Principles**
```
- **Thin edges** — CLI commands and MCP tools call services; no screening/trading logic in the tool layer (same as Design.md §7 and QuantDinger/Jesse patterns).
- **Fire-and-poll for long work** — Universe scans with gap-fill, bulk fetch, backtests return `job_id` / `scan_id`; clients poll or stream until done.
- **Determinism** — `--asof`, journals, `--print-request` / `--request`; LLM path records knowledge-pack hash, model id, seed.
- **Layering** — Agents never talk to brokers or Colibri tokens directly; adapters sit behind Honba services.

---

## 4. Surfaces

### 4.1 CLI (human + agent)

Shape (from CLI Design.md):

```text
honba <app> <verb> [options] [trailing English filters…]
```

| App,      |      Verbs (v1 target),   |      Impl            |   Notes |
|-----------|---------------------------|----------------------|----------|
| screener, | `scan`, `explain`, `metrics` |       Python  |   Already fully designed |
|          | `presets`, `save`, `run`, `ask`| | |

data,           "coverage, gaps, fetch, load, import",    Python + Rust where hot             Gap-fill policy journals

backtest,       "run, status, list, cancel",              Rust engine + Python job API        Async job model

strategy,       "list, show, compile, save",              Python host                         Catalog driven
                "deploy, stop, logs"

ai,             "knowledge export|check",                 Python                              Knowledge pack + hydra style
                "eval, skills install"

mcp,            "serve",                                  Python FastMCP / MCP SDK            stdio+localhost_HTTP     MCP Gateway

colibri,        "status, book, positions",                Optional adapter                    Optional (colibri only)
                " order (gated)"

calendars,       "show",                                  Existing Rust                       Calendars


config/ doctor  "health, schema check                     Both


Shared agent-friendly flags: 
```
    --format json|ndjson|csv|parquet, 
    --print-request, 
    --request, 
    --asof,
    --journal,
    --dry-run,
    --yes / confirm flags,
    --idempotency-key,
    --max-fetch.
```
Exit codes: 0 ok, 1 invalid input, 2 data/source error, 3 approval/budget required.

Rust binary (crates/honba-cli) remains for engine-heavy commands; Python honba entry is the primary agent/CLI surface. Same conceptual verbs where both exist.

### 4.2 MCP gateway
Package: python/honba/ai/mcp (or honba-mcp console script).
Transports




















TransportDefaultAuthstdioYesProcess inherits Honba config; no networkstreamable-http / SSEOpt-inBind 127.0.0.1 only; separate inbound bearer token (≥32 chars). Never 0.0.0.0 by default.
Tool groups (thin over services)













































GroupScopeTools (illustrative)HealthRwhoami, check_healthMarkets / dataRlist_markets, search_symbols, data_coverage, data_fetch_start / getScreenerR/Bscreener_metrics_*, screener_presets_*, screener_scan_start / get (or wait=true)StrategyR/W/Bcompile_strategy_code, save_strategy_source, list_versions, create_strategy, submit_backtest, list_jobs, wait_for_job, stream_job_until_done, stop_strategyKnowledgeRResources honba://knowledge/{grammar,metrics,units,examples,rules}Colibri (optional)R / TRead: book, positions, orders. Write: place/cancel only with confirm + tokenEmergencyTemergency_stop (cancel agent-originated work, revoke agent capabilities)
Safety on mutations

Every W/B/N/T tool requires caller idempotency_key (reuse only for exact retry).
Live/order/stop require explicit confirm flags (confirm_order, confirm_live_trading, confirm_stop) mirroring QuantDinger.
Server-side budgets: max instruments fetched, provider calls, LLM calls per session → structured budget_exhausted.
Errors return typed payloads (code, message, parse caret, suggestions), not bare exceptions.

### 4.3 Skills (external agents)
Generated from the knowledge pack (not hand-written prose):
texthonba ai skills install --target claude|cursor|both

Public skill: grammar, catalog aliases, units, examples, CLI/MCP usage rules.
Private overlay (~/.config/honba/skills/, gitignored): user universes, risk notes.
Research commands: model-invocable.
Any future live/order skill: disable-model-invocation + requires --yes (HydraTrade pattern).

### 4.4 In-process LLM (strategy authoring)
Port: LlmPort.complete(messages, grammar|schema, seed, temperature=0) → str





















BackendRoleLocal (llama-cpp / Ollama / transformers)Default; grammar-constrained decodingRemote (litellm extras)Opt-inScripted fakeTests
Knowledge pack (versioned, content-hashed; drift-checked in CI like make check-schema):

Filter grammar (EBNF → GBNF + JSON Schema)
Metric catalog, presets, unit tables per market pack
Strategy API surface + golden examples
Short “what is / is not supported” notes

Strategy write loop

User/agent: free text or honba strategy ask "…".
LLM produces candidate Python (or English filters for screener).
Parser / AST allow-list sandbox validates (no network, no FS outside strategy dir, no dunder escapes).
compile → save_strategy_source → optional submit_backtest.
Deploy is explicit: paper first; live requires --yes and platform risk gates.
Journal: pack hash, model id, seed, raw output, final validated artifact.

Generated code is never auto-started live (OpenAlgo agent pattern).

### 5. Colibri integration
Role: Optional execution and market-structure bridge for discretionary/scalping; not a replacement for Honba data catalog or sim.
Adapter location: Prefer honba-adapters (or thin package consumed by control plane) using official Colibri Python SDK discovery (localapi.json).
Mapping

Connections / positions / orders / balances → Honba wire entities.
Book / trades / clusters as optional live feeds for research tools (clearly labeled “Colibri live”, not catalog history).

CLI / MCP

```text
honba colibri status
honba colibri book --exchange … --symbol … [--depth N]
honba colibri positions
honba colibri order … --yes --confirm-live   # token-gated
```

Write path: same confirmation and audit rules as any live Honba path. Colibri access token used only inside the adapter process; never logged or passed to the LLM.
Feature flag: configs / env HONBA_COLIBRI=1; absent Colibri → tools report unavailable, do not fail the whole MCP server.

### 6. Strategy host and jobs
Align with QuantDinger Strategy API V2 workflow and OpenAlgo process isolation:

Compile — Validate against Honba strategy contract (ADR 0008 parity).
Save source — Versioned library; restore requires confirm.
Create deployment — Stopped by default; params + capital + mode (signal | paper | live).
Backtest — Async job; manifest owns universe/timeframes, not ad-hoc agent guesses.
Runtime — Isolated processes; crash does not take down control plane.
Stop / emergency — Confirmed; cancels agent-originated work where possible.

Job API shape for MCP/CLI: *_start → {id} ; *_get → status + progress + result; wait / stream helpers with timeouts.

### 7. Safety invariants

Localhost-only listeners by default.
Credentials (broker, Colibri, agent tokens) never in prompts, logs, or skill files.
Read-only tools free for model invocation; mutations need idempotency + confirmations.
Risk guards after human/agent approval still apply (size, symbol allowlists, kill switch).
Analyzer/paper mode is a real fork: orders do not hit live venues.
Journals and audit rows for every mutation attempt (success or fail).
Budgets prevent runaway agent loops.
Knowledge pack and schema drift fail CI.


### 8. Implementation phases













































PhaseDeliverableDepends onP0Screener CLI per existing Design.md; --format json, explain, print-requestMetric catalog seedP1honba mcp serve (stdio); tools: health, screener metrics/scan, knowledge resourcesP0 servicesP2Knowledge pack generator + ai knowledge check + skill installersP0 grammar/catalogP3Strategy compile/save + backtest jobs over MCP/CLI; process-isolated hostADR 0008, engineP4In-process LlmPort + strategy ask / screener ask with constrained decodingP2P5Colibri adapter (read path → write path)OptionalP6Localhost streamable-http MCP + inbound token; optional remote laterP1

## 9. Testing

Unit: tool argument schemas; idempotency; confirm flag enforcement; sandbox rejects illegal AST; knowledge pack completeness/drift.
Integration: MCP stdio client against fake services; CLI CliRunner JSON parity with MCP; job poll lifecycle; Colibri adapter with mock discovery file.
Safety: live tools refused without flags; budget_exhausted; no credential leakage in redacted responses.
Eval: honba ai eval screener (and later strategy) on golden free-text → request pairs; real models marked slow.


## 10. Open questions

Single agent token model vs OpenAlgo-style API key + optional OAuth for remote MCP (defer remote).
Whether strategy code language is Python-only in v1 (recommended) or DSL + Python.
Shared job store (SQLite/Postgres) vs in-memory for local single-user v1.
Colibri multi-connection mapping when several exchange connections are active in the terminal.


## 11. References

OpenAlgo MCP & Agent docs: https://docs.openalgo.in/mcp , https://docs.openalgo.in/new-features/agent
QuantDinger MCP README: OpenByteInc/QuantDinger/mcp_server/README.md
HydraTrade agent: HydraLabs-RF/HydraTrade/agent
Colibri SDK: https://github.com/Colibriecosystem/colibri-sdk
Honba CLI design: crates/honba-cli/Design.md
Honba ADRs: doc/adr/0005–0008

text---

## Short ADR companion (drop-in)

**Path:** `honba/doc/adr/0009-agentic-mcp-and-local-llm-integration.md`

### ADR 0009: Agentic MCP and local LLM integration

- **Status:** Proposed
- **Date:** 2026-10-02
- **Deciders:** Honba core

### Context

Honba aims to be AI-native for Indian-market research. We need external agents and an optional in-process LLM to drive screening, data, backtests, and strategy authorship on a fully local install, without exposing broker credentials or inventing a second API.

Prior art: OpenAlgo (MCP + in-app agent + strategy host), QuantDinger (scoped MCP + job/confirm model), HydraTrade (skills + thin CLI), Colibri Local API (loopback execution).

## Decision

1. Expose **one** domain service layer via CLI and MCP; wire models remain the contract (ADR 0006).
2. Ship MCP as **stdio-first**, localhost HTTP optional with separate inbound auth; no public bind by default.
3. Generate **knowledge pack + skills** for external agents; use **grammar-constrained local LLM** for `ask` / strategy authoring.
4. Strategy code is validated and sandboxed; never auto-started live.
5. Optional **Colibri adapter** for read and gated write; credentials stay in the terminal/adapter.
6. Detailed design lives in `docs/design/agentic-integration.md`; CLI specifics remain in `crates/honba-cli/Design.md`.

## Consequences

- **Positive:** Agents and humans share contracts; local-first safety; clear phases; Colibri without coupling research to a GUI terminal.
- **Negative:** Two CLI entry points (Rust + Python) must stay conceptually aligned; MCP tool surface must be maintained as services evolve.
- **Follow-ups:** Implement phases P0–P4 in core repo; Colibri in adapters; publish user-facing MCP guide in `honba-docs` after P1.

Summary of placement

AArtifact,          Path                    
Decision,           honba/doc/adr/0009-agentic-mcp-and-local-llm-integration.md
Full design,        honba/docs/design/agentic-integration.md
CLI detail,         keep / extend honba/crates/honba-cli/Design.md 
                    (link §13 to the design doc)
Implementation,     "python/honba/ai/mcp, 
                    python/honba/ai/llm, 
                    strategy host under python/honba/"
Colibri,            honba-adapters (or optional package)
Public docs (later),honba-docs

