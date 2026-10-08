"""Backtest runs through ``Client`` against a real in-process server and a real ``honba serve``.

Both transports run the same scenarios and must agree (ADR 0017 decision 9). The HTTP leg is
skipped when the binary is not built (``cargo build -p honba-cli``, or ``HONBA_BIN``).
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest
from _runs_support import END, START, open_clients, scratch, walk, write_bars

from honba.client import (
    BacktestResult,
    Client,
    NotFoundApiError,
    RateLimitedApiError,
    UnsupportedApiError,
    ValidationApiError,
)

pytest.importorskip("honba._honba")

RUN: dict[str, Any] = {
    "strategy": "sma_crossover",
    "universe": "TCS.NSE",
    "start": START,
    "end": END,
    "seed": 42,
    "bar_spec": "1m",
}


@pytest.fixture(scope="module")
def both() -> Iterator[dict[str, Client]]:
    with scratch("client-runs") as root:
        data = root / "bars"
        data.mkdir()
        write_bars(data, "TCS", walk(300))
        with open_clients(data, root / "journals") as found:
            yield found


@pytest.fixture(params=["inproc", "http"])
def client(both: dict[str, Client], request: pytest.FixtureRequest) -> Client:
    if request.param not in both:
        pytest.skip("honba binary not built (cargo build -p honba-cli, or set HONBA_BIN)")
    return both[request.param]


def test_a_run_completes_with_metrics_assumptions_and_a_journal(client: Client) -> None:
    done = client.run_backtest(**RUN, timeout=60)
    assert done.status == "completed" and done.error is None
    assert done.metrics is not None and done.metrics.trades >= 1
    assert done.assumptions is not None
    assert "slippage" in done.assumptions.not_modelled
    assert done.assumptions.timing
    fills = client.backtest_journal(done.run_id)
    assert len(fills) >= 2 * done.metrics.trades
    assert client.backtest(done.run_id) == done  # terminal is final


def test_submit_returns_a_pending_run_with_a_ulid(client: Client) -> None:
    submitted = client.submit_backtest(**RUN)
    assert submitted.status in ("pending", "running", "completed")
    assert len(submitted.run_id) == 26
    client.wait(submitted.run_id, timeout=60)


def test_same_seed_same_data_same_result(client: Client) -> None:
    first = client.run_backtest(**RUN, timeout=60)
    second = client.run_backtest(**RUN, timeout=60)
    assert first.run_id != second.run_id
    assert first.model_dump(exclude={"run_id"}) == second.model_dump(exclude={"run_id"})
    assert client.backtest_journal(first.run_id) == client.backtest_journal(second.run_id)


def test_a_run_over_a_window_without_bars_fails_with_a_typed_error(client: Client) -> None:
    failed = client.run_backtest(**{**RUN, "start": "2030-01-01", "end": "2030-02-01"}, timeout=60)
    assert failed.status == "failed" and failed.metrics is None
    assert failed.error is not None
    assert failed.error.code == "market_data_unavailable"
    assert failed.error.context["reason"] == "no_data"


def test_an_unregistered_strategy_is_422_naming_the_field(client: Client) -> None:
    with pytest.raises(ValidationApiError) as caught:
        client.submit_backtest(**{**RUN, "strategy": "momentum_alpha"})
    assert caught.value.status == 422
    assert caught.value.context["field"] == "strategy"


def test_an_unknown_run_is_404(client: Client) -> None:
    for call in (client.backtest, client.backtest_journal):
        with pytest.raises(NotFoundApiError) as caught:
            call("01ARZ3NDEKTSV4RRFFQ69G5FAV")
        assert caught.value.status == 404
    with pytest.raises(NotFoundApiError):
        client.backtest("not-a-ulid")


def test_both_transports_give_the_same_answers(both: dict[str, Client]) -> None:
    if set(both) != {"inproc", "http"}:
        pytest.skip("honba binary not built")

    def outcome(c: Client, call: Any) -> Any:
        try:
            result = call(c)
        except ValidationApiError as err:
            return ("422", err.code, err.message, err.context)
        return (
            result.model_dump(exclude={"run_id"}) if isinstance(result, BacktestResult) else result
        )

    for call in (
        lambda c: c.run_backtest(**RUN, timeout=60),
        lambda c: c.run_backtest(**{**RUN, "start": "2030-01-01", "end": "2030-02-01"}, timeout=60),
        lambda c: c.submit_backtest(**{**RUN, "strategy": "nope"}),
        lambda c: c.submit_backtest(**{**RUN, "universe": "A.NSE,B.NSE"}),
    ):
        assert outcome(both["inproc"], call) == outcome(both["http"], call)


def test_inproc_without_a_journals_dir_is_503_unsupported() -> None:
    with scratch("client-runs-off") as root:
        data = root / "bars"
        data.mkdir()
        with Client.inproc(data) as c, pytest.raises(UnsupportedApiError) as caught:
            c.submit_backtest(**RUN)
        assert caught.value.status == 503
        assert caught.value.context["reason"] == "no_journals_dir"


def test_a_full_queue_is_429_rate_limited() -> None:
    """A long run holds the single worker; the queue of one fills; the next submit is 429."""
    with scratch("client-runs-429") as root:
        data = root / "bars"
        data.mkdir()
        write_bars(data, "TCS", walk(60_000))
        end = "2024-03-01"
        with Client.inproc(
            data, journals_dir=root / "j", max_concurrent_runs=1, max_queued_runs=1
        ) as c:
            ids: list[str] = []
            with pytest.raises(RateLimitedApiError) as caught:
                for _ in range(50):
                    ids.append(c.submit_backtest(**{**RUN, "end": end}).run_id)
            assert caught.value.status == 429
            assert caught.value.code == "rate_limited" and caught.value.retryable
            assert caught.value.context["reason"]
            for run_id in ids:
                c.wait(run_id, timeout=120)
