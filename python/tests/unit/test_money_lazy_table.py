"""The Rust currency table loads lazily; the domain module imports without the extension."""

from __future__ import annotations

import importlib
import sys
import types

import pytest

import honba


@pytest.fixture
def fresh_money(monkeypatch):
    """Re-import ``honba.domain.money`` so the module-level state is rebuilt."""
    monkeypatch.delitem(sys.modules, "honba.domain.money", raising=False)
    yield lambda: importlib.import_module("honba.domain.money")
    sys.modules.pop("honba.domain.money", None)


def _stub_extension(monkeypatch, **attrs):
    stub = types.ModuleType("honba._honba")
    for key, value in attrs.items():
        setattr(stub, key, value)
    monkeypatch.setitem(sys.modules, "honba._honba", stub)
    monkeypatch.setattr(honba, "_honba", stub, raising=False)


def test_import_succeeds_when_the_extension_is_missing(monkeypatch, fresh_money):
    monkeypatch.setitem(sys.modules, "honba._honba", None)  # import raises ImportError
    money = fresh_money()
    assert money.Currency.INR.value == "INR"
    with pytest.raises(ImportError, match="honba._honba"):
        money.Currency.INR.minor_exponent  # noqa: B018


def test_stale_extension_gives_a_clear_error(monkeypatch, fresh_money):
    _stub_extension(monkeypatch)  # no currency_minor_units attribute
    money = fresh_money()
    with pytest.raises(RuntimeError, match="currency_minor_units.*rebuild"):
        money.Currency.USD.minor_exponent  # noqa: B018


def test_table_is_loaded_once_on_first_use(monkeypatch, fresh_money):
    calls = []

    def table():
        calls.append(1)
        return {"INR": (2, "paisa", "paise"), "USD": (2, "cent", "cents")}

    _stub_extension(monkeypatch, currency_minor_units=table)
    money = fresh_money()
    assert calls == []
    assert money.Currency.INR.minor_exponent == 2
    assert money.Currency.USD.minor_unit.plural == "cents"
    assert calls == [1]
