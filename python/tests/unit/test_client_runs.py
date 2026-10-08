"""Run methods of ``Client`` (ADR 0017 decision 9) over a scripted transport; no native code."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pytest

from honba._native import native_attr
from honba.client import (
    ApiError,
    BacktestResult,
    Client,
    NotFoundApiError,
    RateLimitedApiError,
    RequestValidationError,
    Response,
    RunTimeoutError,
    ValidationApiError,
)
from honba.client import requests as rq

RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
SCHEMA = native_attr("SCHEMA_VERSION")
ENVELOPE = {"api_version": "1.0.0", "schema_version": SCHEMA}


def ok(data: Any) -> Response:
    return Response(200, {**ENVELOPE, "data": data, "error": None})


def err(status: int, code: str, message: str = "m", **extra: Any) -> Response:
    detail = {"code": code, "message": message, "retryable": False, **extra}
    return Response(status, {**ENVELOPE, "data": None, "error": detail})


class Scripted:
    """A transport answering from a queue and recording what it was asked."""

    def __init__(self, *answers: Response) -> None:
        self.answers = list(answers)
        self.sent: list[tuple[str, str, Mapping[str, Any] | None, Any]] = []

    def request(
        self,
        method: str,
        path: str,
        *,
        query: Mapping[str, Any] | None = None,
        body: Any = None,
    ) -> Response:
        self.sent.append((method, path, query, body))
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


METRICS = {"trades": 2, "net_pnl": 12.5, "sharpe": 1.5, "max_drawdown": 0.1, "total_return": 0.01}
ASSUMPTIONS = {
    "not_modelled": ["transaction_costs", "slippage"],
    "timing": "market orders fill at the close of the bar on which the strategy decided",
    "fill_model": "bar_fill",
}


def run(status: str, **extra: Any) -> dict[str, Any]:
    return {"run_id": RUN_ID, "status": status, **extra}


SUBMIT: dict[str, Any] = {
    "strategy": "sma_crossover",
    "universe": "TCS.NSE",
    "start": "2024-01-01",
    "end": "2024-06-01",
    "seed": 42,
}


def test_submit_backtest_posts_the_request_and_parses_the_pending_run() -> None:
    transport = Scripted(ok(run("pending")))
    result = Client(transport).submit_backtest(**SUBMIT, bar_spec="1d", initial_capital=1e6)
    assert isinstance(result, BacktestResult)
    assert (result.run_id, result.status) == (RUN_ID, "pending")
    assert result.metrics is None and result.assumptions is None and result.error is None
    assert not result.is_terminal
    [(method, path, query, body)] = transport.sent
    assert (method, path, query) == ("POST", "/backtests", None)
    assert body == {**SUBMIT, "bar_spec": "1d", "initial_capital": 1e6}


def test_submit_omits_unset_optional_fields_and_normalises_dates() -> None:
    import datetime as dt

    transport = Scripted(ok(run("pending")))
    Client(transport).submit_backtest(
        **{**SUBMIT, "start": dt.date(2024, 1, 1), "end": dt.date(2024, 6, 1)}
    )
    assert transport.sent[0][3] == SUBMIT


@pytest.mark.parametrize(
    ("override", "field"),
    [
        ({"seed": 0}, "seed"),
        ({"seed": -1}, "seed"),
        ({"seed": 1.5}, "seed"),
        ({"strategy": " "}, "strategy"),
        ({"universe": ""}, "universe"),
        ({"start": ""}, "start"),
        ({"initial_capital": -5.0}, "initial_capital"),
    ],
)
def test_a_bad_submit_is_rejected_before_anything_is_sent(
    override: dict[str, Any], field: str
) -> None:
    transport = Scripted(ok(run("pending")))
    with pytest.raises(RequestValidationError) as caught:
        Client(transport).submit_backtest(**{**SUBMIT, **override})
    assert caught.value.field == field
    assert transport.sent == []


def test_a_completed_run_carries_typed_metrics_and_assumptions() -> None:
    transport = Scripted(ok(run("completed", metrics=METRICS, assumptions=ASSUMPTIONS)))
    result = Client(transport).backtest(RUN_ID)
    assert transport.sent[0][:2] == ("GET", f"/backtests/{RUN_ID}")
    assert result.is_terminal and result.error is None
    assert result.metrics is not None and result.metrics.trades == 2
    assert result.metrics.net_pnl == 12.5
    assert result.assumptions is not None
    assert result.assumptions.not_modelled == ("transaction_costs", "slippage")
    assert result.assumptions.timing.startswith("market orders fill")


def test_result_has_not_modelled() -> None:
    """Acceptance E4-S3 ``result_has_not_modelled``: the typed list and timing are on the result."""
    result = Client(
        Scripted(ok(run("completed", metrics=METRICS, assumptions=ASSUMPTIONS)))
    ).backtest(RUN_ID)
    assert result.assumptions is not None
    assert isinstance(result.assumptions.not_modelled, tuple)
    assert all(isinstance(item, str) for item in result.assumptions.not_modelled)
    assert "slippage" in result.assumptions.not_modelled
    assert result.assumptions.timing


def test_a_failed_run_surfaces_its_error_as_a_model_not_an_exception() -> None:
    failed = run(
        "failed",
        assumptions=ASSUMPTIONS,
        error={
            "code": "market_data_unavailable",
            "message": "bar read failed",
            "retryable": True,
            "context": {"reason": "bar_read"},
        },
    )
    result = Client(Scripted(ok(failed))).backtest(RUN_ID)
    assert result.status == "failed" and result.is_terminal
    assert result.metrics is None
    assert result.error is not None
    assert (result.error.code, result.error.retryable) == ("market_data_unavailable", True)
    assert result.error.context == {"reason": "bar_read"}


def test_journal_returns_wire_trades() -> None:
    trade = {
        "order_id": "o1",
        "instrument_id": {"symbol": "TCS", "exchange": "NSE"},
        "side": "buy",
        "quantity": 10.0,
        "price": 101.5,
        "costs": {"amount": 0, "currency": "INR"},
        "ts_event": {"iso": "2024-01-02T00:00:00Z", "unix_nanos": "1704153600000000000"},
        "ts_init": {"iso": "2024-01-02T00:00:00Z", "unix_nanos": "1704153600000000000"},
    }
    transport = Scripted(ok({"trades": [trade]}))
    [fill] = Client(transport).backtest_journal(RUN_ID)
    assert transport.sent[0][:2] == ("GET", f"/backtests/{RUN_ID}/journal")
    assert (fill.order_id, fill.quantity, fill.price) == ("o1", 10.0, 101.5)


@pytest.mark.parametrize(
    ("status", "code", "klass"),
    [
        (404, "not_found", NotFoundApiError),
        (422, "validation_invalid_request", ValidationApiError),
        (429, "rate_limited", RateLimitedApiError),
    ],
)
def test_error_statuses_map_through_the_existing_error_classes(
    status: int, code: str, klass: type[ApiError]
) -> None:
    client = Client(Scripted(err(status, code, context={"reason": "x"})))
    for call in (
        lambda: client.submit_backtest(**SUBMIT),
        lambda: client.backtest(RUN_ID),
        lambda: client.backtest_journal(RUN_ID),
    ):
        with pytest.raises(klass) as caught:
            call()
        assert caught.value.status == status and caught.value.context == {"reason": "x"}


def test_a_run_id_is_percent_encoded_into_the_path() -> None:
    transport = Scripted(err(404, "not_found"))
    with pytest.raises(NotFoundApiError):
        Client(transport).backtest("../x/y")
    assert transport.sent[0][1] == "/backtests/..%2Fx%2Fy"
    with pytest.raises(RequestValidationError):
        Client(transport).backtest("")


def test_request_builders_are_pure() -> None:
    assert rq.get_backtest(RUN_ID).path == f"/backtests/{RUN_ID}"
    assert rq.backtest_journal(RUN_ID).path == f"/backtests/{RUN_ID}/journal"
    assert rq.submit_backtest(**SUBMIT).method == "POST"


class FakeClock:
    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def test_wait_polls_until_terminal() -> None:
    clock = FakeClock()
    transport = Scripted(
        ok(run("pending")),
        ok(run("running")),
        ok(run("completed", metrics=METRICS, assumptions=ASSUMPTIONS)),
    )
    result = Client(transport).wait(
        RUN_ID, timeout=10, poll_interval=0.5, sleep=clock.sleep, clock=clock.time
    )
    assert result.status == "completed"
    assert len(transport.sent) == 3 and clock.sleeps == [0.5, 0.5]


def test_wait_returns_a_failed_run_without_raising() -> None:
    failed = run("failed", error={"code": "internal_error", "message": "boom", "retryable": False})
    result = Client(Scripted(ok(failed))).wait(RUN_ID, timeout=1)
    assert result.status == "failed" and result.error is not None


def test_wait_times_out_with_the_last_seen_state() -> None:
    clock = FakeClock()
    transport = Scripted(ok(run("running")))
    with pytest.raises(RunTimeoutError) as caught:
        Client(transport).wait(
            RUN_ID, timeout=1.0, poll_interval=0.4, sleep=clock.sleep, clock=clock.time
        )
    assert caught.value.run_id == RUN_ID and caught.value.last.status == "running"
    assert clock.now <= 1.0 + 1e-9  # never sleeps past the deadline


@pytest.mark.parametrize("bad", [{"timeout": 0}, {"timeout": -1}, {"poll_interval": 0}])
def test_wait_rejects_bad_durations(bad: dict[str, float]) -> None:
    kwargs = {"timeout": 1.0, "poll_interval": 0.1, **bad}
    with pytest.raises(RequestValidationError):
        Client(Scripted(ok(run("running")))).wait(RUN_ID, **kwargs)


def test_run_backtest_submits_then_waits() -> None:
    clock = FakeClock()
    transport = Scripted(
        ok(run("pending")),
        ok(run("running")),
        ok(run("completed", metrics=METRICS, assumptions=ASSUMPTIONS)),
    )
    result = Client(transport).run_backtest(
        **SUBMIT, timeout=5, poll_interval=0.25, sleep=clock.sleep, clock=clock.time
    )
    assert result.status == "completed"
    assert [s[0] for s in transport.sent] == ["POST", "GET", "GET"]
