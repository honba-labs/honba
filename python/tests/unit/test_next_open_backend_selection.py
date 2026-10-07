"""Backend selection of ``NextOpenExecution`` (ADR 0016, chunk 3b).

The simulator runs on the native ``honba._honba.NextOpenSimulator`` when the extension is
present and current, and on the pure-Python implementation otherwise. ``backend=`` (or the
``HONBA_SIM_BACKEND`` environment variable: ``python`` | ``native`` | ``auto``) overrides the
choice; the constructor argument wins over the environment.
"""

from __future__ import annotations

from typing import Any

import pytest

import honba._native as native_mod
from honba.backtest.simulated import NextOpenExecution, make_simulator, resolve_backend
from honba.domain.money import Currency, Money
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.entities.order import OrderIntent

A = InstrumentId("AAA", "NSE")
CASH = Money.from_major(1_000.0, Currency.INR)


def _have_native() -> bool:
    try:
        native_mod.native_attr("NextOpenSimulator")
    except (ImportError, RuntimeError):
        return False
    return True


needs_native = pytest.mark.skipif(not _have_native(), reason="native extension not built")


def _break_native(monkeypatch: pytest.MonkeyPatch, exc: Exception) -> None:
    def broken(name: str) -> Any:
        raise exc

    monkeypatch.setattr(native_mod, "native_attr", broken)


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("HONBA_SIM_BACKEND", raising=False)


@needs_native
def test_auto_uses_the_native_backend_when_the_extension_is_present() -> None:
    assert resolve_backend("auto") == "native"
    assert NextOpenExecution(cash=CASH).backend == "native"


@needs_native
def test_the_default_is_auto() -> None:
    assert resolve_backend(None) == "native"


def test_auto_falls_back_to_python_when_the_extension_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_native(monkeypatch, ImportError("no extension"))
    assert resolve_backend("auto") == "python"
    port = NextOpenExecution(cash=CASH)
    assert port.backend == "python"
    port.open_session(1, [Bar(A, 1, 10.0, 10.0, 10.0, 10.0, 1.0)])  # it works


def test_auto_falls_back_to_python_when_the_extension_is_stale(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_native(monkeypatch, RuntimeError("honba._honba has no 'NextOpenSimulator': stale"))
    assert NextOpenExecution(cash=CASH).backend == "python"


@needs_native
def test_auto_falls_back_when_the_class_lacks_a_method_this_wrapper_needs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class Old:  # an extension from before chunk 3b: no set_position
        pass

    monkeypatch.setattr(native_mod, "native_attr", lambda name: Old)
    assert resolve_backend("auto") == "python"


def test_python_can_be_forced_even_when_native_is_present() -> None:
    assert NextOpenExecution(cash=CASH, backend="python").backend == "python"


@needs_native
def test_native_can_be_forced() -> None:
    assert NextOpenExecution(cash=CASH, backend="native").backend == "native"


def test_forcing_native_without_the_extension_is_a_clear_error(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _break_native(monkeypatch, ImportError("no extension"))
    with pytest.raises(ImportError, match="native"):
        NextOpenExecution(cash=CASH, backend="native")
    _break_native(monkeypatch, RuntimeError("stale"))
    with pytest.raises(RuntimeError, match="stale|rebuild"):
        NextOpenExecution(cash=CASH, backend="native")


def test_an_unknown_backend_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="backend"):
        NextOpenExecution(cash=CASH, backend="rust")  # type: ignore[arg-type]


def test_the_environment_variable_selects_the_backend(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HONBA_SIM_BACKEND", "python")
    assert NextOpenExecution(cash=CASH).backend == "python"
    monkeypatch.setenv("HONBA_SIM_BACKEND", " PYTHON ")
    assert resolve_backend(None) == "python"


@needs_native
def test_the_constructor_argument_beats_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HONBA_SIM_BACKEND", "python")
    assert NextOpenExecution(cash=CASH, backend="native").backend == "native"


def test_a_bad_environment_value_is_a_value_error(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HONBA_SIM_BACKEND", "gpu")
    with pytest.raises(ValueError, match="HONBA_SIM_BACKEND"):
        NextOpenExecution(cash=CASH)


@pytest.mark.parametrize("backend", ["python", "native"])
def test_make_simulator_builds_on_the_requested_backend(
    backend: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    if backend == "native" and not _have_native():
        pytest.skip("native extension not built")
    monkeypatch.setenv("HONBA_SIM_BACKEND", backend)
    sim = make_simulator(fill="next_open", cash=CASH, costs="india.equity", settlement_days=1)
    assert isinstance(sim, NextOpenExecution) and sim.backend == backend


@pytest.mark.parametrize("backend", ["python", "native"])
def test_a_cost_in_another_currency_fails_before_the_order_is_dequeued(backend: str) -> None:
    """Regression (ADR 0016, chunk 3b): Python used to dequeue a sell, then fail on the currency.

    A failing cost must leave the order working and the state unchanged on every backend, as
    for a negative cost.
    """
    if backend == "native" and not _have_native():
        pytest.skip("native extension not built")

    def usd_cost(side: Any, quantity: float, price: float) -> Money:
        return Money.from_minor(5, Currency.USD)

    port = NextOpenExecution(cash=CASH, costs=usd_cost, backend=backend)
    port.positions[A] = 5.0
    port.open_session(1, [Bar(A, 1, 10.0, 10.0, 10.0, 10.0, 1.0)])
    port.submit("s", OrderIntent.market_sell(A, 2), 1)
    with pytest.raises(ValueError, match="currency"):
        port.open_session(2, [Bar(A, 2, 10.0, 10.0, 10.0, 10.0, 1.0)])
    assert port.working_orders == ["s"]
    assert port.cash == CASH and port.positions == {A: 5.0}
    assert port.drain_fills() == [] and port.drain_rejections() == []
