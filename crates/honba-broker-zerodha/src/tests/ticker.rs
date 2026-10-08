use honba_messages::{AggressorSide, Event, UnixNanos};

use super::{any_instrument, frame, u32be};
use crate::ticker::{decode_frame, to_messages, Mode, Segment, TickerError};

const NSE_TOKEN: u32 = 408_065 * 256 + 1;

fn ltp_packet(token: u32, ltp: u32) -> Vec<u8> {
    let mut p = Vec::new();
    u32be(&mut p, token);
    u32be(&mut p, ltp);
    p
}

fn quote_packet(token: u32) -> Vec<u8> {
    let mut p = Vec::new();
    // ltp, last_qty, avg, volume, buy_qty, sell_qty, open, high, low, close
    for v in [
        token, 250_050, 10, 250_000, 5000, 100, 200, 249_000, 251_000, 248_000, 249_500,
    ] {
        u32be(&mut p, v);
    }
    p
}

fn full_packet(token: u32, bid: u32, ask: u32) -> Vec<u8> {
    let mut p = quote_packet(token);
    for v in [1_700_000_000u32, 7, 8, 6, 1_700_000_100] {
        u32be(&mut p, v);
    }
    for i in 0..10u32 {
        let price = if i < 5 { bid } else { ask };
        u32be(&mut p, 10 + i);
        u32be(&mut p, price);
        p.extend_from_slice(&(i as u16 + 1).to_be_bytes());
        p.extend_from_slice(&[0, 0]);
    }
    assert_eq!(p.len(), 184);
    p
}

#[test]
fn heartbeat_decodes_to_nothing() {
    assert_eq!(decode_frame(&[0x00]).unwrap(), vec![]);
}

#[test]
fn ltp_packet_decodes() {
    let t = decode_frame(&frame(&[ltp_packet(NSE_TOKEN, 250_075)])).unwrap();
    assert_eq!(t.len(), 1);
    assert_eq!(t[0].token, NSE_TOKEN);
    assert_eq!(t[0].segment, Segment::Nse);
    assert_eq!(t[0].mode, Mode::Ltp);
    assert!((t[0].ltp - 2500.75).abs() < 1e-9);
    assert_eq!(t[0].volume, None);
}

#[test]
fn cds_and_bcd_use_wider_divisors() {
    let cds = decode_frame(&frame(&[ltp_packet(256 * 5 + 3, 835_000_000)])).unwrap();
    assert_eq!(cds[0].segment, Segment::Cds);
    assert!((cds[0].ltp - 83.5).abs() < 1e-9);
    let bcd = decode_frame(&frame(&[ltp_packet(256 * 5 + 6, 835_000)])).unwrap();
    assert_eq!(bcd[0].segment, Segment::Bcd);
    assert!((bcd[0].ltp - 83.5).abs() < 1e-9);
}

#[test]
fn quote_packet_decodes() {
    let t = &decode_frame(&frame(&[quote_packet(NSE_TOKEN)])).unwrap()[0];
    assert_eq!(t.mode, Mode::Quote);
    assert_eq!(t.last_qty, Some(10));
    assert_eq!(t.volume, Some(5000));
    assert_eq!(t.open, Some(2490.0));
    assert_eq!(t.high, Some(2510.0));
    assert_eq!(t.low, Some(2480.0));
    assert_eq!(t.close, Some(2495.0));
    assert!(t.bids.is_empty() && t.asks.is_empty());
    assert_eq!(t.exchange_ts_secs, None);
}

#[test]
fn index_packets_decode() {
    let token = 256 * 9 + 9;
    let mut p = Vec::new();
    // token, ltp, high, low, open, close, change
    for v in [
        token, 2_200_000, 2_210_000, 2_190_000, 2_195_000, 2_180_000, 5,
    ] {
        u32be(&mut p, v);
    }
    let t = &decode_frame(&frame(&[p.clone()])).unwrap()[0];
    assert_eq!(t.segment, Segment::Indices);
    assert_eq!(t.mode, Mode::Quote);
    assert_eq!(t.high, Some(22_100.0));
    assert_eq!(t.low, Some(21_900.0));
    assert_eq!(t.open, Some(21_950.0));
    assert_eq!(t.close, Some(21_800.0));
    assert_eq!(t.exchange_ts_secs, None);
    u32be(&mut p, 1_700_000_000);
    let t = &decode_frame(&frame(&[p])).unwrap()[0];
    assert_eq!(t.mode, Mode::Full);
    assert_eq!(t.exchange_ts_secs, Some(1_700_000_000));
}

#[test]
fn full_packet_decodes_depth() {
    let t = &decode_frame(&frame(&[full_packet(NSE_TOKEN, 250_000, 250_100)])).unwrap()[0];
    assert_eq!(t.mode, Mode::Full);
    assert_eq!(t.exchange_ts_secs, Some(1_700_000_100));
    assert_eq!(t.bids.len(), 5);
    assert_eq!(t.asks.len(), 5);
    assert_eq!(t.bids[0].qty, 10);
    assert_eq!(t.bids[0].orders, 1);
    assert!((t.bids[0].price - 2500.0).abs() < 1e-9);
    assert!((t.asks[0].price - 2501.0).abs() < 1e-9);
    assert_eq!(t.asks[4].orders, 10);
}

#[test]
fn multi_packet_frame_decodes_in_order() {
    let f = frame(&[ltp_packet(NSE_TOKEN, 100), ltp_packet(NSE_TOKEN + 256, 200)]);
    let t = decode_frame(&f).unwrap();
    assert_eq!(t.len(), 2);
    assert_eq!(t[1].token, NSE_TOKEN + 256);
}

#[test]
fn malformed_frames_error_without_panic() {
    let good = frame(&[ltp_packet(NSE_TOKEN, 100)]);
    for cut in 2..good.len() {
        assert!(
            matches!(
                decode_frame(&good[..cut]),
                Err(TickerError::Malformed { .. })
            ),
            "cut {cut}"
        );
    }
    assert!(matches!(
        decode_frame(&[]),
        Err(TickerError::Malformed { .. })
    ));
}

#[test]
fn unknown_length_is_reported() {
    let f = frame(&[vec![0u8; 12]]);
    assert_eq!(decode_frame(&f), Err(TickerError::UnknownPacketLength(12)));
}

#[test]
fn ltp_maps_to_trade_with_ts_init() {
    let t = &decode_frame(&frame(&[ltp_packet(NSE_TOKEN, 250_075)])).unwrap()[0];
    let m = to_messages(t, &any_instrument(), UnixNanos::from_u64(42));
    assert_eq!(m.len(), 1);
    match m[0].event() {
        Event::Trade(tr) => {
            assert_eq!(tr.price(), 2500.75);
            assert_eq!(tr.size(), 0.0);
            assert_eq!(tr.aggressor_side(), AggressorSide::NoAggressor);
            assert_eq!(tr.ts_event(), UnixNanos::from_u64(42));
            assert_eq!(tr.trade_id().as_str(), format!("{NSE_TOKEN}-42"));
        }
        other => panic!("expected trade, got {other:?}"),
    }
}

#[test]
fn quote_maps_to_sized_trade() {
    let t = &decode_frame(&frame(&[quote_packet(NSE_TOKEN)])).unwrap()[0];
    let m = to_messages(t, &any_instrument(), UnixNanos::from_u64(42));
    assert_eq!(m.len(), 1);
    let Event::Trade(tr) = m[0].event() else {
        panic!()
    };
    assert_eq!(tr.size(), 10.0);
}

#[test]
fn full_maps_to_trade_and_quote_with_exchange_ts() {
    let t = &decode_frame(&frame(&[full_packet(NSE_TOKEN, 250_000, 250_100)])).unwrap()[0];
    let m = to_messages(t, &any_instrument(), UnixNanos::from_u64(1));
    assert_eq!(m.len(), 2);
    let expected_ts = UnixNanos::from_u64(1_700_000_100 * 1_000_000_000);
    let Event::Trade(tr) = m[0].event() else {
        panic!()
    };
    assert_eq!(tr.ts_event(), expected_ts);
    let Event::Quote(q) = m[1].event() else {
        panic!()
    };
    assert_eq!(q.bid_price(), 2500.0);
    assert_eq!(q.ask_price(), 2501.0);
    assert_eq!(q.bid_size(), 10.0);
    assert_eq!(q.ask_size(), 15.0);
    assert_eq!(q.ts_event(), expected_ts);
    assert_eq!(q.ts_init(), UnixNanos::from_u64(1));
}

#[test]
fn full_skips_quote_when_crossed_or_empty() {
    let crossed = &decode_frame(&frame(&[full_packet(NSE_TOKEN, 250_200, 250_100)])).unwrap()[0];
    assert_eq!(
        to_messages(crossed, &any_instrument(), UnixNanos::from_u64(1)).len(),
        1
    );
    let empty = &decode_frame(&frame(&[full_packet(NSE_TOKEN, 0, 250_100)])).unwrap()[0];
    assert_eq!(
        to_messages(empty, &any_instrument(), UnixNanos::from_u64(1)).len(),
        1
    );
    let mut none = empty.clone();
    none.bids.clear();
    assert_eq!(
        to_messages(&none, &any_instrument(), UnixNanos::from_u64(1)).len(),
        1
    );
}
