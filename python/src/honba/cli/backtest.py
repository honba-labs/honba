"""``honba backtest``: submit one backtest run, wait for it, print the result.

Story E4-S3 (ADR 0017 decision 9). It is a thin front end over :class:`honba.client.Client`:
the run is executed by the Rust run service, either in this process (``--data-dir``, journals
under ``--journals-dir``) or on a running ``honba serve`` (``--url``). There is no second
execution path; the native ``honba-cli backtest`` stays a single-shot command.

The request comes from flags, from a manifest file (JSON or TOML, same keys as the flags), or
both; a flag overrides the file.
"""

from __future__ import annotations

import json
import sys
from datetime import date, datetime
from pathlib import Path
from typing import Annotated, Any

import typer

from honba.client import (
    ApiError,
    BacktestResult,
    Client,
    RequestValidationError,
    RunTimeoutError,
    ValidationApiError,
)

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - Python 3.10
    import tomli as tomllib

EXIT_COMPLETED = 0
EXIT_FAILED = 1
EXIT_USAGE = 2
EXIT_TRANSPORT = 3

_MANIFEST_KEYS = ("strategy", "universe", "start", "end", "seed", "bar_spec", "initial_capital")
_FORMATS = ("json", "plain")


def _usage(message: str) -> typer.Exit:
    typer.echo(f"error: {message}", err=True)
    return typer.Exit(code=EXIT_USAGE)


def _load_manifest(path: Path) -> dict[str, Any]:
    """The run request in ``path`` (JSON or TOML by suffix); a usage error if unreadable."""
    try:
        text = path.read_text()
        data = tomllib.loads(text) if path.suffix == ".toml" else json.loads(text)
    except (OSError, ValueError) as exc:  # JSONDecodeError and TOMLDecodeError are ValueErrors
        raise _usage(f"cannot read manifest {path}: {exc}") from None
    if not isinstance(data, dict):
        raise _usage(f"manifest {path} must be an object")
    unknown = sorted(set(data) - set(_MANIFEST_KEYS))
    if unknown:
        raise _usage(f"manifest {path} has unknown keys {unknown}; allowed: {list(_MANIFEST_KEYS)}")
    out: dict[str, Any] = {}
    for key, value in data.items():
        if key in ("start", "end") and isinstance(value, (date, datetime)):
            value = value.isoformat()
        want: Any = int if key == "seed" else (int, float) if key == "initial_capital" else str
        if isinstance(value, bool) or not isinstance(value, want):
            raise _usage(f"manifest key {key!r} has the wrong type: {value!r}")
        out[key] = value
    return out


def _open_client(
    *,
    data_dir: Path | None,
    url: str | None,
    journals_dir: Path,
    max_concurrent_runs: int | None,
    max_queued_runs: int | None,
    http_timeout: float,
) -> Client:
    """The one construction point of the CLI's client (tests replace it)."""
    if url is not None:
        return Client.http(url, timeout=http_timeout)
    assert data_dir is not None
    return Client.inproc(
        data_dir,
        journals_dir=journals_dir,
        max_concurrent_runs=max_concurrent_runs,
        max_queued_runs=max_queued_runs,
    )


def _describe(error: ApiError) -> str:
    context = error.context if isinstance(error.context, dict) else {}
    detail = ", ".join(f"{k}={context[k]}" for k in ("field", "reason") if k in context)
    return f"{error.code}: {error.message}" + (f" ({detail})" if detail else "")


def _plain(result: BacktestResult) -> str:
    lines = [f"run_id: {result.run_id}", f"status: {result.status}"]
    if result.metrics is not None:
        lines += [f"{k}: {v}" for k, v in result.metrics.model_dump().items()]
    if result.assumptions is not None:
        lines.append(f"timing: {result.assumptions.timing}")
        lines.append("not_modelled: " + ", ".join(result.assumptions.not_modelled))
    if result.error is not None:
        lines.append(f"error: {result.error.code}: {result.error.message}")
    return "\n".join(lines)


def backtest(
    manifest: Annotated[
        Path | None,
        typer.Argument(help="Run manifest (.json or .toml) with the same keys as the flags."),
    ] = None,
    strategy: Annotated[
        str | None, typer.Option(help="Rust-registered strategy name (others are rejected).")
    ] = None,
    universe: Annotated[str | None, typer.Option(help="One instrument, SYMBOL.EXCHANGE.")] = None,
    start: Annotated[str | None, typer.Option(help="Inclusive start date or RFC 3339.")] = None,
    end: Annotated[str | None, typer.Option(help="Exclusive end date or RFC 3339.")] = None,
    seed: Annotated[int | None, typer.Option(help="Run seed, >= 1 (required).")] = None,
    bar_spec: Annotated[str | None, typer.Option(help="Bar size, e.g. 1d or 1m.")] = None,
    initial_capital: Annotated[float | None, typer.Option(help="Starting cash.")] = None,
    data_dir: Annotated[
        Path | None,
        typer.Option(help="Run in this process over SYMBOL.EXCHANGE.parquet files here."),
    ] = None,
    url: Annotated[
        str | None, typer.Option(help="Run on a `honba serve` at this base URL instead.")
    ] = None,
    journals_dir: Annotated[
        Path, typer.Option(help="Run journals root for --data-dir runs.")
    ] = Path("data/journals"),
    max_concurrent_runs: Annotated[
        int | None, typer.Option(help="In-process worker threads (default: server default).")
    ] = None,
    max_queued_runs: Annotated[
        int | None, typer.Option(help="In-process pending-queue bound (default: 64).")
    ] = None,
    timeout: Annotated[float, typer.Option(help="Seconds to wait for the run to finish.")] = 300.0,
    poll_interval: Annotated[float, typer.Option(help="Seconds between status polls.")] = 0.1,
    format: Annotated[
        str, typer.Option("--format", "-f", help="json (default) or plain.")
    ] = "json",
) -> None:
    """Run one backtest through the Honba run service and print the result.

    Prints the terminal run (status, metrics, assumptions including `not_modelled`, and the
    error of a failed run) as JSON on stdout; diagnostics go to stderr.

    Exit codes: 0 completed; 1 failed run; 2 usage or validation (422); 3 transport or queue
    (429 queue full, 503, connection failure, poll timeout).
    """
    if format not in _FORMATS:
        raise _usage(f"--format must be one of {', '.join(_FORMATS)}")
    if (data_dir is None) == (url is None):
        raise _usage("give exactly one of --data-dir (in process) or --url (running server)")
    for name, value in (("--timeout", timeout), ("--poll-interval", poll_interval)):
        if not value > 0:
            raise _usage(f"{name} must be > 0")
    fields: dict[str, Any] = _load_manifest(manifest) if manifest is not None else {}
    flags = {
        "strategy": strategy,
        "universe": universe,
        "start": start,
        "end": end,
        "seed": seed,
        "bar_spec": bar_spec,
        "initial_capital": initial_capital,
    }
    fields.update({k: v for k, v in flags.items() if v is not None})
    missing = [k for k in ("strategy", "universe", "start", "end", "seed") if k not in fields]
    if missing:
        raise _usage("missing " + ", ".join(f"--{m.replace('_', '-')}" for m in missing))
    if fields["seed"] < 1:
        raise _usage("--seed must be >= 1")

    client: Client | None = None
    try:
        client = _open_client(
            data_dir=data_dir,
            url=url,
            journals_dir=journals_dir,
            max_concurrent_runs=max_concurrent_runs,
            max_queued_runs=max_queued_runs,
            http_timeout=max(10.0, poll_interval * 10),
        )
        result = client.run_backtest(**fields, timeout=timeout, poll_interval=poll_interval)
    except (RequestValidationError, NotADirectoryError, ValueError) as exc:
        raise _usage(str(exc)) from None
    except ValidationApiError as exc:
        typer.echo(f"error: {_describe(exc)}", err=True)
        raise typer.Exit(code=EXIT_USAGE) from None
    except ApiError as exc:
        typer.echo(f"error: {_describe(exc)}", err=True)
        raise typer.Exit(code=EXIT_TRANSPORT) from None
    except RunTimeoutError as exc:
        typer.echo(f"error: {exc} (the run continues; poll run_id {exc.run_id})", err=True)
        raise typer.Exit(code=EXIT_TRANSPORT) from None
    except (ImportError, RuntimeError) as exc:
        typer.echo(f"error: native extension unavailable: {exc}", err=True)
        raise typer.Exit(code=EXIT_TRANSPORT) from None
    finally:
        if client is not None:
            client.close()

    if format == "json":
        typer.echo(json.dumps(result.model_dump(mode="json", exclude_none=True), indent=2))
    else:
        typer.echo(_plain(result))
    if result.status != "completed":
        why = f"{result.error.code}: {result.error.message}" if result.error else "no detail"
        typer.echo(f"error: run {result.run_id} {result.status}: {why}", err=True)
        raise typer.Exit(code=EXIT_FAILED)
