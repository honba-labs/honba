//! Unit tests for `crate::pyclasses::next_open` (the interpreter-free core of `NextOpenSimulator`).

use crate::pyclasses::next_open::*;

fn bar(symbol: &str, ts: u64, open: f64) -> BarIn {
    BarIn {
        symbol: symbol.into(),
        exchange: "NSE".into(),
        ts,
        open,
        high: open,
        low: open,
        close: open,
        volume: 1000.0,
    }
}

fn market(id: &str, symbol: &str, side: &str, qty: f64, ts: u64) -> OrderIn {
    OrderIn {
        id: id.into(),
        symbol: symbol.into(),
        exchange: "NSE".into(),
        side: side.into(),
        kind: "market".into(),
        qty,
        price: None,
        trigger: None,
        ts,
    }
}

fn sim(cash: i64) -> NativeSim {
    NativeSim::new(SimConfig::new(cash)).unwrap()
}

#[test]
fn a_market_buy_fills_at_the_next_open_in_minor_units() {
    let mut s = sim(1_000_000);
    s.on_bar(&bar("AAA", 1, 100.0)).unwrap();
    s.submit(&market("o-0", "AAA", "buy", 10.0, 1)).unwrap();
    assert!(s.drain_fills().is_empty());
    s.on_bar(&bar("AAA", 2, 110.0)).unwrap();
    let fills = s.drain_fills();
    assert_eq!(fills.len(), 1);
    let f = &fills[0];
    assert_eq!(
        (f.order_id.as_str(), f.symbol.as_str(), f.side),
        ("o-0", "AAA", "buy")
    );
    assert_eq!((f.quantity, f.price, f.ts, f.costs), (10.0, 110.0, 2, 0));
    assert_eq!(s.cash(), 890_000);
    assert_eq!(s.traded_notional(), 110_000);
    assert_eq!(s.positions(), vec![("AAA".into(), "NSE".into(), 10.0)]);
}

#[test]
fn rejections_carry_the_cancelled_flag_and_stamp() {
    let mut s = sim(1_000_000);
    s.submit(&market("o-0", "AAA", "buy", 1.0, 0)).unwrap();
    s.cancel("o-0", 7).unwrap();
    let r = s.drain_rejections();
    assert_eq!(r.len(), 1);
    assert_eq!(
        (r[0].reason.as_str(), r[0].cancelled, r[0].ts),
        ("cancelled", true, 7)
    );
    assert!(s.drain_rejections().is_empty());
}

#[test]
fn a_duplicate_bar_is_a_value_error_and_a_late_settlement_change_a_runtime_error() {
    let mut s = sim(1_000_000);
    s.on_bar(&bar("AAA", 1, 100.0)).unwrap();
    let e = s.on_bar(&bar("AAA", 1, 100.0)).unwrap_err();
    assert_eq!(e.kind, SimErrorKind::Value, "{}", e.message);
    let e = s.set_settlement_days(1).unwrap_err();
    assert_eq!(e.kind, SimErrorKind::Runtime, "{}", e.message);
    let e = s.set_settlement_days(-1).unwrap_err();
    assert_eq!(e.kind, SimErrorKind::Value, "{}", e.message);
}

#[test]
fn bad_input_is_a_value_error_not_a_panic() {
    let mut s = sim(1_000_000);
    let mut o = market("o", "AAA", "buy", 1.0, 0);
    o.kind = "iceberg".into();
    assert_eq!(s.submit(&o).unwrap_err().kind, SimErrorKind::Value);
    assert_eq!(
        s.submit(&market("o", "AAA", "hold", 1.0, 0))
            .unwrap_err()
            .kind,
        SimErrorKind::Value
    );
    let mut cfg = SimConfig::new(100);
    cfg.currency = "XYZ".into();
    assert_eq!(NativeSim::new(cfg).err().unwrap().kind, SimErrorKind::Value);
    assert_eq!(
        NativeSim::new(SimConfig::new(-1)).err().unwrap().kind,
        SimErrorKind::Value
    );
    assert_eq!(
        s.set_lot_size("AAA", "NSE", 0.0).unwrap_err().kind,
        SimErrorKind::Value
    );
}

#[test]
fn a_limit_order_is_reported_unsupported() {
    let mut s = sim(1_000_000);
    let mut o = market("o", "AAA", "buy", 3.0, 5);
    o.kind = "limit".into();
    o.price = Some(99.0);
    s.submit(&o).unwrap();
    let r = s.drain_rejections();
    assert_eq!(
        (r[0].reason.as_str(), r[0].quantity, r[0].ts),
        ("unsupported_order_type", 3.0, 5)
    );
}

#[test]
fn named_india_costs_match_the_python_fill_cost_functions() {
    // Values printed by `nse_equity_{delivery,intraday}_fill_cost` for 10 @ 2950.0.
    assert_eq!(
        india_fill_cost("india.equity.delivery", "buy", 10.0, 2950.0).unwrap(),
        1598
    );
    assert_eq!(
        india_fill_cost("india.equity", "sell", 10.0, 2950.0).unwrap(),
        4105
    );
    assert_eq!(
        india_fill_cost("india.equity.intraday", "buy", 10.0, 2950.0).unwrap(),
        1244
    );
    assert_eq!(
        india_fill_cost("india.equity.intraday", "sell", 10.0, 2950.0).unwrap(),
        1893
    );
    assert_eq!(india_fill_cost("none", "buy", 10.0, 2950.0).unwrap(), 0);
    assert_eq!(
        india_fill_cost("india.futures", "buy", 1.0, 1.0)
            .unwrap_err()
            .kind,
        SimErrorKind::Value
    );
}

#[test]
fn a_named_cost_pack_is_charged_on_fills() {
    let mut cfg = SimConfig::new(100_000_000);
    cfg.costs = CostSpec::named("india.equity.delivery").unwrap();
    let mut s = NativeSim::new(cfg).unwrap();
    s.submit(&market("o", "AAA", "buy", 10.0, 0)).unwrap();
    s.on_bar(&bar("AAA", 1, 2950.0)).unwrap();
    assert_eq!(s.drain_fills()[0].costs, 1598);
    assert_eq!(s.fees(), 1598);
    assert_eq!(s.cash(), 100_000_000 - 2_950_000 - 1598);
}

#[test]
fn set_position_seeds_a_holding_by_symbol_and_exchange() {
    let mut s = sim(0);
    s.set_position("AAA", "NSE", 4.0).unwrap();
    s.set_position("AAA", "BSE", 2.0).unwrap();
    assert_eq!(
        s.positions(),
        vec![
            ("AAA".into(), "NSE".into(), 4.0),
            ("AAA".into(), "BSE".into(), 2.0)
        ]
    );
    s.set_position("AAA", "NSE", 0.0).unwrap();
    assert_eq!(s.positions(), vec![("AAA".into(), "BSE".into(), 2.0)]);
    assert!(s.set_position("AAA", "NSE", f64::NAN).is_err());
}

#[test]
fn session_ts_follows_the_open_session() {
    let mut s = sim(0);
    assert_eq!(s.session_ts(), None);
    s.on_bar(&bar("AAA", 5, 10.0)).unwrap();
    assert_eq!(s.session_ts(), Some(5));
}

#[test]
fn an_unusable_or_malformed_bar_never_panics_and_never_fills() {
    for open in [f64::NAN, f64::INFINITY, f64::NEG_INFINITY, 0.0, -5.0] {
        let mut s = sim(1_000_000);
        s.on_bar(&bar("AAA", 1, 100.0)).unwrap();
        s.submit(&market("o", "AAA", "buy", 1.0, 1)).unwrap();
        s.on_bar(&bar("AAA", 2, open)).unwrap(); // the session opens, the order keeps waiting
        assert_eq!(s.working_orders(), vec!["o".to_string()], "open {open}");
        assert!(s.drain_fills().is_empty());
        s.on_bar(&bar("AAA", 3, 100.0)).unwrap();
        assert_eq!(s.drain_fills().len(), 1);
    }
    // crossed high/low, negative volume: only the open matters to this simulator
    let mut s = sim(1_000_000);
    let mut b = bar("AAA", 1, 100.0);
    (b.high, b.low, b.volume) = (90.0, 110.0, -1.0);
    s.on_bar(&b).unwrap();
}
