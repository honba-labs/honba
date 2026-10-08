//! Unit tests for the Zerodha adapter.

use honba_messages::{Exchange, InstrumentId};

mod ticker;
mod tokens;

pub(crate) fn any_instrument() -> InstrumentId {
    InstrumentId::new("RELIANCE", Exchange::new("NSE"))
}

/// Appends a big-endian u32.
pub(crate) fn u32be(buf: &mut Vec<u8>, v: u32) {
    buf.extend_from_slice(&v.to_be_bytes());
}

/// Wraps packets into a frame.
pub(crate) fn frame(packets: &[Vec<u8>]) -> Vec<u8> {
    let mut f = (packets.len() as u16).to_be_bytes().to_vec();
    for p in packets {
        f.extend_from_slice(&(p.len() as u16).to_be_bytes());
        f.extend_from_slice(p);
    }
    f
}
