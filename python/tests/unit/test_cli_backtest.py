"""``honba backtest``: arguments to a run through ``honba.client``, and the exit codes.

The client is replaced by a scripted fake so no native code or server is involved.
Exit codes (documented in ``--help``): 0 completed, 1 failed run, 2 usage or validation (422),
3 transport, queue (429/503) or any other API failure.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from honba.cli import backtest as cli
from honba.cli.main import app
from honba.client import (
    ApiError,
    BacktestResult,
    RateLimitedApiError,
    RunTimeoutError,
    TransportApiError,
    UnsupportedApiError,
    ValidationApiError,
)

runner = CliRunner()
RUN_ID = "01ARZ3NDEKTSV4RRFFQ69G5FAV"
ARGS = [
    "backtest",
    "--data-dir",
    "bars",
    "--strategy",
    "sma_crossover",
    "--universe",
    "TCS.NSE",
    "--start",
    "2024-01-01",
    "--end",
    "2024-06-01",
    "--seed",
    "42",
]
METRICS = {"trades": 2, "net_pnl": 12.5, "sharpe": 1.5, "max_drawdown": 0.1, "total_return": 0.01}
ASSUMPTIONS = {"not_modelled": ["slippage"], "timing": "close of the decision bar"}


def result(status: str, **extra: Any) -> BacktestResult:
    return BacktestResult.model_validate({"run_id": RUN_ID, "status": status, **extra})


class FakeClient:
    def __init__(self, outcome: BacktestResult | Exception) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, Any]] = []
        self.closed = False

    def run_backtest(self, **kwargs: Any) -> BacktestResult:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        return self.outcome

    def close(self) -> None:
        self.closed = True


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> list[FakeClient]:
    made: list[FakeClient] = []
    outcome: list[BacktestResult | Exception] = [result("completed")]

    def install(value: BacktestResult | Exception) -> FakeClient:
        client = FakeClient(value)
        made.append(client)
        return client

    monkeypatch.setattr(cli, "_open_client", lambda **kw: install(outcome[0]))
    made.append(outcome)  # type: ignore[arg-type]
    return made


def use(fake_state: list[Any], outcome: BacktestResult | Exception) -> None:
    fake_state[0][0] = outcome


def invoke(args: list[str]) -> Any:
    return runner.invoke(app, args)


def test_completed_run_exits_0_and_prints_machine_readable_json(fake: list[Any]) -> None:
    use(fake, result("completed", metrics=METRICS, assumptions=ASSUMPTIONS))
    res = invoke(ARGS)
    assert res.exit_code == 0, res.output
    out = json.loads(res.stdout)
    assert out["run_id"] == RUN_ID and out["status"] == "completed"
    assert out["metrics"]["trades"] == 2
    assert out["assumptions"]["not_modelled"] == ["slippage"]


def test_cli_exit_codes(fake: list[Any]) -> None:
    """Acceptance E4-S3 ``cli_exit_codes``: 0 completed, 1 failed, 2 validation, 3 transport."""
    failed = result(
        "failed", error={"code": "internal_error", "message": "boom", "retryable": False}
    )
    cases: list[tuple[BacktestResult | Exception, int]] = [
        (result("completed", metrics=METRICS, assumptions=ASSUMPTIONS), 0),
        (failed, 1),
        (ValidationApiError("validation_invalid_request", "bad", status=422), 2),
        (RateLimitedApiError("rate_limited", "full", status=429, retryable=True), 3),
        (UnsupportedApiError("unsupported", "no journals", status=503), 3),
        (TransportApiError("transport_error", "refused", retryable=True), 3),
        (ApiError("internal_error", "oops", status=500), 3),
        (RunTimeoutError(RUN_ID, result("running"), 1.0), 3),
    ]
    for outcome, code in cases:
        use(fake, outcome)
        res = invoke(ARGS)
        assert res.exit_code == code, (outcome, res.output)


def test_a_failed_run_prints_its_error_and_the_result(fake: list[Any]) -> None:
    use(
        fake,
        result(
            "failed",
            error={"code": "market_data_unavailable", "message": "no bars", "retryable": True},
        ),
    )
    res = invoke(ARGS)
    assert res.exit_code == 1
    assert json.loads(res.stdout)["error"]["code"] == "market_data_unavailable"
    assert "market_data_unavailable" in res.stderr and "no bars" in res.stderr


def test_a_server_validation_error_names_the_field_on_stderr(fake: list[Any]) -> None:
    use(
        fake,
        ValidationApiError(
            "validation_invalid_request",
            "strategy 'x' is not registered",
            status=422,
            context={"field": "strategy", "reason": "unknown_strategy"},
        ),
    )
    res = invoke(ARGS)
    assert res.exit_code == 2
    assert "strategy" in res.stderr and "unknown_strategy" in res.stderr


def test_the_request_reaches_the_client(fake: list[Any]) -> None:
    use(fake, result("completed", metrics=METRICS, assumptions=ASSUMPTIONS))
    res = invoke([*ARGS, "--bar-spec", "1d", "--initial-capital", "500000", "--timeout", "7"])
    assert res.exit_code == 0, res.output
    [client] = fake[1:]
    [call] = client.calls
    assert call["strategy"] == "sma_crossover" and call["universe"] == "TCS.NSE"
    assert (call["start"], call["end"], call["seed"]) == ("2024-01-01", "2024-06-01", 42)
    assert (call["bar_spec"], call["initial_capital"], call["timeout"]) == ("1d", 500000.0, 7.0)
    assert client.closed


@pytest.mark.parametrize(
    "args",
    [
        ["backtest"],  # nothing at all
        [a for a in ARGS if a not in ("--data-dir", "bars")],  # no data-dir or url
        [*ARGS, "--url", "http://127.0.0.1:1"],  # both
        [*ARGS[:-2], "--seed", "0"],
        [*ARGS[:-2], "--seed", "abc"],
        [*ARGS, "--format", "yaml"],
        [*ARGS, "--timeout", "0"],
    ],
)
def test_usage_errors_exit_2_without_touching_a_client(fake: list[Any], args: list[str]) -> None:
    res = invoke(args)
    assert res.exit_code == 2, res.output
    assert len(fake) == 1  # only the outcome slot: no client was opened


def test_a_manifest_file_supplies_the_request_and_flags_override_it(
    fake: list[Any], tmp_path: Path
) -> None:
    manifest = tmp_path / "run.json"
    manifest.write_text(
        json.dumps(
            {
                "strategy": "buy_and_hold",
                "universe": "INFY.NSE",
                "start": "2024-01-01",
                "end": "2024-02-01",
                "seed": 7,
                "bar_spec": "1d",
            }
        )
    )
    use(fake, result("completed", metrics=METRICS, assumptions=ASSUMPTIONS))
    res = invoke(["backtest", str(manifest), "--data-dir", "bars", "--seed", "9"])
    assert res.exit_code == 0, res.output
    [call] = fake[1].calls
    assert (call["strategy"], call["universe"], call["seed"]) == ("buy_and_hold", "INFY.NSE", 9)
    assert call["bar_spec"] == "1d"


def test_a_toml_manifest_is_read_too(fake: list[Any], tmp_path: Path) -> None:
    manifest = tmp_path / "run.toml"
    manifest.write_text(
        'strategy = "buy_and_hold"\nuniverse = "INFY.NSE"\nstart = "2024-01-01"\n'
        'end = "2024-02-01"\nseed = 3\n'
    )
    res = invoke(["backtest", str(manifest), "--data-dir", "bars"])
    assert res.exit_code == 0, res.output
    assert fake[1].calls[0]["seed"] == 3


@pytest.mark.parametrize(
    "text",
    ["not json", '["a"]', '{"strategy": "x", "bogus": 1}', '{"seed": "nine"}'],
)
def test_a_bad_manifest_is_a_usage_error(fake: list[Any], tmp_path: Path, text: str) -> None:
    manifest = tmp_path / "run.json"
    manifest.write_text(text)
    res = invoke(["backtest", str(manifest), "--data-dir", "bars"])
    assert res.exit_code == 2, res.output


def test_help_documents_the_exit_codes() -> None:
    res = invoke(["backtest", "--help"])
    assert res.exit_code == 0
    text = " ".join(res.stdout.split())
    for needle in ("Exit codes", "0 completed", "1 failed", "2 usage", "3 transport"):
        assert needle in text, text


def test_plain_format_prints_a_summary(fake: list[Any]) -> None:
    use(fake, result("completed", metrics=METRICS, assumptions=ASSUMPTIONS))
    res = invoke([*ARGS, "--format", "plain"])
    assert res.exit_code == 0
    assert RUN_ID in res.stdout and "slippage" in res.stdout
