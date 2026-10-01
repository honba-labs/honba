//! Unit tests for `crate::settlement`.

use honba_entities::{InstrumentKind, PositionSide};

use super::{date, Holidays};
use crate::settlement::PercentageMarginModel;
use crate::StandardRollingSettlement;
use crate::{MarginModel, MarginRequirement, SettlementRules, SettlementType};

#[test]
fn t_plus_one_skips_weekends_and_holidays() {
    let cal = Holidays(vec![date(2025, 1, 13)]);
    let s = StandardRollingSettlement::t_plus_1();
    // Fri 10th -> Sat, Sun, holiday Mon 13th -> Tue 14th.
    assert_eq!(
        s.settlement_date(date(2025, 1, 10), InstrumentKind::Equity, &cal),
        date(2025, 1, 14)
    );
}

#[test]
fn t_plus_zero_settles_on_the_trade_date() {
    let cal = Holidays(vec![]);
    let s = StandardRollingSettlement::t_plus_0();
    assert_eq!(
        s.settlement_date(date(2025, 1, 11), InstrumentKind::Equity, &cal),
        date(2025, 1, 11)
    );
}

#[test]
fn rolling_settlement_is_cash_for_every_kind() {
    let s = StandardRollingSettlement::new(2);
    assert_eq!(s.settlement_days(InstrumentKind::Future), 2);
    assert_eq!(
        s.settlement_type(InstrumentKind::Option),
        SettlementType::Cash
    );
}

#[test]
fn percentage_margin_scales_notional() {
    let m = PercentageMarginModel::new(0.2, 0.1);
    assert_eq!(
        m.calculate_margin(InstrumentKind::Future, PositionSide::Short, 1000.0),
        MarginRequirement::new(200.0, 100.0)
    );
    assert_eq!(MarginRequirement::zero(), MarginRequirement::new(0.0, 0.0));
}
