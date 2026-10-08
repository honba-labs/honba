//! Rule 3: unknown instruments are refused, never approved.

use honba_messages::{Exchange, InstrumentId};

use super::{refusal, req, stage};
use crate::{RiskCheck, RiskRefusal};

#[test]
fn instrument_unknown_refused() {
    let unknown = InstrumentId::new("NOPE", Exchange::new("NSE"));
    let mut r = req();
    r.instrument_id = unknown.clone();
    assert_eq!(
        refusal(stage().check(&r)),
        RiskRefusal::InstrumentUnknown {
            instrument_id: unknown
        }
    );
}
