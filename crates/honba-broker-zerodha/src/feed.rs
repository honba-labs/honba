//! [`MarketDataFeed`] over KiteTicker.

use std::collections::{HashSet, VecDeque};

use async_trait::async_trait;
use honba_messages::{InstrumentId, Message};
use honba_ports::{MarketDataFeed, PortError, PortResult};

use crate::gateway::NowFn;
use crate::ticker::{decode_frame, to_messages, Mode};
use crate::tokens::TokenMap;
use crate::transport::TransportError;
use crate::ws::{TickerSocket, WsFrame};

/// Kite allows at most this many instruments per websocket connection.
pub const MAX_SUBSCRIPTIONS: usize = 3000;

/// A [`MarketDataFeed`] backed by one KiteTicker websocket.
///
/// `next()` returns `Ok(None)` when the peer has closed the socket cleanly.
pub struct KiteFeed<S: TickerSocket> {
    socket: S,
    tokens: TokenMap,
    mode: Mode,
    now: NowFn,
    subscribed: HashSet<u32>,
    queue: VecDeque<Message>,
}

impl<S: TickerSocket> KiteFeed<S> {
    /// Creates a feed. `now` supplies `ts_init` for emitted messages.
    pub fn new(socket: S, tokens: TokenMap, mode: Mode, now: NowFn) -> Self {
        Self {
            socket,
            tokens,
            mode,
            now,
            subscribed: HashSet::new(),
            queue: VecDeque::new(),
        }
    }

    fn mode_name(&self) -> &'static str {
        match self.mode {
            Mode::Ltp => "ltp",
            Mode::Quote => "quote",
            Mode::Full => "full",
        }
    }

    async fn send(&mut self, value: serde_json::Value) -> PortResult<()> {
        self.socket
            .send_text(value.to_string())
            .await
            .map_err(map_transport)
    }

    fn handle_text(text: &str) -> PortResult<()> {
        let Ok(v) = serde_json::from_str::<serde_json::Value>(text) else {
            return Ok(());
        };
        if v.get("type").and_then(|t| t.as_str()) == Some("error") {
            let msg = v.get("data").and_then(|d| d.as_str()).unwrap_or("unknown");
            return Err(PortError::Transport(msg.to_owned()));
        }
        Ok(())
    }

    fn handle_binary(&mut self, bytes: &[u8]) -> PortResult<()> {
        let ticks = decode_frame(bytes)
            .map_err(|e| PortError::Internal(format!("ticker decode failed: {e}")))?;
        let ts_init = (self.now)();
        for tick in &ticks {
            if !self.subscribed.contains(&tick.token) {
                continue;
            }
            if let Some(inst) = self.tokens.instrument_for(tick.token) {
                self.queue.extend(to_messages(tick, inst, ts_init));
            }
        }
        Ok(())
    }
}

fn map_transport(e: TransportError) -> PortError {
    match e {
        TransportError::Timeout => PortError::Timeout,
        other => PortError::Transport(other.to_string()),
    }
}

#[async_trait]
impl<S: TickerSocket> MarketDataFeed for KiteFeed<S> {
    async fn subscribe(&mut self, symbols: &[InstrumentId]) -> PortResult<()> {
        let mut new: Vec<u32> = Vec::new();
        for sym in symbols {
            let token = self.tokens.token_for(sym).ok_or_else(|| {
                PortError::InvalidRequest(format!("unknown symbol {}", sym.symbol()))
            })?;
            if !self.subscribed.contains(&token) && !new.contains(&token) {
                new.push(token);
            }
        }
        if new.is_empty() {
            return Ok(());
        }
        if self.subscribed.len() + new.len() > MAX_SUBSCRIPTIONS {
            return Err(PortError::InvalidRequest(format!(
                "subscription exceeds the {MAX_SUBSCRIPTIONS} instrument limit per connection"
            )));
        }
        self.send(serde_json::json!({"a": "subscribe", "v": new}))
            .await?;
        let mode = self.mode_name();
        self.send(serde_json::json!({"a": "mode", "v": [mode, new]}))
            .await?;
        self.subscribed.extend(new);
        Ok(())
    }

    async fn unsubscribe(&mut self, symbols: &[InstrumentId]) -> PortResult<()> {
        let mut gone: Vec<u32> = Vec::new();
        for sym in symbols {
            if let Some(token) = self.tokens.token_for(sym) {
                if self.subscribed.contains(&token) && !gone.contains(&token) {
                    gone.push(token);
                }
            }
        }
        if gone.is_empty() {
            return Ok(());
        }
        self.send(serde_json::json!({"a": "unsubscribe", "v": gone}))
            .await?;
        for t in &gone {
            self.subscribed.remove(t);
        }
        Ok(())
    }

    async fn next(&mut self) -> PortResult<Option<Message>> {
        loop {
            if let Some(m) = self.queue.pop_front() {
                return Ok(Some(m));
            }
            match self.socket.recv().await.map_err(map_transport)? {
                None => return Ok(None),
                Some(WsFrame::Binary(b)) => self.handle_binary(&b)?,
                Some(WsFrame::Text(t)) => Self::handle_text(&t)?,
            }
        }
    }
}
