//! Unit tests for `crate::settlement`.

use honba_entities::{InstrumentKind, PositionSide};

use super::{date, Holidays};
use crate::settlement::PercentageMarginModel;
use crate::StandardRollingSettlement;
use crate::{MarginModel, MarginRequirement, MarketProfile, SettlementRules, SettlementType};

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
fn t_plus_two_skips_weekends() {
    let cal = Holidays(vec![]);
    let s = StandardRollingSettlement::t_plus_2();
    // Thu 9th -> Fri 10th (1), Sat/Sun skipped -> Mon 13th (2).
    assert_eq!(
        s.settlement_date(date(2025, 1, 9), InstrumentKind::Equity, &cal),
        date(2025, 1, 13)
    );
}

#[test]
fn t_plus_two_skips_weekends_and_holidays() {
    let cal = Holidays(vec![date(2025, 1, 13)]);
    let s = StandardRollingSettlement::t_plus_2();
    // Fri 10th -> Sat, Sun, holiday Mon 13th -> Tue 14th, Wed 15th.
    assert_eq!(
        s.settlement_date(date(2025, 1, 10), InstrumentKind::Equity, &cal),
        date(2025, 1, 15)
    );
}

#[test]
fn t_plus_two_reports_two_days() {
    let s = StandardRollingSettlement::t_plus_2();
    assert_eq!(s.settlement_days(InstrumentKind::Equity), 2);
}

#[cfg(feature = "india")]
#[test]
fn india_equity_settles_t_plus_two_by_default() {
    let profile = crate::IndiaMarketProfile::default();
    assert_eq!(
        profile
            .settlement_rules()
            .settlement_days(InstrumentKind::Equity),
        2
    );
    assert_eq!(crate::IndiaMarketProfile::equity_settlement_days(), 2);
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
