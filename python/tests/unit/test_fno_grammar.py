"""Unit tests for Indian F&O instrument grammar and domain resolution (E1-S2)."""

import datetime

import pytest

from honba._honba import FnoContract, parse_fno_symbol
from honba.domain.instrument import Instrument, InstrumentId, InstrumentKind, OptionType
from honba.markets.india.fno import resolve_fno_instrument


def test_fno_grammar_parsing_monthly_and_weekly():
    # Monthly index option
    opt_m = parse_fno_symbol("NIFTY24OCT24000CE")
    assert isinstance(opt_m, FnoContract)
    assert opt_m.underlying == "NIFTY"
    assert opt_m.kind == "option"
    assert opt_m.expiry_year == 2024
    assert opt_m.expiry_month == 10
    assert opt_m.expiry_day is None
    assert not opt_m.is_weekly
    assert opt_m.strike == 24000.0
    assert opt_m.option_type == "CE"
    assert opt_m.symbol == "NIFTY24OCT24000CE"

    # Weekly index option
    opt_w = parse_fno_symbol("NIFTY24O1724000CE")
    assert opt_w.is_weekly
    assert opt_w.expiry_year == 2024
    assert opt_w.expiry_month == 10
    assert opt_w.expiry_day == 17
    assert opt_w.strike == 24000.0
    assert opt_w.option_type == "CE"

    # Weekly option with month digit '1' (Jan)
    opt_jan = parse_fno_symbol("FINNIFTY2410921000CE")
    assert opt_jan.is_weekly
    assert opt_jan.expiry_month == 1
    assert opt_jan.expiry_day == 9
    assert opt_jan.underlying == "FINNIFTY"

    # Monthly future
    fut = parse_fno_symbol("RELIANCE24OCTFUT")
    assert fut.kind == "future"
    assert fut.underlying == "RELIANCE"
    assert fut.strike is None
    assert fut.option_type is None
    assert not fut.is_weekly


def test_ce_suffix_ambiguity():
    # Equities containing CE/PE should not parse as F&O options
    with pytest.raises(ValueError):
        parse_fno_symbol("CESC")

    with pytest.raises(ValueError):
        parse_fno_symbol("CENTURYPLY")

    with pytest.raises(ValueError):
        parse_fno_symbol("PETRONET")

    # Options on those underlyings should parse properly
    cesc_opt = parse_fno_symbol("CESC24OCT180CE")
    assert cesc_opt.underlying == "CESC"
    assert cesc_opt.strike == 180.0
    assert cesc_opt.option_type == "CE"

    cent_opt = parse_fno_symbol("CENTURYPLY24OCT750PE")
    assert cent_opt.underlying == "CENTURYPLY"
    assert cent_opt.strike == 750.0
    assert cent_opt.option_type == "PE"


def test_resolve_fno_instrument():
    inst = resolve_fno_instrument("NIFTY24OCT24000CE")
    assert isinstance(inst, Instrument)
    assert inst.instrument_id == InstrumentId("NIFTY24OCT24000CE", "NSE")
    assert inst.kind == InstrumentKind.OPTION
    assert inst.lot_size == 25.0
    assert inst.tick_size == 0.05
    assert inst.currency == "INR"
    assert inst.underlying == "NIFTY"
    assert inst.strike == 24000.0
    assert inst.option_type == OptionType.CALL
    assert inst.expiry == datetime.date(2024, 10, 31)
    assert inst.is_derivative

    # Future
    fut_inst = resolve_fno_instrument("RELIANCE24OCTFUT")
    assert fut_inst.kind == InstrumentKind.FUTURE
    assert fut_inst.underlying == "RELIANCE"
    assert fut_inst.strike is None
    assert fut_inst.option_type is None
    assert fut_inst.is_derivative


def test_option_type_enum():
    assert OptionType.CALL.code == "CE"
    assert OptionType.PUT.code == "PE"
    assert OptionType.from_code("CE") == OptionType.CALL
    assert OptionType.from_code("call") == OptionType.CALL
    assert OptionType.from_code("PE") == OptionType.PUT
    assert OptionType.from_code("put") == OptionType.PUT

    with pytest.raises(ValueError):
        OptionType.from_code("INVALID")
