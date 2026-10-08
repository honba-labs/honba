use crate::ws::{ticker_url, WsFrame};

use super::FakeSocket;
use crate::ws::TickerSocket;

#[test]
fn ticker_url_carries_key_and_token() {
    assert_eq!(
        ticker_url("key1", "tok2"),
        "wss://ws.kite.trade?api_key=key1&access_token=tok2"
    );
}

#[tokio::test]
async fn fake_socket_records_sends_and_closes_with_none() {
    let (mut s, sent) = FakeSocket::new(vec![Ok(Some(WsFrame::Text("x".into())))]);
    s.send_text("hello".into()).await.unwrap();
    assert_eq!(sent.lock().unwrap().as_slice(), ["hello"]);
    assert_eq!(s.recv().await.unwrap(), Some(WsFrame::Text("x".into())));
    assert_eq!(s.recv().await.unwrap(), None, "closed");
}
