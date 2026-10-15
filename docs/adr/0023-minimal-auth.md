# ADR 0023: Minimal auth before non-loopback serve

Date: 2026-10-11. Status: **accepted**. Roadmap story: decides D11; implements E11-S8
("Auth, scopes, secrets, audit (minimum viable before non-loopback `serve`)",
`docs/ROADMAP.md:333`). Depends on E5-S3 (scope map), E11-S7 (write endpoints).

## Context

`honba serve` listens on `0.0.0.0` only when explicitly asked, and prints a warning that the
API has no auth or TLS (`crates/honba-cli/src/serve.rs:36-39`). To make non-loopback serving
safe, a minimal authentication and authorization layer is required before binding any non-loopback
address.

What already exists:
- **Scope map** (E5-S3, `python/src/honba/ai/mcp/tools.py:98`): scopes defined —
  `read:market`, `read:account`, `write:orders`, `research` (a fifth, `operator`, was added on
  2026-10-15). Every generated MCP tool carries a
  `scope` annotation. The `McpGateway` filters by scope (`python/src/honba/ai/mcp/handlers.py:99`).
- **Write endpoints** (E11-S7): `POST /orders`, `DELETE /orders/{id}`, `POST /positions/close`,
  `POST /approval/approve`, `POST /approval/reject` — currently open to anyone who can reach the
  port.
- **Error envelope** (`honba-messages::ErrorCode`): `Unauthorized`, `Forbidden`, `Auth` categories
  with stable codes (`crates/honba-messages/src/errors.rs`).
- **TradingState** (`honba-messages::TradingState`): `Active`, `Reducing`, `Halted` — the operator
  control plane; changing it is `PUT /trading-state` (scope `operator`).

What is missing:
- No token validation on incoming requests
- No scope enforcement on REST routes
- No secret store for token issuance/rotation
- No audit log of auth decisions (granted/denied, token id, route, scope)

The threat model is a local or LAN deployment where an operator runs `honba serve` and wants to
expose the API to internal tooling (MCP clients, dashboard, CI). The auth must be simple enough
to configure in a dev/CI context but strong enough to not be "security by obscurity".

## Decision

### 1. Bearer token authentication

- Tokens are opaque strings (hex-encoded, 32+ bytes entropy). No JWT, no asymmetric crypto,
  no introspection endpoint — a token **is** its metadata (scopes, expiry, label) looked up in the
  secret store.
- Transport: `Authorization: Bearer <token>` header on every request (standard RFC 6750).
- Missing/invalid/expired token → `401 Unauthorized` with code `unauthorized` (wire
  `ErrorCode::Unauthorized`).
- Valid token but scope mismatch → `403 Forbidden` with code `forbidden` (wire
  `ErrorCode::Forbidden`).

### 2. Scope enforcement per endpoint

| Endpoint | Required scope |
|---|---|
| `GET /capabilities`, `/health`, `/schema` | *(none)* — public metadata |
| `GET /instruments*`, `/quotes*`, `/bars*`, `/depth*`, `/screener/scan` | `read:market` |
| `GET /strategies`, `GET /strategies/verify` | `research` |
| `POST /strategies`, `POST /strategies/verify` | `research` |
| `GET /backtests*`, `GET /sweeps*`, `GET /journals*` | `research` |
| `POST /backtests`, `POST /sweeps` | `research` |
| `POST /orders`, `DELETE /orders/{id}`, `POST /positions/close` | `write:orders` |
| `GET /approval/queue`, `POST /approval/approve`, `POST /approval/reject` | `write:orders` |
| `GET /orders` (write ledger, E11-S7, currently 501) | `write:orders` |
| `POST /bars` | `write:orders` |
| `GET /trading-state`, `PUT /trading-state` | `operator` (see decision log 2026-10-15) |

`read:account` is reserved for future account/positions endpoints (not built yet).

`operator` is the fifth scope. It gates the routes that change or read the runtime control plane
(`GET`/`PUT /trading-state`), so a token that may place orders (`write:orders`) cannot halt
trading, and an operator token cannot place orders. Scopes are explicit: none implies another, so
a token that needs both lists both. An unlisted scope name never matches a route.

### 3. Secret store

A new `honba-api-rest` module `auth.rs` provides:
- `TokenStore` trait (so tests can inject an in-memory stub)
- `FileTokenStore` — reads a TOML file (`tokens.toml`) at startup; hot-reload on SIGHUP
- Token record: `token_id`, `token_hash` (SHA-256), `scopes` (list of scope strings), `label`,
  `expires_at` (optional RFC 3339), `created_at`
- The server never logs the raw token; logs only `token_id` (first 8 hex chars) and `label`
- `TokenStore::validate(token: &str) -> Result<TokenInfo, AuthError>` — computes SHA-256,
  looks up `token_hash`, checks expiry, returns `TokenInfo { token_id, scopes }`
- Default path: `--tokens-file` CLI flag (optional). If not provided, auth is **disabled**
  (loopback-only warning logic unchanged). When provided, auth is enforced on all non-exempt routes.

### 4. Audit log of auth decisions

Every auth decision writes one line to `audit.log` (append-only, NDJSON, same dir as
`--journals-dir` or `--tokens-file` dir) with:
```json
{
  "ts": "2026-10-11T12:34:56.789Z",
  "event": "auth.decision",
  "token_id": "a1b2c3d4",
  "route": "POST /orders",
  "method": "POST",
  "decision": "granted",  // or "denied"
  "reason": "scope_mismatch",  // only on denied: "missing_token", "invalid_token", "expired", "scope_mismatch"
  "required_scope": "write:orders",
  "token_scopes": ["read:market", "research"],
  "client_ip": "127.0.0.1"
}
```
The audit log is **not** the same as the run journal; it is a security log, rotation and
retention are out of scope for this ADR (handled by the operator).

### 5. Composition root changes

- `AppState` gains an optional `auth: Option<AuthLayer>` (where `AuthLayer` wraps a `TokenStore`).
- `ApiConfig` gains `tokens_file: Option<PathBuf>`.
- `api_router_with_config` wraps the router with an `AuthLayer` extractor when `tokens_file`
  is `Some`, otherwise passes through unchanged (so loopback dev without tokens still works).
- The extractor is an axum `FromRequestParts` that runs before the handler, validates the
  token, checks the required scope for the matched route, and on success inserts
  `TokenInfo` into request extensions for downstream use (e.g., `orders` handler can read
  `token_id` for audit correlation).

### 6. CLI changes

`honba serve` gains:
- `--tokens-file <path>`: enables auth, loads `FileTokenStore` from the TOML file
- The non-loopback warning is **suppressed** when auth is enabled (since auth is now present)

### 7. Token file format (TOML)

```toml
# tokens.toml — one table per token
[[tokens]]
token_id = "a1b2c3d4e5f6"
token_hash = "sha256:..."   # hex of SHA-256(token)
scopes = ["read:market", "research"]
label = "ci-backtest-runner"
expires_at = "2027-01-01T00:00:00Z"

[[tokens]]
token_id = "f0e1d2c3b4a5"
token_hash = "sha256:..."
scopes = ["read:market", "read:account", "write:orders", "research", "operator"]
label = "operator-cli"
# no expires_at = never expires
```

The raw token is never stored; the operator generates it (`openssl rand -hex 32`), puts the
hash in the file, and distributes the raw token to the client.

## Consequences

- `honba serve --addr 0.0.0.0:8080 --tokens-file tokens.toml` is now safe for non-loopback
  binding; the warning is suppressed.
- MCP stdio and in-process transports are unaffected (they bypass HTTP).
- The secret store is file-based and hot-reloadable (SIGHUP) so token rotation doesn't
  require a restart.
- No dependency on external auth systems (OAuth2, OIDC, LDAP); those can be layered on top
  by a reverse proxy if needed.
- The audit log gives the operator a tamper-evident trail of who (which token) did what.
- Write endpoints (`write:orders`) are protected by both the approval queue (E5-S4) and
  scope enforcement — defense in depth.

## Out of scope

| Item | Disposition |
|---|---|
| Token issuance API (`POST /auth/tokens`) | Deferred; file-based is sufficient for v1 |
| Token revocation list / blocklist | Deferred; short expiry + SIGHUP reload covers rotation |
| Rate limiting per token | Deferred; `RiskStage` rate rule covers order rate; auth rate limit is a separate concern |
| TLS / mTLS | Deferred; assumed to be terminated by a reverse proxy (nginx, Caddy) |
| `read:account` endpoints | Deferred until account/positions routes are built |
| Multi-factor / second factor on write | Deferred (roadmap mentions it); current design is single-factor bearer token |

## Known limits

- File-based store means all server instances must share the same `tokens.toml` (or a shared
  filesystem); horizontal scaling requires a shared store or external auth proxy.
- No token introspection endpoint; the client cannot query its own scopes (by design — the
  client knows what token it has).
- Audit log grows unbounded; operator must configure logrotate or similar.

## Decision log

- 2026-10-15: add the `operator` scope. `GET` and `PUT /trading-state` moved from `write:orders`
  (commit 4fa883a, a stopgap: no operator scope existed) to `operator`, so order placement and
  halting trading are separate grants. Both methods require `operator` (a read-only `read:account`
  fallback for `GET` was rejected to keep the map one scope per route). `operator` implies no other
  scope and `write:orders` no longer reaches the route. Existing `tokens.toml` files keep working;
  a token that used `PUT /trading-state` must be reissued with `operator`. Scope names in
  `tokens.toml` are free-form strings checked against route requirements, so no parser change was
  needed.

## References

- `docs/ROADMAP.md:333` (E11-S8), `:351` (D11), `:300` (E5-S3 scope map)
- `python/src/honba/ai/mcp/tools.py` (scope annotations on generated tools)
- `python/src/honba/ai/mcp/handlers.py` (McpGateway scope filter)
- `crates/honba-cli/src/serve.rs` (non-loopback warning)
- `crates/honba-api-rest/src/lib.rs` (router, ApiConfig)
- `crates/honba-api-rest/src/state.rs` (AppState)
- `crates/honba-api-rest/src/orders.rs` (write endpoints)
- `crates/honba-messages/src/errors.rs` (Unauthorized, Forbidden codes)