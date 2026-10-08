//! Public-API flow: instruments csv -> token map -> frame decode -> domain messages.

use honba_broker_zerodha::ticker::{decode_frame, to_messages};
use honba_broker_zerodha::tokens::TokenMap;
use honba_messages::{Event, UnixNanos};

const CSV: &str = "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n\
2885633,11272,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE\n\
2953217,11536,TCS,TCS,0,,0,0.05,1,EQ,NSE,NSE\n";

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

#[test]
fn multi_packet_frame_flows_to_messages() {
    let map = TokenMap::from_instruments_csv(CSV).unwrap();
    let mut full = u32s(&[2_885_633, 250_050, 10, 250_000, 5000, 1, 2, 1, 2, 3, 4]);
    full.extend(u32s(&[0, 0, 0, 0, 1_700_000_000]));
    for i in 0..10u32 {
        full.extend(u32s(&[5, if i < 5 { 250_000 } else { 250_100 }]));
        full.extend_from_slice(&[0, 1, 0, 0]);
    }
    let ltp = u32s(&[2_953_217, 410_000]);
    let ticks = decode_frame(&frame(&[full, ltp])).unwrap();
    assert_eq!(ticks.len(), 2);

    let mut msgs = Vec::new();
    for t in &ticks {
        let inst = map.instrument_for(t.token).expect("mapped token");
        msgs.extend(to_messages(t, inst, UnixNanos::from_u64(9)));
    }
    assert_eq!(msgs.len(), 3);
    assert!(matches!(msgs[0].event(), Event::Trade(_)));
    assert!(matches!(msgs[1].event(), Event::Quote(_)));
    let Event::Trade(tr) = msgs[2].event() else {
        panic!()
    };
    assert_eq!(tr.instrument_id().symbol(), "TCS");
    assert_eq!(tr.price(), 4100.0);
}

#[test]
fn heartbeat_and_garbage_are_handled() {
    assert!(decode_frame(&[0]).unwrap().is_empty());
    assert!(decode_frame(&[0, 5, 0]).is_err());
}
