//! Transport abstraction for the Kite REST client.
//!
//! The client speaks only to [`HttpTransport`], so it stays free of I/O and is tested with
//! in-memory fakes. `ReqwestTransport` (feature `net`) is the real implementation.

use async_trait::async_trait;

/// HTTP method used by the Kite API.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Method {
    /// GET.
    Get,
    /// POST (form encoded).
    Post,
    /// PUT (form encoded).
    Put,
    /// DELETE.
    Delete,
}

/// A request to send. `form` pairs are sent as an `application/x-www-form-urlencoded` body.
#[derive(Debug, Clone, PartialEq)]
pub struct HttpRequest {
    /// HTTP method.
    pub method: Method,
    /// Absolute URL.
    pub url: String,
    /// Request headers.
    pub headers: Vec<(String, String)>,
    /// Form body fields.
    pub form: Vec<(String, String)>,
}

/// A received response; the body is decoded as text.
#[derive(Debug, Clone, PartialEq)]
pub struct HttpResponse {
    /// HTTP status code.
    pub status: u16,
    /// Response body.
    pub body: String,
}

/// Failure to obtain any HTTP response.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[non_exhaustive]
pub enum TransportError {
    /// The request timed out.
    #[error("request timed out")]
    Timeout,
    /// The connection could not be established or broke.
    #[error("connection failed: {0}")]
    Connect(String),
}

/// Sends HTTP requests on behalf of the client.
#[async_trait]
pub trait HttpTransport: Send + Sync {
    /// Sends one request and returns the response, whatever its status.
    async fn send(&self, req: HttpRequest) -> Result<HttpResponse, TransportError>;
}

/// reqwest-backed transport (rustls) with configurable timeouts.
///
/// Not unit-tested: it needs the network. It is exercised only by compilation and clippy.
#[cfg(feature = "net")]
#[derive(Debug, Clone)]
pub struct ReqwestTransport {
    client: reqwest::Client,
}

#[cfg(feature = "net")]
impl ReqwestTransport {
    /// Builds a transport with the given connect and total request timeouts.
    pub fn new(
        connect_timeout: std::time::Duration,
        request_timeout: std::time::Duration,
    ) -> Result<Self, TransportError> {
        let client = reqwest::Client::builder()
            .connect_timeout(connect_timeout)
            .timeout(request_timeout)
            .build()
            .map_err(|e| TransportError::Connect(e.without_url().to_string()))?;
        Ok(Self { client })
    }
}

#[cfg(feature = "net")]
impl Default for ReqwestTransport {
    fn default() -> Self {
        Self {
            client: reqwest::Client::new(),
        }
    }
}

#[cfg(feature = "net")]
#[async_trait]
impl HttpTransport for ReqwestTransport {
    async fn send(&self, req: HttpRequest) -> Result<HttpResponse, TransportError> {
        let method = match req.method {
            Method::Get => reqwest::Method::GET,
            Method::Post => reqwest::Method::POST,
            Method::Put => reqwest::Method::PUT,
            Method::Delete => reqwest::Method::DELETE,
        };
        let mut builder = self.client.request(method, &req.url);
        for (k, v) in &req.headers {
            builder = builder.header(k.as_str(), v.as_str());
        }
        if !req.form.is_empty() {
            builder = builder.form(&req.form);
        }
        let map = |e: reqwest::Error| {
            if e.is_timeout() {
                TransportError::Timeout
            } else {
                TransportError::Connect(e.without_url().to_string())
            }
        };
        let resp = builder.send().await.map_err(map)?;
        let status = resp.status().as_u16();
        let body = resp.text().await.map_err(map)?;
        Ok(HttpResponse { status, body })
    }
}
