//! Kite Connect v3 REST client over an abstract [`HttpTransport`].
//!
//! The client performs no I/O itself and never sleeps; pair it with
//! [`crate::rate_limit::RateLimiter`] at the call site.
//!
//! Error mapping to [`PortError`]:
//! - `TokenException` (HTTP 403): [`PortError::Rejected`] with `code` `"TokenException"`. The
//!   session expired; retrying unchanged will never help until a new session is generated, so it
//!   must not be reported as the retryable `Unavailable`. (Calling before any session exists is
//!   `Unavailable`: nothing was sent and the caller can authenticate then retry.)
//! - `InputException`, `OrderException`, `MarginException`, any other 4xx with a message:
//!   [`PortError::Rejected`] (`code` is the Kite `error_type`, or `HTTP_<status>`).
//! - HTTP 429 and 5xx (`NetworkException`, `GeneralException`): [`PortError::Transport`].
//! - Timeouts: [`PortError::Timeout`]. Undecodable bodies: [`PortError::Internal`], whose
//!   message never includes response bodies or credentials.

use honba_messages::{OrderSide, OrderType, TimeInForce};
use honba_ports::PortError;
use serde::de::DeserializeOwned;
use sha2::{Digest, Sha256};

use crate::mapping::{order_side_to_kite, order_type_to_kite, tif_to_kite, MappingError, Product};
use crate::transport::{HttpRequest, HttpResponse, HttpTransport, Method, TransportError};
use crate::wire::{Envelope, OrderIdData, OrderRecord, SessionData, TradeRecord};

const DEFAULT_BASE_URL: &str = "https://api.kite.trade";

/// Client configuration.
#[derive(Clone)]
pub struct KiteConfig {
    /// Kite API key.
    pub api_key: String,
    /// API base URL.
    pub base_url: String,
    /// Access token, set by [`KiteClient::generate_session`] or supplied up front.
    pub access_token: Option<String>,
}

impl KiteConfig {
    /// Config for `api_key` against the production API, without a session.
    pub fn new(api_key: impl Into<String>) -> Self {
        Self {
            api_key: api_key.into(),
            base_url: DEFAULT_BASE_URL.to_owned(),
            access_token: None,
        }
    }
}

impl std::fmt::Debug for KiteConfig {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("KiteConfig")
            .field("api_key", &self.api_key)
            .field("base_url", &self.base_url)
            .field(
                "access_token",
                &self.access_token.as_ref().map(|_| "<redacted>"),
            )
            .finish()
    }
}

/// An order placement request.
#[derive(Debug, Clone)]
pub struct PlaceOrder {
    /// Kite variety, for example `regular` or `amo`.
    pub variety: String,
    /// Trading symbol.
    pub tradingsymbol: String,
    /// Exchange code.
    pub exchange: String,
    /// Side.
    pub side: OrderSide,
    /// Order type.
    pub order_type: OrderType,
    /// Quantity.
    pub quantity: u64,
    /// Limit price; omitted from the request when `None`.
    pub price: Option<f64>,
    /// Trigger price; omitted from the request when `None`.
    pub trigger_price: Option<f64>,
    /// Validity.
    pub validity: TimeInForce,
    /// Product.
    pub product: Product,
    /// Tag, see [`crate::mapping::kite_tag`].
    pub tag: String,
}

/// Kite Connect REST client.
#[derive(Debug)]
pub struct KiteClient<T: HttpTransport> {
    config: KiteConfig,
    transport: T,
}

fn unsupported(e: MappingError) -> PortError {
    match e {
        MappingError::Unsupported(m) => PortError::Unsupported(m),
        other => PortError::InvalidRequest(other.to_string()),
    }
}

fn map_transport(e: TransportError) -> PortError {
    match e {
        TransportError::Timeout => PortError::Timeout,
        TransportError::Connect(m) => PortError::Transport(m),
    }
}

/// Maps a non-success HTTP response to a port error without echoing the raw body.
fn map_failure(resp: &HttpResponse) -> PortError {
    let env: Option<Envelope<serde_json::Value>> = serde_json::from_str(&resp.body).ok();
    let message = env.as_ref().and_then(|e| e.message.clone());
    let kind = env.as_ref().and_then(|e| e.error_type.clone());
    let shown = message
        .clone()
        .unwrap_or_else(|| format!("HTTP {}", resp.status));
    if kind.as_deref() == Some("TokenException") {
        return PortError::Rejected {
            code: "TokenException".to_owned(),
            message: format!("session expired or invalid: {shown}"),
        };
    }
    if resp.status == 429 {
        return PortError::Transport(format!("rate limited: {shown}"));
    }
    if resp.status >= 500 {
        return PortError::Transport(shown);
    }
    if (400..500).contains(&resp.status) {
        if let Some(message) = message {
            let code = kind.unwrap_or_else(|| format!("HTTP_{}", resp.status));
            return PortError::Rejected { code, message };
        }
    }
    PortError::Internal(format!("undecodable response (HTTP {})", resp.status))
}

fn decode<D: DeserializeOwned>(resp: &HttpResponse) -> Result<D, PortError> {
    let undecodable =
        || PortError::Internal(format!("undecodable response (HTTP {})", resp.status));
    if !(200..300).contains(&resp.status) {
        return Err(map_failure(resp));
    }
    let env: Envelope<D> = serde_json::from_str(&resp.body).map_err(|_| undecodable())?;
    if !env.is_success() {
        return Err(map_failure(resp));
    }
    env.data.ok_or_else(undecodable)
}

impl<T: HttpTransport> KiteClient<T> {
    /// Creates a client.
    pub fn new(config: KiteConfig, transport: T) -> Self {
        Self { config, transport }
    }

    /// The current access token, if a session exists.
    pub fn access_token(&self) -> Option<&str> {
        self.config.access_token.as_deref()
    }

    /// The underlying transport.
    pub fn transport(&self) -> &T {
        &self.transport
    }

    fn url(&self, path: &str) -> String {
        format!("{}{}", self.config.base_url.trim_end_matches('/'), path)
    }

    fn authed(
        &self,
        method: Method,
        path: &str,
        form: Vec<(String, String)>,
    ) -> Result<HttpRequest, PortError> {
        let token = self.config.access_token.as_deref().ok_or_else(|| {
            PortError::Unavailable("no Kite session; call generate_session".into())
        })?;
        Ok(HttpRequest {
            method,
            url: self.url(path),
            headers: vec![
                ("X-Kite-Version".to_owned(), "3".to_owned()),
                (
                    "Authorization".to_owned(),
                    format!("token {}:{}", self.config.api_key, token),
                ),
            ],
            form,
        })
    }

    async fn send(&self, req: HttpRequest) -> Result<HttpResponse, PortError> {
        self.transport.send(req).await.map_err(map_transport)
    }

    async fn call<D: DeserializeOwned>(&self, req: HttpRequest) -> Result<D, PortError> {
        decode(&self.send(req).await?)
    }

    /// Exchanges a `request_token` for an access token, stores it and returns the user id.
    pub async fn generate_session(
        &mut self,
        request_token: &str,
        api_secret: &str,
    ) -> Result<String, PortError> {
        let mut hasher = Sha256::new();
        hasher.update(self.config.api_key.as_bytes());
        hasher.update(request_token.as_bytes());
        hasher.update(api_secret.as_bytes());
        let checksum: String = hasher
            .finalize()
            .iter()
            .map(|b| format!("{b:02x}"))
            .collect();
        let req = HttpRequest {
            method: Method::Post,
            url: self.url("/session/token"),
            headers: vec![("X-Kite-Version".to_owned(), "3".to_owned())],
            form: vec![
                ("api_key".to_owned(), self.config.api_key.clone()),
                ("request_token".to_owned(), request_token.to_owned()),
                ("checksum".to_owned(), checksum),
            ],
        };
        let session: SessionData = self.call(req).await?;
        self.config.access_token = Some(session.access_token);
        Ok(session.user_id)
    }

    /// Places an order and returns the venue order id.
    pub async fn place_order(&self, order: &PlaceOrder) -> Result<String, PortError> {
        let mut form = vec![
            ("tradingsymbol".to_owned(), order.tradingsymbol.clone()),
            ("exchange".to_owned(), order.exchange.clone()),
            (
                "transaction_type".to_owned(),
                order_side_to_kite(order.side)
                    .map_err(unsupported)?
                    .to_owned(),
            ),
            (
                "order_type".to_owned(),
                order_type_to_kite(order.order_type)
                    .map_err(unsupported)?
                    .to_owned(),
            ),
            ("quantity".to_owned(), order.quantity.to_string()),
            (
                "validity".to_owned(),
                tif_to_kite(order.validity).map_err(unsupported)?.to_owned(),
            ),
            ("product".to_owned(), order.product.as_str().to_owned()),
            ("tag".to_owned(), order.tag.clone()),
        ];
        if let Some(p) = order.price {
            form.push(("price".to_owned(), p.to_string()));
        }
        if let Some(p) = order.trigger_price {
            form.push(("trigger_price".to_owned(), p.to_string()));
        }
        let req = self.authed(Method::Post, &format!("/orders/{}", order.variety), form)?;
        Ok(self.call::<OrderIdData>(req).await?.order_id)
    }

    /// Modifies an open order and returns its order id.
    pub async fn modify_order(
        &self,
        variety: &str,
        order_id: &str,
        quantity: Option<u64>,
        price: Option<f64>,
        trigger_price: Option<f64>,
    ) -> Result<String, PortError> {
        let mut form = Vec::new();
        if let Some(q) = quantity {
            form.push(("quantity".to_owned(), q.to_string()));
        }
        if let Some(p) = price {
            form.push(("price".to_owned(), p.to_string()));
        }
        if let Some(p) = trigger_price {
            form.push(("trigger_price".to_owned(), p.to_string()));
        }
        let req = self.authed(Method::Put, &format!("/orders/{variety}/{order_id}"), form)?;
        Ok(self.call::<OrderIdData>(req).await?.order_id)
    }

    /// Cancels an order and returns its order id.
    pub async fn cancel_order(&self, variety: &str, order_id: &str) -> Result<String, PortError> {
        let req = self.authed(
            Method::Delete,
            &format!("/orders/{variety}/{order_id}"),
            vec![],
        )?;
        Ok(self.call::<OrderIdData>(req).await?.order_id)
    }

    /// All orders of the day.
    pub async fn orders(&self) -> Result<Vec<OrderRecord>, PortError> {
        self.call(self.authed(Method::Get, "/orders", vec![])?)
            .await
    }

    /// State history of one order.
    pub async fn order_history(&self, order_id: &str) -> Result<Vec<OrderRecord>, PortError> {
        self.call(self.authed(Method::Get, &format!("/orders/{order_id}"), vec![])?)
            .await
    }

    /// All trades of the day.
    pub async fn trades(&self) -> Result<Vec<TradeRecord>, PortError> {
        self.call(self.authed(Method::Get, "/trades", vec![])?)
            .await
    }

    /// The instruments dump as raw CSV (no JSON envelope), optionally for one exchange.
    pub async fn instruments_csv(&self, exchange: Option<&str>) -> Result<String, PortError> {
        let path = match exchange {
            Some(e) => format!("/instruments/{e}"),
            None => "/instruments".to_owned(),
        };
        let resp = self.send(self.authed(Method::Get, &path, vec![])?).await?;
        if (200..300).contains(&resp.status) {
            Ok(resp.body)
        } else {
            Err(map_failure(&resp))
        }
    }
}
