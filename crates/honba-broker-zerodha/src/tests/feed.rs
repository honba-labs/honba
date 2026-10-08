use honba_messages::{Event, Exchange, InstrumentId, UnixNanos};
use honba_ports::{MarketDataFeed, PortError};

use super::{frame, u32be, FakeSocket};
use crate::feed::KiteFeed;
use crate::ticker::Mode;
use crate::tokens::TokenMap;
use crate::transport::TransportError;
use crate::ws::WsFrame;

const REL: u32 = 408_065 * 256 + 1;
const TCS: u32 = 2_953_217;

fn inst(sym: &str) -> InstrumentId {
    InstrumentId::new(sym, Exchange::new("NSE"))
}

fn map() -> TokenMap {
    let mut m = TokenMap::new();
    m.insert(REL, inst("RELIANCE"));
    m.insert(TCS, inst("TCS"));
    m
}

fn ltp(token: u32, price: u32) -> Vec<u8> {
    let mut p = Vec::new();
    u32be(&mut p, token);
    u32be(&mut p, price);
    p
}

type Script = Vec<Result<Option<WsFrame>, TransportError>>;

fn feed_with(
    script: Script,
    mode: Mode,
) -> (
    KiteFeed<FakeSocket>,
    std::sync::Arc<std::sync::Mutex<Vec<String>>>,
) {
    let (sock, sent) = FakeSocket::new(script);
    let feed = KiteFeed::new(sock, map(), mode, Box::new(|| UnixNanos::from_u64(5)));
    (feed, sent)
}

fn bin(packets: &[Vec<u8>]) -> Result<Option<WsFrame>, TransportError> {
    Ok(Some(WsFrame::Binary(frame(packets))))
}

fn text(t: &str) -> Result<Option<WsFrame>, TransportError> {
    Ok(Some(WsFrame::Text(t.to_owned())))
}

fn sent(h: &std::sync::Arc<std::sync::Mutex<Vec<String>>>) -> Vec<String> {
    h.lock().unwrap().clone()
}

#[tokio::test]
async fn subscribe_sends_subscribe_then_mode() {
    let (mut f, s) = feed_with(vec![], Mode::Quote);
    f.subscribe(&[inst("RELIANCE"), inst("TCS")]).await.unwrap();
    assert_eq!(
        sent(&s),
        vec![
            format!(r#"{{"a":"subscribe","v":[{REL},{TCS}]}}"#),
            format!(r#"{{"a":"mode","v":["quote",[{REL},{TCS}]]}}"#),
        ]
    );
}

#[tokio::test]
async fn mode_names() {
    for (m, name) in [(Mode::Ltp, "ltp"), (Mode::Full, "full")] {
        let (mut f, s) = feed_with(vec![], m);
        f.subscribe(&[inst("TCS")]).await.unwrap();
        assert!(sent(&s)[1].contains(&format!(r#""{name}""#)));
    }
}

#[tokio::test]
async fn unknown_symbol_is_invalid_request_and_sends_nothing() {
    let (mut f, s) = feed_with(vec![], Mode::Ltp);
    let err = f.subscribe(&[inst("TCS"), inst("NOPE")]).await.unwrap_err();
    assert!(matches!(&err, PortError::InvalidRequest(m) if m.contains("NOPE")));
    assert!(sent(&s).is_empty());
}

#[tokio::test]
async fn resubscribe_sends_only_new_tokens_and_empty_sends_nothing() {
    let (mut f, s) = feed_with(vec![], Mode::Ltp);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    s.lock().unwrap().clear();
    f.subscribe(&[inst("TCS")]).await.unwrap();
    assert!(sent(&s).is_empty());
    f.subscribe(&[inst("TCS"), inst("RELIANCE"), inst("RELIANCE")])
        .await
        .unwrap();
    assert_eq!(sent(&s)[0], format!(r#"{{"a":"subscribe","v":[{REL}]}}"#));
}

#[tokio::test]
async fn subscribe_over_cap_is_invalid_request() {
    let mut m = TokenMap::new();
    let syms: Vec<InstrumentId> = (0..3001u32)
        .map(|i| {
            let id = inst(&format!("S{i}"));
            m.insert(i + 1, id.clone());
            id
        })
        .collect();
    let (sock, sent) = FakeSocket::new(vec![]);
    let mut f = KiteFeed::new(sock, m, Mode::Ltp, Box::new(|| UnixNanos::from_u64(1)));
    f.subscribe(&syms[..3000]).await.unwrap();
    let before = sent.lock().unwrap().len();
    let err = f.subscribe(&syms[3000..]).await.unwrap_err();
    assert!(matches!(err, PortError::InvalidRequest(_)));
    assert_eq!(sent.lock().unwrap().len(), before);
}

#[tokio::test]
async fn unsubscribe_sends_only_subscribed_tokens() {
    let (mut f, s) = feed_with(vec![], Mode::Ltp);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    s.lock().unwrap().clear();
    f.unsubscribe(&[inst("TCS"), inst("RELIANCE"), inst("NOPE")])
        .await
        .unwrap();
    assert_eq!(
        sent(&s),
        vec![format!(r#"{{"a":"unsubscribe","v":[{TCS}]}}"#)]
    );
    f.unsubscribe(&[inst("TCS")]).await.unwrap();
    assert_eq!(sent(&s).len(), 1);
}

#[tokio::test]
async fn next_decodes_queues_and_drops_unsubscribed_ticks() {
    let script = vec![
        bin(&[ltp(TCS, 400_000), ltp(REL, 250_000)]),
        bin(&[ltp(TCS, 410_000), ltp(REL, 251_000)]),
    ];
    let (mut f, _) = feed_with(script, Mode::Ltp);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    let m = f.next().await.unwrap().unwrap();
    let Event::Trade(t) = m.event() else { panic!() };
    assert_eq!(t.instrument_id().symbol(), "TCS");
    assert_eq!(t.price(), 4100.0);
    assert_eq!(
        f.next().await.unwrap_err(),
        PortError::Transport("ticker connection closed".into()),
        "REL dropped, then closed"
    );
}

#[tokio::test]
async fn next_skips_heartbeats_and_ignored_text() {
    let script = vec![
        Ok(Some(WsFrame::Binary(vec![0]))),
        text(r#"{"type":"order","data":{}}"#),
        text("not json"),
        bin(&[ltp(TCS, 100)]),
        bin(&[ltp(TCS, 200)]),
    ];
    let (mut f, _) = feed_with(script, Mode::Ltp);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    assert!(f.next().await.unwrap().is_some());
}

#[tokio::test]
async fn text_error_is_transport_error() {
    let (mut f, _) = feed_with(
        vec![text(r#"{"type":"error","data":"token expired"}"#)],
        Mode::Ltp,
    );
    let err = f.next().await.unwrap_err();
    assert_eq!(err, PortError::Transport("token expired".into()));
}

#[tokio::test]
async fn malformed_frame_is_internal_without_payload() {
    let (mut f, _) = feed_with(
        vec![Ok(Some(WsFrame::Binary(vec![0, 5, 0xAB, 0xCD])))],
        Mode::Ltp,
    );
    let err = f.next().await.unwrap_err();
    let PortError::Internal(m) = err else {
        panic!()
    };
    assert!(!m.to_lowercase().contains("abcd") && !m.contains("171"));
}

#[tokio::test]
async fn transport_errors_map() {
    let (mut f, _) = feed_with(vec![Err(TransportError::Timeout)], Mode::Ltp);
    assert_eq!(f.next().await.unwrap_err(), PortError::Timeout);
    let (mut f, _) = feed_with(vec![Err(TransportError::Connect("boom".into()))], Mode::Ltp);
    assert!(matches!(
        f.next().await.unwrap_err(),
        PortError::Transport(_)
    ));
}

#[tokio::test]
async fn closed_socket_is_transport_error() {
    let (mut f, _) = feed_with(vec![], Mode::Ltp);
    let closed = PortError::Transport("ticker connection closed".into());
    assert_eq!(f.next().await.unwrap_err(), closed);
    assert_eq!(f.next().await.unwrap_err(), closed, "stays closed");
}

#[tokio::test]
async fn queued_messages_drain_before_closed_error() {
    let script = vec![bin(&[ltp(TCS, 100)]), bin(&[ltp(TCS, 200)])];
    let (mut f, _) = feed_with(script, Mode::Ltp);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    assert!(f.next().await.unwrap().is_some());
    assert_eq!(
        f.next().await.unwrap_err(),
        PortError::Transport("ticker connection closed".into())
    );
}

fn feed_failing_sends(
    script: Script,
    failures: Vec<bool>,
) -> (
    KiteFeed<FakeSocket>,
    std::sync::Arc<std::sync::Mutex<Vec<String>>>,
) {
    let (sock, sent) = FakeSocket::new(script);
    let sock = sock.with_send_failures(failures);
    let feed = KiteFeed::new(sock, map(), Mode::Ltp, Box::new(|| UnixNanos::from_u64(5)));
    (feed, sent)
}

#[tokio::test]
async fn subscribe_send_failure_leaves_nothing_subscribed_and_retry_resends_all() {
    let script = vec![bin(&[ltp(TCS, 100)]), bin(&[ltp(TCS, 200)])];
    let (mut f, s) = feed_failing_sends(script, vec![true]);
    assert!(f.subscribe(&[inst("TCS")]).await.is_err());
    assert!(sent(&s).is_empty());
    f.subscribe(&[inst("TCS")]).await.unwrap();
    assert_eq!(
        sent(&s),
        vec![
            format!(r#"{{"a":"subscribe","v":[{TCS}]}}"#),
            format!(r#"{{"a":"mode","v":["ltp",[{TCS}]]}}"#),
        ]
    );
}

#[tokio::test]
async fn mode_send_failure_keeps_tokens_subscribed_and_retry_resends_only_mode() {
    let script = vec![bin(&[ltp(TCS, 100)]), bin(&[ltp(TCS, 200)])];
    let (mut f, s) = feed_failing_sends(script, vec![false, true]);
    assert!(f.subscribe(&[inst("TCS")]).await.is_err());
    assert_eq!(
        sent(&s),
        vec![format!(r#"{{"a":"subscribe","v":[{TCS}]}}"#)]
    );
    s.lock().unwrap().clear();
    // Retry: server already has the token, only the mode is outstanding.
    f.subscribe(&[inst("TCS")]).await.unwrap();
    assert_eq!(
        sent(&s),
        vec![format!(r#"{{"a":"mode","v":["ltp",[{TCS}]]}}"#)]
    );
    s.lock().unwrap().clear();
    // Mode is no longer pending: a further subscribe sends nothing.
    f.subscribe(&[inst("TCS")]).await.unwrap();
    assert!(sent(&s).is_empty());
    // The token counts as subscribed, so ticks flow (baseline then a change).
    assert!(f.next().await.unwrap().is_some());
}

fn quote44(token: u32, ltp: u32, last_qty: u32, volume: u32) -> Vec<u8> {
    let mut p = Vec::new();
    for v in [token, ltp, last_qty, 0, volume, 0, 0, 0, 0, 0, 0] {
        u32be(&mut p, v);
    }
    p
}

fn trade_of(m: &honba_messages::Message) -> (f64, f64, String) {
    let Event::Trade(t) = m.event() else {
        panic!("expected trade")
    };
    (t.price(), t.size(), t.trade_id().as_str().to_owned())
}

#[tokio::test]
async fn volume_packets_emit_trade_only_when_volume_increases() {
    let script = vec![
        bin(&[quote44(TCS, 100, 7, 1000)]),
        bin(&[quote44(TCS, 100, 7, 1000)]),
        bin(&[quote44(TCS, 101, 3, 1005)]),
        bin(&[quote44(TCS, 102, 4, 1012)]),
    ];
    let (mut f, _) = feed_with(script, Mode::Quote);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    let a = trade_of(&f.next().await.unwrap().unwrap());
    let b = trade_of(&f.next().await.unwrap().unwrap());
    assert_eq!((a.0, a.1), (1.01, 5.0), "size is the volume delta");
    assert_eq!((b.0, b.1), (1.02, 7.0));
    assert_ne!(a.2, b.2, "trade ids unique");
    assert!(
        a.2.ends_with("-1005") && b.2.ends_with("-1012"),
        "{a:?} {b:?}"
    );
    assert!(f.next().await.is_err());
}

#[tokio::test]
async fn ltp_only_packets_emit_trade_only_when_price_changes() {
    let script = vec![
        bin(&[ltp(TCS, 100)]),
        bin(&[ltp(TCS, 100)]),
        bin(&[ltp(TCS, 200)]),
        bin(&[ltp(TCS, 100)]),
    ];
    let (mut f, _) = feed_with(script, Mode::Ltp);
    f.subscribe(&[inst("TCS")]).await.unwrap();
    let a = trade_of(&f.next().await.unwrap().unwrap());
    let b = trade_of(&f.next().await.unwrap().unwrap());
    assert_eq!((a.0, a.1), (2.0, 0.0));
    assert_eq!((b.0, b.1), (1.0, 0.0));
    assert_ne!(a.2, b.2, "ltp-only trade ids unique even at equal ts_event");
    assert!(f.next().await.is_err());
}

#[tokio::test]
async fn baseline_is_per_token() {
    let script = vec![bin(&[ltp(TCS, 100), ltp(REL, 100)]), bin(&[ltp(TCS, 150)])];
    let (mut f, _) = feed_with(script, Mode::Ltp);
    f.subscribe(&[inst("TCS"), inst("RELIANCE")]).await.unwrap();
    let a = trade_of(&f.next().await.unwrap().unwrap());
    assert_eq!(a.0, 1.5);
    assert!(f.next().await.is_err(), "REL had only its baseline");
}
