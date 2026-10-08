//! Pure, I/O-free decoder for the KiteTicker (Kite Connect v3) binary websocket protocol.

use honba_messages::{
    AggressorSide, Event, InstrumentId, Message, QuoteTick, TradeId, TradeTick, UnixNanos,
};

/// Errors raised while decoding a ticker frame.
#[derive(Debug, Clone, PartialEq, Eq, thiserror::Error)]
#[non_exhaustive]
pub enum TickerError {
    /// The frame is truncated or its lengths overrun the buffer.
    #[error("malformed frame: {reason}")]
    Malformed {
        /// What was wrong.
        reason: &'static str,
    },
    /// A packet had a payload length the protocol does not define.
    #[error("unknown packet length {0}")]
    UnknownPacketLength(usize),
}

/// Exchange segment encoded in the low byte of the instrument token.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[non_exhaustive]
pub enum Segment {
    /// NSE cash.
    Nse,
    /// NSE F&O.
    Nfo,
    /// NSE currency derivatives.
    Cds,
    /// BSE cash.
    Bse,
    /// BSE F&O.
    Bfo,
    /// BSE currency derivatives.
    Bcd,
    /// MCX commodities.
    Mcx,
    /// MCX-SX.
    McxSx,
    /// Indices.
    Indices,
    /// Unrecognised segment code.
    Unknown(u8),
}

/// Subscription mode a packet was produced in.
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub enum Mode {
    /// Last traded price only.
    Ltp,
    /// Quote (OHLC, volume, quantities).
    Quote,
    /// Full (quote plus OI, timestamps and market depth).
    Full,
}

/// One market depth level.
#[derive(Debug, Clone, Copy, PartialEq)]
pub struct DepthLevel {
    /// Quantity at this level.
    pub qty: u32,
    /// Price at this level.
    pub price: f64,
    /// Number of orders at this level.
    pub orders: u16,
}

/// A decoded ticker packet.
#[derive(Debug, Clone, PartialEq)]
pub struct DecodedTick {
    /// Instrument token.
    pub token: u32,
    /// Segment derived from the token.
    pub segment: Segment,
    /// Mode of the packet.
    pub mode: Mode,
    /// Last traded price.
    pub ltp: f64,
    /// Last traded quantity, when present.
    pub last_qty: Option<u32>,
    /// Day volume, when present.
    pub volume: Option<u32>,
    /// Open, when present.
    pub open: Option<f64>,
    /// High, when present.
    pub high: Option<f64>,
    /// Low, when present.
    pub low: Option<f64>,
    /// Close, when present.
    pub close: Option<f64>,
    /// Bid depth, best first.
    pub bids: Vec<DepthLevel>,
    /// Ask depth, best first.
    pub asks: Vec<DepthLevel>,
    /// Exchange timestamp in unix seconds, when present.
    pub exchange_ts_secs: Option<u32>,
}

impl Segment {
    fn from_token(token: u32) -> Self {
        match (token & 0xff) as u8 {
            1 => Segment::Nse,
            2 => Segment::Nfo,
            3 => Segment::Cds,
            4 => Segment::Bse,
            5 => Segment::Bfo,
            6 => Segment::Bcd,
            7 => Segment::Mcx,
            8 => Segment::McxSx,
            9 => Segment::Indices,
            other => Segment::Unknown(other),
        }
    }

    fn divisor(self) -> f64 {
        match self {
            Segment::Cds => 10_000_000.0,
            Segment::Bcd => 10_000.0,
            _ => 100.0,
        }
    }
}

/// Big-endian reader over a packet payload; callers pre-check lengths.
struct Reader<'a> {
    buf: &'a [u8],
}

impl Reader<'_> {
    fn u32_at(&self, off: usize) -> u32 {
        u32::from_be_bytes([
            self.buf[off],
            self.buf[off + 1],
            self.buf[off + 2],
            self.buf[off + 3],
        ])
    }

    fn u16_at(&self, off: usize) -> u16 {
        u16::from_be_bytes([self.buf[off], self.buf[off + 1]])
    }
}

fn decode_packet(payload: &[u8]) -> Result<DecodedTick, TickerError> {
    let len = payload.len();
    if !matches!(len, 8 | 28 | 32 | 44 | 184) {
        return Err(TickerError::UnknownPacketLength(len));
    }
    let r = Reader { buf: payload };
    let token = r.u32_at(0);
    let segment = Segment::from_token(token);
    let div = segment.divisor();
    let price = |off: usize| f64::from(r.u32_at(off)) / div;
    let mut t = DecodedTick {
        token,
        segment,
        mode: Mode::Ltp,
        ltp: price(4),
        last_qty: None,
        volume: None,
        open: None,
        high: None,
        low: None,
        close: None,
        bids: Vec::new(),
        asks: Vec::new(),
        exchange_ts_secs: None,
    };
    match len {
        8 => {}
        28 | 32 => {
            t.mode = if len == 32 { Mode::Full } else { Mode::Quote };
            t.high = Some(price(8));
            t.low = Some(price(12));
            t.open = Some(price(16));
            t.close = Some(price(20));
            if len == 32 {
                t.exchange_ts_secs = Some(r.u32_at(28));
            }
        }
        _ => {
            t.mode = if len == 184 { Mode::Full } else { Mode::Quote };
            t.last_qty = Some(r.u32_at(8));
            t.volume = Some(r.u32_at(16));
            t.open = Some(price(28));
            t.high = Some(price(32));
            t.low = Some(price(36));
            t.close = Some(price(40));
            if len == 184 {
                t.exchange_ts_secs = Some(r.u32_at(60));
                for i in 0..10 {
                    let off = 64 + i * 12;
                    let level = DepthLevel {
                        qty: r.u32_at(off),
                        price: price(off + 4),
                        orders: r.u16_at(off + 8),
                    };
                    if i < 5 {
                        t.bids.push(level);
                    } else {
                        t.asks.push(level);
                    }
                }
            }
        }
    }
    Ok(t)
}

/// Decodes one websocket binary message into ticks.
///
/// A single-byte message is a heartbeat and yields no ticks. Truncated or overrunning frames
/// return [`TickerError::Malformed`]; a packet of undefined size returns
/// [`TickerError::UnknownPacketLength`]. Never panics on arbitrary input.
pub fn decode_frame(frame: &[u8]) -> Result<Vec<DecodedTick>, TickerError> {
    if frame.len() == 1 {
        return Ok(Vec::new());
    }
    if frame.len() < 2 {
        return Err(TickerError::Malformed {
            reason: "missing packet count",
        });
    }
    let count = usize::from(u16::from_be_bytes([frame[0], frame[1]]));
    let mut pos = 2usize;
    let mut out = Vec::with_capacity(count);
    for _ in 0..count {
        let hdr = frame.get(pos..pos + 2).ok_or(TickerError::Malformed {
            reason: "truncated packet length",
        })?;
        let len = usize::from(u16::from_be_bytes([hdr[0], hdr[1]]));
        pos += 2;
        let payload = frame.get(pos..pos + len).ok_or(TickerError::Malformed {
            reason: "packet overruns frame",
        })?;
        pos += len;
        out.push(decode_packet(payload)?);
    }
    Ok(out)
}

fn event_ts(tick: &DecodedTick, ts_init: UnixNanos) -> UnixNanos {
    tick.exchange_ts_secs.map_or(ts_init, |s| {
        UnixNanos::from_u64(u64::from(s) * 1_000_000_000)
    })
}

fn trade_message(
    tick: &DecodedTick,
    instrument: &InstrumentId,
    size: f64,
    discriminator: u64,
    ts_init: UnixNanos,
) -> Message {
    let ts_event = event_ts(tick, ts_init);
    let trade = TradeTick::new(
        instrument.clone(),
        tick.ltp,
        size,
        AggressorSide::NoAggressor,
        TradeId::new(format!(
            "{}-{}-{}",
            tick.token,
            ts_event.as_u64(),
            discriminator
        )),
        ts_event,
        ts_init,
    );
    Message::new(Event::Trade(trade), ts_init)
}

fn quote_message(
    tick: &DecodedTick,
    instrument: &InstrumentId,
    ts_init: UnixNanos,
) -> Option<Message> {
    if tick.mode != Mode::Full {
        return None;
    }
    let (b, a) = (tick.bids.first()?, tick.asks.first()?);
    if !(b.price > 0.0 && a.price > 0.0 && b.price <= a.price) {
        return None;
    }
    let quote = QuoteTick::new(
        instrument.clone(),
        b.price,
        a.price,
        f64::from(b.qty),
        f64::from(a.qty),
        event_ts(tick, ts_init),
        ts_init,
    );
    Some(Message::new(Event::Quote(quote), ts_init))
}

/// Converts a decoded packet into domain messages, statelessly.
///
/// Every packet yields a [`TradeTick`] at the last price (size is the last quantity when
/// present, else zero), so a repeated packet repeats the trade. Live feeds must use
/// [`TradeFilter`] instead, which only reports trades that actually happened. Full packets
/// with a valid best bid and ask additionally yield a [`QuoteTick`]. `ts_event` is the
/// exchange timestamp when present, else `ts_init`. The trade id is
/// `{token}-{ts_event}-{volume}` (volume `0` when the packet has none).
pub fn to_messages(
    tick: &DecodedTick,
    instrument: &InstrumentId,
    ts_init: UnixNanos,
) -> Vec<Message> {
    let mut out = vec![trade_message(
        tick,
        instrument,
        f64::from(tick.last_qty.unwrap_or(0)),
        u64::from(tick.volume.unwrap_or(0)),
        ts_init,
    )];
    out.extend(quote_message(tick, instrument, ts_init));
    out
}

#[derive(Debug, Clone, Copy, Default)]
struct TokenState {
    volume: Option<u32>,
    ltp: Option<f64>,
    seq: u64,
}

/// Stateful converter that reports a trade only when one demonstrably happened.
///
/// Kite re-sends the same snapshot whenever anything in the packet changes (for example depth),
/// so each packet is not a trade. Per token this tracks the last `(volume, ltp)`:
/// - the first packet is the baseline and emits no trade;
/// - with volume: a [`TradeTick`] only when volume strictly increased, sized by the delta (a
///   decrease, as after a day roll, only resets the baseline);
/// - without volume (LTP-only, indices): a [`TradeTick`] of size 0 only when the price changed.
///
/// Trade ids are `{token}-{ts_event}-{volume}` or, without volume, a per-token counter, so they
/// are unique per trade. A [`QuoteTick`] is still emitted for every Full packet with a valid best
/// bid and ask.
#[derive(Debug, Default)]
pub struct TradeFilter {
    state: std::collections::HashMap<u32, TokenState>,
}

impl TradeFilter {
    /// Creates an empty filter.
    pub fn new() -> Self {
        Self::default()
    }

    /// Converts `tick`, updating the per-token baseline.
    pub fn messages(
        &mut self,
        tick: &DecodedTick,
        instrument: &InstrumentId,
        ts_init: UnixNanos,
    ) -> Vec<Message> {
        let st = self.state.entry(tick.token).or_default();
        let is_baseline = st.volume.is_none() && st.ltp.is_none();
        let mut out = Vec::new();
        if !is_baseline {
            match (tick.volume, st.volume) {
                (Some(v), Some(prev)) if v > prev => out.push(trade_message(
                    tick,
                    instrument,
                    f64::from(v - prev),
                    u64::from(v),
                    ts_init,
                )),
                (None, _) if st.ltp != Some(tick.ltp) => {
                    st.seq += 1;
                    out.push(trade_message(tick, instrument, 0.0, st.seq, ts_init));
                }
                _ => {}
            }
        }
        if tick.volume.is_some() {
            st.volume = tick.volume;
        }
        st.ltp = Some(tick.ltp);
        out.extend(quote_message(tick, instrument, ts_init));
        out
    }
}
