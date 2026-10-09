# Generic Broker API Architecture & Integration Plan

**Document:** `honba/docs/broker-apis.md`  
**Status:** Draft / Architectural Specification  
**Related Documents:** [ADR 0010: The Adapter Contract](adr/0010-adapter-contract.md), [Adapter Interface (Book)](../../honba-docs/book/src/architecture/adapter_interface.md)  
**External Reference Repositories:**
- **Zerodha Kite Connect (Rust):** [`https://github.com/zerodha/kiteconnect-rs`](https://github.com/zerodha/kiteconnect-rs)
- **DhanHQ (Python):** [`https://github.com/dhan-oss/DhanHQ-py`](https://github.com/dhan-oss/DhanHQ-py)

---

## 1. Executive Summary

Honba separates quantitative strategies and the execution kernel from external broker interfaces. Strategies run against a unified execution interface (`ExecutionPortLike` in Python, `honba-ports::ExecutionGateway` in Rust), remaining completely oblivious to whether orders are being filled by a historical backtest simulator (`NextOpenExecution`), an in-memory paper runner (`FakeAdapter`), or an external live broker.

This document defines:
1. **The Generic Broker API Architecture**: The polymorphic lifecycle, market data, and execution contracts shared across all broker integrations in both Python (`honba.adapters`) and Rust (`honba-ports`).
2. **Current System Audit**: An inventory of what currently exists across `honba`, `honba-adapters`, and `honba-ports`, and what remains stubbed.
3. **Specific Integration Plans**:
   - **Zerodha Kite Connect**: Leveraging `kiteconnect-rs` (Rust) and implementing `honba-adapters/zerodha` (Python).
   - **DhanHQ**: Leveraging `DhanHQ-py` (Python SDK) and implementing `honba-adapters/dhan`.
4. **Implementation Roadmap**: Phased milestones with concrete deliverables, testing harnesses, and verification gates.

---

## 2. Existing State vs. What Needs to Be Added

### 2.1 Audit of Existing Codebase

| Component | Location | Current State | What Needs to Be Added |
|---|---|---|---|
| **Adapter Core Architecture** | `honba/python/src/honba/adapters/` | **Complete (ADR 0010)**: `Adapter` ABC, `MarketDataAdapter`, `ExecutionAdapter` Protocols, `AdapterCapabilities`, canonical dataclasses in `models.py`, error hierarchy in `errors.py`, lazy `AdapterRegistry`. | Account-level push stream protocol (order/trade push events), connection health monitoring. |
| **Contract Verification Suite** | `honba/python/src/honba/adapters/contract.py` | **Complete**: `verify_adapter_contract()` tests lifecycle, capability gating, error translation, order lifecycle, idempotency. | Streaming quote/tick test harness, rate-limit recovery validation. |
| **Test Reference Implementation** | `honba/python/src/honba/adapters/testing.py` | **Complete**: `FakeAdapter` in-memory reference implementation passing the full contract suite. | Live broker recording/playback fixtures. |
| **Rust Port Abstractions** | `honba/crates/honba-ports/` | **Partial**: Synchronous kernel seam traits (`Clock`, `MarketDataFeed`, `ExecutionGateway`, `InstrumentMaster`, `DepthReader`, `QuoteReader`). | Async broker gateway trait implementation bindings, REST/WebSocket transport runtime. |
| **Shared Adapter Support** | `honba-adapters/shared/` | **Partial**: Re-exports contract test suite and basic test fixtures. `auth.py`, `rate_limit.py`, `websocket.py`, `parsing.py` are empty (0-byte stubs). | Shared token bucket rate limiter, TOTP 2FA generator, reconnecting WebSocket base client. |
| **Dhan Adapter Package** | `honba-adapters/dhan/` | **Stub (0-bytes)**: `honba_dhan` directory exists with 9 empty stub files (`http.py`, `execution.py`, `data.py`, `websocket.py`, etc.). | Full implementation wrapping `dhanhq` / Dhan REST & WebSocket APIs conforming to `Adapter`. |
| **Zerodha Adapter Package** | `honba-adapters/zerodha/` | **Stub (0-bytes)**: `honba_zerodha` directory exists with 9 empty stub files. | Full implementation wrapping Kite Connect v3 REST & KiteTicker WebSocket conforming to `Adapter`. |
| **Rust Zerodha Connector** | `honba/crates/` (target) | **Non-existent**: No Rust crate imports or bridges `kiteconnect-rs`. | Native Rust execution port wrapping `kiteconnect-rs` (or async Kite client) for zero-overhead execution. |

---

## 3. The Generic Broker API Specification

Honba defines broker adapters as a **facade plus two role protocols** (ADR 0010). A broker adapter never exposes proprietary SDK types; it translates broker-specific wire formats into canonical Honba domain entities at the process boundary.

```
                         ┌─────────────────────────────────┐
                         │      honba.adapters.base        │
                         │            Adapter              │
                         │    (Lifecycle & Capabilities)   │
                         └────────────────┬────────────────┘
                                          │
                 ┌────────────────────────┴────────────────────────┐
                 ▼                                                 ▼
   ┌───────────────────────────┐                     ┌───────────────────────────┐
   │     MarketDataAdapter     │                     │     ExecutionAdapter      │
   │  (Quotes, Depth, History, │                     │(Orders, Trades, Positions,│
   │      Instruments, Feed)   │                     │ Holdings, Funds, Margin)  │
   └─────────────┬─────────────┘                     └─────────────┬─────────────┘
                 │                                                 │
                 ▼                                                 ▼
   ┌───────────────────────────┐                     ┌───────────────────────────┐
   │   honba-adapters/dhan     │                     │   honba-adapters/zerodha  │
   │    (DhanHQ-py client)     │                     │ (kiteconnect-rs / REST+WS)│
   └───────────────────────────┘                     └───────────────────────────┘
```

### 3.1 Core Contracts

#### 1. Lifecycle Facade (`Adapter`)
```python
class Adapter(ABC):
    name: str  # e.g., "dhan", "zerodha"

    @abstractmethod
    def capabilities(self) -> AdapterCapabilities:
        """Declared offline capability descriptor."""
        ...

    @abstractmethod
    async def connect(self) -> SessionInfo:
        """Authenticates, opens transports, returns user/session metadata."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """Gracefully closes transports and releases network resources."""
        ...

    @abstractmethod
    def is_connected(self) -> bool:
        """Reports live readiness for network I/O."""
        ...
```

#### 2. Market Data Role (`MarketDataAdapter`)
```python
@runtime_checkable
class MarketDataAdapter(Protocol):
    async def instruments(self) -> list[Instrument]: ...
    async def search_instruments(self, query: str) -> list[Instrument]: ...
    async def quote(self, instrument_id: InstrumentId) -> QuoteTick: ...
    async def depth(self, instrument_id: InstrumentId, levels: int = 5) -> MarketDepth: ...
    async def historical_bars(
        self,
        instrument_id: InstrumentId,
        *,
        timeframe: str,
        start: dt.datetime,
        end: dt.datetime,
    ) -> list[Bar]: ...
    async def subscribe(
        self,
        instruments: Sequence[InstrumentId],
        *,
        mode: StreamMode,
        callback: StreamCallback,
    ) -> Subscription: ...
    async def unsubscribe(self, subscription_id: str) -> None: ...
```

#### 3. Execution Role (`ExecutionAdapter`)
```python
@runtime_checkable
class ExecutionAdapter(Protocol):
    async def place_order(
        self,
        intent: OrderIntent,
        *,
        product: Product,
        client_order_id: str | None = None,
    ) -> OrderReport: ...
    async def modify_order(
        self,
        order_id: str,
        *,
        quantity: float | None = None,
        price: float | None = None,
        trigger_price: float | None = None,
    ) -> OrderReport: ...
    async def cancel_order(self, order_id: str) -> OrderReport: ...
    async def cancel_all(
        self,
        *,
        instrument_id: InstrumentId | None = None,
        product: Product | None = None,
    ) -> list[OrderReport]: ...
    async def order_status(self, order_id: str) -> OrderReport: ...
    async def orders(self) -> list[OrderReport]: ...
    async def trades(self) -> list[Trade]: ...
    async def positions(self) -> list[Position]: ...
    async def holdings(self) -> list[Holding]: ...
    async def funds(self) -> Funds: ...
    async def margin(self, instrument_id: InstrumentId | None = None) -> MarginReport: ...
```

### 3.2 Canonical Data Models

Every adapter translates broker responses to frozen domain models:
- **`Product`**: `INTRADAY` (MIS), `DELIVERY` (CNC), `CARRY` (NRML).
- **`OrderReport`**: Carries `order_id`, `client_order_id`, `status` (`SUBMITTED`, `ACCEPTED`, `PARTIALLY_FILLED`, `FILLED`, `CANCELLED`, `REJECTED`), `filled_quantity`, `average_price`, `reject_reason`, `ts_event`.
- **`Funds`**: Carries `available_cash`, `opening_balance`, `margin_used`, `collateral`, `currency`.
- **`Holding`**: Long-term depository holdings with ISIN and T1 uncommitted quantities.
- **`MarketDepth`**: Standardized order book with `bids` and `asks` tuples up to requested depth.

### 3.3 Universal Rules & Guardrails
1. **Refusals are Data, Not Exceptions**: If a broker rejects an order (e.g. margin shortfall, invalid price, market closed), the adapter returns `OrderReport(status=OrderStatus.REJECTED, reject_reason="...")`. It must **never** raise an uncaught exception.
2. **Capability Pre-checks**: If a strategy invokes an unsupported action (e.g. asking for 20-level depth on a 5-level broker), the adapter raises `CapabilityError` *before* issuing any network request.
3. **Idempotent Identifiers**: `client_order_id` must be propagated to the broker's client reference tag to prevent duplicate fills on network timeouts.
4. **Strict Error Containment**: Only subclasses of `honba.adapters.errors.AdapterError` may cross the adapter boundary. Raw HTTP or SDK exceptions (`requests.HTTPError`, `urllib3.TimeoutError`) violate the contract.

---

## 4. Specific Integration: Zerodha Kite Connect

**Upstream Rust Crate:** [`zerodha/kiteconnect-rs`](https://github.com/zerodha/kiteconnect-rs)  
**API Specification:** Kite Connect v3 REST API & KiteTicker WebSocket protocol.

### 4.1 Overview of `kiteconnect-rs`
- **Features**: REST bindings for session handling, order placement/modification/cancellation, portfolio positions/holdings, margins, quotes, historical data, and `KiteTicker` binary WebSocket reader.
- **Architecture**: Synchronous HTTP client (uses `reqwest::blocking` in version 0.2) and binary WebSocket packet unpacker.
- **Data Protocols**:
  - Quotes/Depth: REST JSON endpoints.
  - Live Ticks: Custom binary protocol streaming packed binary structs (LTP: 8 bytes, Quote: 44 bytes, Full Depth: 184 bytes with 5 bid/ask levels in 2-byte little-endian integers and 4-byte integers for prices in paise).

### 4.2 Honba Zerodha Adapter Architecture

Honba will support Zerodha across two complementary tiers:
1. **Python Adapter (`honba-adapters/zerodha`)**: Full async implementation for research, multi-asset backtests, and standard live execution.
2. **Rust Native Port (`honba-broker-zerodha`)**: High-performance Rust crate linking `kiteconnect-rs` or an async tokio WebSocket parser directly into `honba-ports::ExecutionGateway`.

#### A. Authentication Flow
- **Parameters**: `api_key`, `api_secret`, `user_id`, `totp_secret`, `pin`.
- **Workflow**:
  1. Automated login: Send request to `https://kite.zerodha.com/api/login`, extract `request_token` using TOTP generated via RFC 6238 algorithm.
  2. Session exchange: `POST /session/token` with `checksum = SHA256(api_key + request_token + api_secret)` $\to$ returns `access_token` valid until 06:00 AM IST next day.
  3. Session token cached locally in secure keyring/file to avoid duplicate daily authentications.

#### B. Vocabulary & Mapping Tables

```python
ZERODHA_PRODUCT_MAP = {
    Product.DELIVERY: "CNC",
    Product.INTRADAY: "MIS",
    Product.CARRY: "NRML",
}

ZERODHA_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_LOSS: "SL",
    OrderType.STOP_LOSS_MARKET: "SL-M",
}

ZERODHA_STATUS_MAP = {
    "OPEN": OrderStatus.ACCEPTED,
    "TRIGGER PENDING": OrderStatus.ACCEPTED,
    "COMPLETE": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
}
```

#### C. Instrument Master Caching
- Zerodha provides daily master dumps via CSV at `https://api.kite.trade/instruments`.
- Downloaded at session connect and cached in SQLite / Parquet:
  - Key mapping: `(tradingsymbol, exchange) -> instrument_token` (e.g. `("RELIANCE", "NSE") -> 738561`).
  - Stores `lot_size`, `tick_size`, `segment`.

#### D. Binary WebSocket Feed (`KiteTicker`)
- KiteTicker pushes binary packets containing a 2-byte packet count header followed by fixed-length payloads:
  - Mode 1 (LTP, 8 bytes): `token` (4 bytes), `ltp` (4 bytes in paise).
  - Mode 2 (Quote, 44 bytes): `token`, `ltp`, `volume`, `high`, `low`, `open`, `close`.
  - Mode 3 (Full Depth, 184 bytes): Quote fields plus 5 levels of bid/ask (each level: 4-byte qty, 4-byte price, 2-byte orders).
- The adapter decodes packets without heap allocations and fires `StreamCallback` with `QuoteTick` or `MarketDepth`.

#### E. Rate Limits & Quotas
- Orders: Max 10 requests / second.
- Quotes: Max 10 requests / second (Kite v3).
- Historical Data: Max 3 requests / second.
- WebSocket: Max 3 concurrent connections per API key; max 3,000 instruments per connection.
- **Guard**: Implemented via a leaky bucket in `honba_adapters_shared/rate_limit.py`.

---

## 5. Specific Integration: DhanHQ

**Upstream Python SDK:** [`dhan-oss/DhanHQ-py`](https://github.com/dhan-oss/DhanHQ-py)  
**API Specification:** DhanHQ v2 REST API & Dhan Market Feed WebSocket.

### 5.1 Overview of `DhanHQ-py`
- **Features**: Python client `dhanhq` providing methods for order management, positions, trade book, holdings, margin limits, quotes, and market feeds.
- **Data Protocols**:
  - REST API: JSON endpoints over HTTPS (`api.dhan.co`).
  - Live Feeds: Binary WebSocket stream (`wss://api-feed.dhan.co`) supporting LTP (code 17), Quote (code 18), and Depth 20 levels (code 19).

### 5.2 Honba Dhan Adapter Architecture (`honba-adapters/dhan`)

#### A. Authentication Flow
- **Parameters**: `client_id`, `access_token`.
- **Workflow**:
  - Dhan provides static access tokens valid for 30 days via the Dhan developer portal, or dynamic 2FA via login credentials.
  - The adapter initializes `dhanhq(client_id, access_token)`.
  - On `connect()`, validates credentials by calling `get_fund_limits()` and constructing `SessionInfo`.

#### B. Vocabulary & Mapping Tables

```python
DHAN_PRODUCT_MAP = {
    Product.DELIVERY: "CNC",
    Product.INTRADAY: "INTRADAY",
    Product.CARRY: "MARGIN",  # or "MTF"
}

DHAN_ORDER_TYPE_MAP = {
    OrderType.MARKET: "MARKET",
    OrderType.LIMIT: "LIMIT",
    OrderType.STOP_LOSS: "STOP_LOSS",
    OrderType.STOP_LOSS_MARKET: "STOP_LOSS_MARKET",
}

DHAN_STATUS_MAP = {
    "TRANSIT": OrderStatus.SUBMITTED,
    "PENDING": OrderStatus.ACCEPTED,
    "TRADED": OrderStatus.FILLED,
    "CANCELLED": OrderStatus.CANCELLED,
    "REJECTED": OrderStatus.REJECTED,
    "EXPIRED": OrderStatus.EXPIRED,
}
```

#### C. Asynchronous Execution Bridge
- `dhanhq` is a synchronous library using `requests`.
- To preserve Honba's non-blocking async contract:
  - **Option 1 (Fast Wrapper)**: Dispatch `dhanhq` calls onto Tokio / asyncio thread pool via `asyncio.to_thread(dhan.place_order, ...)`.
  - **Option 2 (Direct Async Client - Recommended)**: Implement `honba_dhan/http.py` directly using `httpx.AsyncClient` targeting the Dhan v2 REST endpoints, using `dhanhq` data structures as reference. This eliminates GIL thread hops and provides sub-millisecond dispatch.

#### D. Security ID Resolution & Master Data
- Dhan identifies instruments via integer `security_id` and `exchange_segment`:
  - `("RELIANCE", "NSE")` $\leftrightarrow$ `security_id="1333"`, `exchange_segment="NSE_EQ"`.
- Daily master CSV downloaded from `https://images.dhan.co/api-data/api-scrip-master.csv`.
- Loaded into SQLite cache for $O(1)$ lookups between `InstrumentId` and Dhan's `security_id`.

#### E. Binary WebSocket Feed (20-Level Depth)
- Dhan's WebSocket feed (`wss://api-feed.dhan.co`) uses binary packet headers:
  - Feed Response Code 17: LTP ticker (8-byte header + 8-byte payload).
  - Feed Response Code 18: Quote (price, volume, OHLC, 5-level depth).
  - Feed Response Code 19: **20-level market depth** packet.
- Adapter maps packet 19 directly to Honba's `MarketDepth(levels=20)`.

#### F. Rate Limits
- Orders: 10 requests / second (burst up to 25 / second).
- Data Quotes: 5 requests / second.
- WebSocket: Max 5 connections per user; max 5,000 instruments per connection.

---

## 6. Detailed Comparison: Zerodha vs. Dhan

| Feature | Zerodha (Kite Connect) | Dhan (DhanHQ) | Honba Canonical Representation |
|---|---|---|---|
| **Primary Language** | Rust (`kiteconnect-rs`) & Python | Python (`dhanhq`) & REST | Async Python (`honba.adapters`) + Native Rust (`honba-ports`) |
| **Auth Expiry** | 24 Hours (daily TOTP re-auth) | 30 Days (static token) or TOTP | `SessionInfo.expires_at` |
| **Instrument Lookup** | `instrument_token` (uint32) | `security_id` (str) + `exchange_segment` | `InstrumentId(symbol, exchange)` |
| **Market Depth** | 5 levels | **Up to 20 levels** | `MarketDepth(bids, asks)` |
| **Order Placement Tag** | `tag` (max 8 alphanumeric chars) | `correlationId` (max 25 chars) | `client_order_id` (idempotency key) |
| **Margins API** | Segment-wise (`equity.available.cash`) | Combined limit (`availabelBalance`) | `Funds.available_cash`, `Funds.margin_used` |
| **Order Modification** | Price, Qty, Trigger, OrderType | Price, Qty, Trigger | `ExecutionAdapter.modify_order(...)` |
| **Historical Data** | Minute / Day bars | Minute / Day bars | `list[Bar]` |
| **Order Book Polling** | `GET /orders` | `GET /orders` | `ExecutionAdapter.orders()` |

---

## 7. Actionable Implementation Plan & Milestones

### Milestone 1: Shared Adapter Infrastructure (`honba-adapters/shared`)
1. Implement `honba_adapters_shared/rate_limit.py`:
   - Async Leaky Bucket / Token Bucket rate limiter with per-endpoint token pools.
2. Implement `honba_adapters_shared/auth.py`:
   - Standard RFC 6238 TOTP generator for headless broker logins.
   - Secure token persistence and auto-refresh mechanisms.
3. Implement `honba_adapters_shared/websocket.py`:
   - Reconnecting async WebSocket transport with exponential backoff, ping/pong heartbeat, and sequence gap detection.

### Milestone 2: Dhan Python Adapter (`honba-adapters/dhan`)
1. Implement `honba_dhan/constants.py` and `parsing.py`:
   - Enums and bidirectional mapper between Dhan dicts and Honba canonical types.
2. Implement `honba_dhan/instruments.py`:
   - Scrip master downloader, CSV parsing, SQLite caching, and `InstrumentId` resolver.
3. Implement `honba_dhan/http.py` and `execution.py`:
   - Async REST client conforming to `ExecutionAdapter` and `MarketDataAdapter`.
4. Implement `honba_dhan/websocket.py`:
   - Binary packet decoder for Dhan feed codes 17, 18, and 19.
5. **Certification**:
   - Run `verify_adapter_contract(DhanAdapter)` using recorded HTTP/WS mock responses.

### Milestone 3: Zerodha Python Adapter (`honba-adapters/zerodha`)
1. Implement `honba_zerodha/constants.py` and `parsing.py`:
   - Product, order type, and status mappings.
2. Implement `honba_zerodha/instruments.py`:
   - Daily instrument dump cache and token resolver.
3. Implement `honba_zerodha/http.py` and `execution.py`:
   - Async HTTP client conforming to `ExecutionAdapter`.
4. Implement `honba_zerodha/websocket.py`:
   - Binary packet unpacker for KiteTicker packets (8-byte LTP, 44-byte Quote, 184-byte Depth).
5. **Certification**:
   - Run `verify_adapter_contract(ZerodhaAdapter)` against mock fixtures.

### Milestone 4: Zerodha Native Rust Connector (`kiteconnect-rs` in `honba-ports`)
1. Add `crates/honba-broker-zerodha` in `honba`:
   - Integrate `kiteconnect-rs` dependency.
2. Implement `honba_ports::ExecutionGateway`:
   - Route `SubmitOrder`, `CancelOrder`, and `ModifyOrder` directly into Kite Connect REST API.
3. Implement `honba_ports::MarketDataFeed`:
   - Stream live KiteTicker binary ticks into `QuoteTick` channel for `honba-engine`.
4. Run Rust unit tests in `crates/honba-broker-zerodha/tests`.

### Milestone 5: End-to-End Paper Validation & Promotion
1. Verify with `honba-examples/basic/01_connect_dhan.py`:
   ```bash
   python basic/01_connect_dhan.py --adapter dhan --config client_id=... --config access_token=...
   python basic/01_connect_dhan.py --adapter zerodha --config api_key=... --config access_token=...
   ```
2. Verify with `honba-examples/basic/04_place_order_paper.py` pointing to sandbox credentials.
3. Promote certified adapters to production registry.
