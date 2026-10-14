# ADR 0026: Webhook order ingestion

Date: 2026-10-14. Status: **accepted**. Roadmap story: implements E5-S6 ("Webhook ingestion",
`docs/archive/ROADMAP-borrowed-ideas.md:168,540`; previously DEFER in `docs/ROADMAP.md:188` because
its premise — hosted order ingestion — needed auth (E11-S8, ADR 0023) and the risk stage (E2-S2,
ADR 0018), both now shipped). Python-only surface, like the rest of the agent/API layer.

## Context

The only write paths that reach the engine today are local (the Python runner, the REST write
routes gated by ADR 0018/0023, and the MCP server). A broker or an external automation can only
push order updates by polling; there is no inbound HTTP endpoint that a broker webhook can call.

OpenAlgo's webhook handler is the model (Apache-labelled reference, behaviour only): it runs an
ordered pipeline rather than ad-hoc checks, hashes the URL token, caps the body, and dedupes
deliveries. Its known weaknesses (order of checks leaks whether a token exists; unbounded body;
no cool-off) are the things this ADR fixes.

The infrastructure this must reuse already exists:

- `honba.risk` (ADR 0018) — `TradingState` (`ACTIVE`/`REDUCING`/`HALTED`), `RiskStage.check`,
  `RiskDecision`/`RiskRefusal`. The kill switch is `TradingState::Halted`.
- `honba.api.auth` (ADR 0023) — SHA-256 token hashing, `TokenRecord.matches_token`, `AuthError`/
  `AuthErrorCode`, the NDJSON audit logger. Webhook tokens are the same kind of secret.
- `honba.domain.order.OrderIntent` — the canonical intent a strategy emits.
- The engine journal (`AuditLog` + `JournalV1`) records accepted events.

There is no webhook trait, stage list, token handling or TTL cache anywhere in the tree.

## Decision

### 1. Placement and shape

Python-only, in the existing `honba.api` package beside `auth.py`:
`python/src/honba/api/webhooks.py`. No Rust change: the risk stage, trading state and wire types are
already reachable from Python, and a hosted ingestion endpoint is an API-surface concern, not a
deterministic hot path.

Public surface:

```
WebhookConfig            # frozen: enabled, token_hashes, ip_allowlist, max_body_bytes,
                         #   idempotency_ttl_s, cool_off_ttl_s, cool_off_max, require_live;
                         #   WebhookConfig.from_dict reads a [webhooks] TOML table
WebhookRequest           # frozen: method, path, headers, body: bytes, client_ip
WebhookResponse          # frozen: status_code: int, body: dict  (JSON-able)
WebhookRefusal           # frozen: stage, code, message, context
WebhookStage             # StrEnum of the ordered stages below
WebhookIngestor          # .ingest(request) -> WebhookResponse
TtlCache                 # .check_and_set(key, value, now, ttl_s) -> value | None
```

`WebhookIngestor(config, *, risk_stage=None, journal=None, clock=time.monotonic)`.

### 2. Stage order (the whole design)

`ingest` runs an **ordered** pipeline and stops at the first refusal, so an early stage can never be
bypassed by a later one. The order is fixed and unit-tested:

1. **kill switch** — refuse when `config.enabled` is false or the injected `TradingState` is
   `HALTED`. (Operator stop outranks everything.)
2. **IP allowlist** — refuse when `config.ip_allowlist` is non-empty and `client_ip` is not in it.
3. **size cap** — refuse when `len(body) > config.max_body_bytes`, before any parse.
4. **token** — refuse unless a header token hashes (SHA-256, constant-time compare via
   `hmac.compare_digest`) to a configured `token_hashes` entry.
5. **parse** — the JSON body must decode and shape into an `OrderIntent`; malformed input refuses.
6. **live gate** — when `config.require_live`, refuse unless a live/paper session is active.
7. **idempotency** — a repeated `Idempotency-Key` inside `idempotency_ttl_s` returns the cached
   *terminal* `WebhookResponse` of the first delivery and does no further work. The key is written
   only after the risk/journal stages, so a delivery that is later refused caches the refusal, never
   a premature `202`.
8. **cool-off** — more than `cool_off_max` accepted deliveries from the same token/client inside
   `cool_off_ttl_s` refuse (rate limit).
9. **risk** — the `OrderIntent` is checked by an injected risk gate (the production binding is the
   `honba.risk` `RiskStage`; tests inject a fake so the unit layer stays native-free); a refusal is
   returned typed and audited (it never reaches the engine).
10. **journal** — an accepted order is handed to the journal sink and acknowledged.

### 3. Identical responses for unknown and malformed tokens (and IP)

Stages 1, 2 and 4 share **one** opaque refusal: HTTP 401 with the body
`{"status": "rejected"}`. An attacker cannot tell "no such token" from "bad token format" from "IP
not allowed", so the endpoint is not an oracle. The reason (stage/code) is written to the **audit
log** only, never to the response.

Later stages (size 413, parse 400, live gate 409, cool-off 429, risk 422) may answer specifically,
because by then the caller has already authenticated.

### 4. Hashed token, size cap, TTL caches

- Tokens are stored only as `sha256:<hex>` (config lists the hashes, never the plaintext), matching
  `honba.api.auth`. Comparison is constant-time and length-independent.
- `max_body_bytes` is checked on the raw bytes **before** `json.loads`, so an oversized body is
  rejected without being parsed.
- Idempotency and cool-off are the same `TtlCache` (a bounded dict of `(key -> (value, expiry))`
  driven by the injected **monotonic** clock). Tests inject a fake clock, so TTL behaviour is
  deterministic and no wall clock enters the domain.

### 5. Determinism and audit

The ingestor takes its clock as a parameter (default `time.monotonic`) and never reads the wall
clock in the decision path. Every decision — granted or refused — is emitted through the existing
`honba.api.auth.audit_log()` NDJSON logger with the stage, decision, token id (never the token) and
client IP, so a webhook refusal is visible next to auth decisions.

## Consequences

- Broker pushes become a supported write path, gated by the same risk stage and journal as every
  other order, with no new Rust surface.
- The pipeline is a plain ordered function, so new stages are appended and tested, not woven in.
- The ingest response is intentionally coarse for authentication failures; operators read the audit
  log for the specific reason.

## Acceptance (unit / integration)

Unit (`python/tests/unit/test_webhooks.py`):

- `stage_order_blocks_before_parse` — a HALTED kill switch refuses even a malformed, oversized body.
- `size_cap_rejects_oversize_body` — body over `max_body_bytes` refuses without parsing.
- `unknown_and_malformed_tokens_are_indistinguishable` — both yield the identical opaque response.
- `ip_allowlist_refuses_unlisted_client` — and shares the opaque response.
- `hashed_token_never_compared_in_plaintext` — only `sha256:` hashes are accepted as config.
- `idempotent_replay_returns_cached_response` — same `Idempotency-Key` returns the first response.
- `cool_off_limits_repeat_deliveries` — over `cool_off_max` in the window refuses.
- `ttl_expiry_is_deterministic_under_a_fake_clock`.

Integration (`python/tests/integration/test_webhook_ingest.py`):

- `webhook_order_passes_risk_and_journals` — an accepted order reaches the risk stage and is
  journaled.
- `webhook_risk_refusal_is_audited` — a risk refusal is returned typed and written to the audit log.

## Deferred

| Item | Disposition |
|---|---|
| REST route `POST /webhooks/{broker}` | Separate story; needs codegen + ADR 0013 route decision. This ADR defines the pipeline the route would call. |
| Signature/HMAC verification per broker | Not all Indian brokers sign webhooks; the hashed-URL-token model is the baseline. Add per-broker verifiers when a broker requires it. |
| Durable idempotency store | In-memory TTL cache first (matches ADR 0018's in-memory risk state); durable state is E2-S11-style follow-on. |
| Asynchronous queue / backpressure | Out of scope; the ingestor is a synchronous pure function returning a response. |

## Known limits

- The in-memory TTL caches are per-process; two server workers do not share idempotency or cool-off
  state. Acceptable until a durable store lands.
- IP allowlist trusts `client_ip` as supplied by the transport; a proxy must set a trusted forwarded
  header, and that wiring is the route's job, not this module's.
- The cool-off window is a fixed count, not a token bucket; it bounds bursts, it is not a fair
  scheduler.

## References

- `docs/archive/ROADMAP-borrowed-ideas.md:168` (E5-S6 scope), `:540` (stage order), `:188`
  (deferral premise), `docs/ROADMAP.md:188` (DEFER, now lifted).
- `docs/adr/0018-risk-stage.md` (risk stage, `TradingState`), `docs/adr/0023-minimal-auth.md`
  (hashed tokens, audit log), `docs/adr/0013-rest-read-ports.md` (route ownership).
- `python/src/honba/api/auth.py` (`TokenRecord.matches_token`, `audit_log`),
  `python/src/honba/risk.py` (`RiskStage`, `TradingState`),
  `python/src/honba/domain/order.py` (`OrderIntent`).
