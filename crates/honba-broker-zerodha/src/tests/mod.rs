//! Unit tests for the Zerodha adapter.

use honba_messages::{Exchange, InstrumentId};

use std::collections::VecDeque;
use std::sync::Mutex;

use crate::transport::{HttpRequest, HttpResponse, HttpTransport, TransportError};

mod client;
mod mapping;
mod rate_limit;
mod ticker;
mod tokens;
mod wire;

/// Scripted in-memory transport that records every request.
pub(crate) struct FakeTransport {
    responses: Mutex<VecDeque<Result<HttpResponse, TransportError>>>,
    requests: Mutex<Vec<HttpRequest>>,
}

impl FakeTransport {
    pub(crate) fn new(responses: Vec<Result<HttpResponse, TransportError>>) -> Self {
        Self {
            responses: Mutex::new(responses.into()),
            requests: Mutex::new(Vec::new()),
        }
    }

    pub(crate) fn ok(status: u16, body: &str) -> Result<HttpResponse, TransportError> {
        Ok(HttpResponse {
            status,
            body: body.to_owned(),
        })
    }

    pub(crate) fn requests(&self) -> Vec<HttpRequest> {
        self.requests.lock().unwrap().clone()
    }
}

#[async_trait::async_trait]
impl HttpTransport for FakeTransport {
    async fn send(&self, req: HttpRequest) -> Result<HttpResponse, TransportError> {
        self.requests.lock().unwrap().push(req);
        self.responses
            .lock()
            .unwrap()
            .pop_front()
            .expect("no scripted response left")
    }
}

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
