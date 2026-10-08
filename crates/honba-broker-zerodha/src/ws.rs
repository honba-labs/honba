//! Websocket transport seam for KiteTicker.
//!
//! [`KiteFeed`](crate::KiteFeed) speaks only to [`TickerSocket`], so it stays free of I/O and is
//! tested with an in-memory fake. `TungsteniteSocket` (feature `net`) is the real implementation.

use async_trait::async_trait;

use crate::transport::TransportError;

/// A websocket message the ticker cares about. Ping/pong/close are handled by the socket.
#[derive(Debug, Clone, PartialEq, Eq)]
pub enum WsFrame {
    /// Binary market-data frame.
    Binary(Vec<u8>),
    /// Text frame (JSON postbacks and errors).
    Text(String),
}

/// A connected KiteTicker websocket.
#[async_trait]
pub trait TickerSocket: Send {
    /// Sends one text message (a JSON command).
    async fn send_text(&mut self, text: String) -> Result<(), TransportError>;

    /// Waits for the next data frame.
    ///
    /// `Ok(None)` means the peer closed the connection cleanly; `Err` means the connection broke.
    /// Control frames (ping/pong) are consumed transparently and never returned.
    async fn recv(&mut self) -> Result<Option<WsFrame>, TransportError>;
}

/// Builds the KiteTicker connection URL.
///
/// The URL embeds the access token: treat it as a secret, never log it, and never put it in
/// error text. Nothing in this crate formats it into a `Debug` or error string.
pub fn ticker_url(api_key: &str, access_token: &str) -> String {
    format!("wss://ws.kite.trade?api_key={api_key}&access_token={access_token}")
}

/// tokio-tungstenite-backed socket (rustls).
///
/// Not unit-tested: it needs the network. It is exercised only by compilation and clippy.
#[cfg(feature = "net")]
pub struct TungsteniteSocket {
    inner: tokio_tungstenite::WebSocketStream<
        tokio_tungstenite::MaybeTlsStream<tokio::net::TcpStream>,
    >,
}

#[cfg(feature = "net")]
impl std::fmt::Debug for TungsteniteSocket {
    fn fmt(&self, f: &mut std::fmt::Formatter<'_>) -> std::fmt::Result {
        f.debug_struct("TungsteniteSocket").finish_non_exhaustive()
    }
}

#[cfg(feature = "net")]
impl TungsteniteSocket {
    /// Connects to `url` (see [`ticker_url`]). Errors never include the URL.
    pub async fn connect(url: &str) -> Result<Self, TransportError> {
        let (inner, _) = tokio_tungstenite::connect_async(url)
            .await
            .map_err(|e| TransportError::Connect(describe(&e)))?;
        Ok(Self { inner })
    }
}

/// Describes a websocket error by category only, so URLs and tokens cannot leak.
#[cfg(feature = "net")]
fn describe(e: &tokio_tungstenite::tungstenite::Error) -> String {
    use tokio_tungstenite::tungstenite::Error as E;
    match e {
        E::Io(_) => "io error",
        E::Tls(_) => "tls error",
        E::Http(_) | E::HttpFormat(_) => "http handshake rejected",
        E::Url(_) => "invalid url",
        E::Protocol(_) | E::Utf8 => "protocol error",
        _ => "websocket error",
    }
    .to_owned()
}

#[cfg(feature = "net")]
#[async_trait]
impl TickerSocket for TungsteniteSocket {
    async fn send_text(&mut self, text: String) -> Result<(), TransportError> {
        use futures_util::SinkExt;
        use tokio_tungstenite::tungstenite::Message;
        self.inner
            .send(Message::Text(text.into()))
            .await
            .map_err(|e| TransportError::Connect(describe(&e)))
    }

    async fn recv(&mut self) -> Result<Option<WsFrame>, TransportError> {
        use futures_util::StreamExt;
        use tokio_tungstenite::tungstenite::Message;
        loop {
            match self.inner.next().await {
                None | Some(Ok(Message::Close(_))) => return Ok(None),
                Some(Ok(Message::Binary(b))) => return Ok(Some(WsFrame::Binary(b.to_vec()))),
                Some(Ok(Message::Text(t))) => return Ok(Some(WsFrame::Text(t.to_string()))),
                // Pings are answered by tungstenite on the next write/flush; pongs and raw
                // frames carry no data.
                Some(Ok(_)) => {}
                Some(Err(e)) => return Err(TransportError::Connect(describe(&e))),
            }
        }
    }
}
