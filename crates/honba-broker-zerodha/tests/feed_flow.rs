//! Public-API flow: instruments csv -> token map -> KiteFeed over a scripted socket.
//!
//! Also replicates the `MarketDataFeed` contract assertions from
//! `honba-ports/tests/ports_contract.rs` (`feed_subscribes_then_drains_to_none`):
//! subscribe/unsubscribe succeed, the feed drains in order to `Ok(None)`, and stays exhausted
//! on repeated `next()`. Duplicate subscribe is not an error and does not duplicate deliveries;
//! unsubscribing an unsubscribed symbol is not an error.

use std::collections::VecDeque;
use std::sync::{Arc, Mutex};

use async_trait::async_trait;
use honba_broker_zerodha::ticker::Mode;
use honba_broker_zerodha::tokens::TokenMap;
use honba_broker_zerodha::transport::TransportError;
use honba_broker_zerodha::ws::{TickerSocket, WsFrame};
use honba_broker_zerodha::KiteFeed;
use honba_messages::{Event, Exchange, InstrumentId, UnixNanos};
use honba_ports::MarketDataFeed;

const CSV: &str = "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n\
2885633,11272,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE\n\
2953217,11536,TCS,TCS,0,,0,0.05,1,EQ,NSE,NSE\n";

struct ScriptedSocket {
    frames: VecDeque<WsFrame>,
    sent: Arc<Mutex<Vec<String>>>,
}

#[async_trait]
impl TickerSocket for ScriptedSocket {
    async fn send_text(&mut self, text: String) -> Result<(), TransportError> {
        self.sent.lock().unwrap().push(text);
        Ok(())
    }

    async fn recv(&mut self) -> Result<Option<WsFrame>, TransportError> {
        Ok(self.frames.pop_front())
    }
}

fn u32s(vals: &[u32]) -> Vec<u8> {
    vals.iter().flat_map(|v| v.to_be_bytes()).collect()
}

fn frame(packets: &[Vec<u8>]) -> Vec<u8> {
    let mut f = (packets.len() as u16).to_be_bytes().to_vec();
    for p in packets {
        f.extend_from_slice(&(p.len() as u16).to_be_bytes());
        f.extend_from_slice(p);
    }
    f
}

fn full_packet() -> Vec<u8> {
    let mut full = u32s(&[2_885_633, 250_050, 10, 250_000, 5000, 1, 2, 1, 2, 3, 4]);
    full.extend(u32s(&[0, 0, 0, 0, 1_700_000_000]));
    for i in 0..10u32 {
        full.extend(u32s(&[5, if i < 5 { 250_000 } else { 250_100 }]));
        full.extend_from_slice(&[0, 1, 0, 0]);
    }
    full
}

fn inst(sym: &str) -> InstrumentId {
    InstrumentId::new(sym, Exchange::new("NSE"))
}

fn feed(frames: Vec<WsFrame>) -> (KiteFeed<ScriptedSocket>, Arc<Mutex<Vec<String>>>) {
    let sent = Arc::new(Mutex::new(Vec::new()));
    let socket = ScriptedSocket {
        frames: frames.into(),
        sent: Arc::clone(&sent),
    };
    let map = TokenMap::from_instruments_csv(CSV).unwrap();
    let feed = KiteFeed::new(socket, map, Mode::Full, Box::new(|| UnixNanos::from_u64(9)));
    (feed, sent)
}

#[tokio::test]
async fn multi_packet_frame_yields_trade_then_quote_then_next_symbol() {
    let ltp = u32s(&[2_953_217, 410_000]);
    let (mut f, sent) = feed(vec![WsFrame::Binary(frame(&[full_packet(), ltp]))]);
    f.subscribe(&[inst("RELIANCE"), inst("TCS")]).await.unwrap();
    assert_eq!(sent.lock().unwrap().len(), 2);

    let m1 = f.next().await.unwrap().unwrap();
    let m2 = f.next().await.unwrap().unwrap();
    let m3 = f.next().await.unwrap().unwrap();
    assert!(matches!(m1.event(), Event::Trade(_)));
    assert!(matches!(m2.event(), Event::Quote(_)));
    let Event::Trade(t) = m3.event() else {
        panic!()
    };
    assert_eq!(t.instrument_id().symbol(), "TCS");
    assert!(f.next().await.unwrap().is_none());
}

#[tokio::test]
async fn duplicate_subscribe_does_not_duplicate_deliveries() {
    let ltp = u32s(&[2_953_217, 410_000]);
    let (mut f, sent) = feed(vec![WsFrame::Binary(frame(&[ltp]))]);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    f.subscribe(&[inst("TCS")]).await.unwrap();
    assert_eq!(
        sent.lock().unwrap().len(),
        2,
        "second subscribe sends nothing"
    );
    assert!(f.next().await.unwrap().is_some());
    assert!(f.next().await.unwrap().is_none());
}

#[tokio::test]
async fn unsubscribe_drops_later_ticks() {
    let ltp = u32s(&[2_953_217, 410_000]);
    let (mut f, _) = feed(vec![WsFrame::Binary(frame(&[ltp]))]);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    f.unsubscribe(&[inst("TCS")]).await.unwrap();
    f.unsubscribe(&[inst("TCS")]).await.unwrap();
    assert!(f.next().await.unwrap().is_none());
}

#[tokio::test]
async fn contract_subscribe_unsubscribe_then_drain_to_none() {
    let (mut f, _) = feed(vec![]);
    f.subscribe(&[inst("RELIANCE")]).await.unwrap();
    f.unsubscribe(&[inst("RELIANCE")]).await.unwrap();
    assert!(f.next().await.unwrap().is_none());
    assert!(f.next().await.unwrap().is_none(), "stays exhausted");
}
