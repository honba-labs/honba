use honba_messages::{Exchange, InstrumentId};

use super::any_instrument;
use crate::tokens::{TokenMap, TokensError};

const CSV: &str = "instrument_token,exchange_token,tradingsymbol,name,last_price,expiry,strike,tick_size,lot_size,instrument_type,segment,exchange\n\
738561,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE\n\
256265,1001,NIFTY 50,NIFTY 50,0,,0,0,0,EQ,INDICES,NSE\n";

#[test]
fn insert_and_lookup_both_ways() {
    let mut m = TokenMap::new();
    assert!(m.is_empty());
    m.insert(1, any_instrument());
    assert_eq!(m.len(), 1);
    assert_eq!(m.token_for(&any_instrument()), Some(1));
    assert_eq!(m.instrument_for(1), Some(&any_instrument()));
    assert_eq!(m.instrument_for(2), None);
}

#[test]
fn reinserting_instrument_drops_stale_token() {
    let mut m = TokenMap::new();
    m.insert(1, any_instrument());
    m.insert(2, any_instrument());
    assert_eq!(m.len(), 1);
    assert_eq!(m.token_for(&any_instrument()), Some(2));
    assert_eq!(m.instrument_for(1), None);
    assert_eq!(m.instrument_for(2), Some(&any_instrument()));
}

#[test]
fn reinserting_token_drops_stale_instrument() {
    let other = InstrumentId::new("TCS", Exchange::new("NSE"));
    let mut m = TokenMap::new();
    m.insert(1, any_instrument());
    m.insert(1, other.clone());
    assert_eq!(m.len(), 1);
    assert_eq!(m.token_for(&any_instrument()), None);
    assert_eq!(m.token_for(&other), Some(1));
}

#[test]
fn parses_instruments_csv() {
    let m = TokenMap::from_instruments_csv(CSV).unwrap();
    assert_eq!(m.len(), 2);
    assert_eq!(m.token_for(&any_instrument()), Some(738_561));
    let nifty = InstrumentId::new("NIFTY 50", Exchange::new("NSE"));
    assert_eq!(m.token_for(&nifty), Some(256_265));
}

#[test]
fn malformed_row_errors() {
    let bad = format!("{CSV}notanumber,1,X,X,0,,0,0,1,EQ,NSE,NSE\n");
    assert!(matches!(
        TokenMap::from_instruments_csv(&bad),
        Err(TokensError::Csv(_))
    ));
    let short = format!("{CSV}5,1,X\n");
    assert!(matches!(
        TokenMap::from_instruments_csv(&short),
        Err(TokensError::Csv(_))
    ));
}

#[test]
fn duplicate_rows_keep_last_consistently() {
    let dup = format!("{CSV}999,2885,RELIANCE,RELIANCE INDUSTRIES,0,,0,0.05,1,EQ,NSE,NSE\n");
    let m = TokenMap::from_instruments_csv(&dup).unwrap();
    assert_eq!(m.len(), 2);
    assert_eq!(m.token_for(&any_instrument()), Some(999));
    assert_eq!(m.instrument_for(738_561), None);
}
