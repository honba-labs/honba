"""Subprocess sandbox with resource limits for the AI strategy verifier (E5-S5, D7).

D7 chose "subprocess with rlimits" over a container: the sandbox is a fresh interpreter that
applies ``resource`` limits to itself, installs an audit hook that denies network, process and
filesystem-mutation events, executes the strategy, and reports back as one sentinel-prefixed JSON
line. The parent also enforces a wall-clock timeout. See ``docs/adr/0022-ai-strategy-verifier.md``.

The static scan (``static.py``) runs first and short-circuits on any violation, so untrusted code
only ever reaches this sandbox when it is already allowlisted; the sandbox is defense in depth, not
the only line.
"""

from __future__ import annotations

import dataclasses
import json
import os
import signal
import subprocess
import sys
import tempfile
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from honba.ai.verification.findings import Finding, FindingCode
from honba.entities.bar import Bar
from honba.entities.instrument import InstrumentId
from honba.strategies.config import StrategyConfig

__all__ = [
    "RESULT_PREFIX",
    "SandboxLimits",
    "SandboxOutcome",
    "default_bars",
    "run_in_sandbox",
]

RESULT_PREFIX = "@@HONBA_VERIFY@@"
"""Sentinel that prefixes the one JSON result line on the child's stdout."""

_DEFAULT_SYMBOL = "NIFTY"
_DEFAULT_EXCHANGE = "NSE"


@dataclass(frozen=True, slots=True)
class SandboxLimits:
    """Resource limits applied in the child before the strategy is imported.

    ``address_space_mb`` is *additional* address space beyond the interpreter's baseline (measured
    inside the child after ``honba`` is imported), so a limit does not fight the numeric stack.
    """

    cpu_seconds: int = 10
    address_space_mb: int = 2048
    file_size_mb: int = 8
    open_files: int = 256


@dataclass(frozen=True, slots=True)
class SandboxOutcome:
    """What one sandboxed run produced."""

    ok: bool
    exit_code: int | None
    duration_s: float
    smoke: dict[str, Any] | None
    look_ahead: dict[str, Any] | None
    findings: tuple[Finding, ...] = field(default_factory=tuple)
    stdout: str = ""
    stderr: str = ""


def default_bars(count: int = 32) -> list[Bar]:
    """A deterministic synthetic bar series for callers that do not supply one."""
    bars: list[Bar] = []
    instrument = InstrumentId(_DEFAULT_SYMBOL, _DEFAULT_EXCHANGE)
    price = 100.0
    for i in range(count):
        step = 2.0 if (i // 4) % 2 == 0 else -2.0
        open_price = price
        close = price + step
        bars.append(
            Bar(
                instrument,
                (i + 1) * 1_000_000_000,
                open_price,
                max(open_price, close) + 0.5,
                min(open_price, close) - 0.5,
                close,
                1_000.0,
            )
        )
        price = close
    return bars


def _config_dict(config: StrategyConfig | Mapping[str, Any] | None) -> dict[str, Any]:
    if config is None:
        return {"name": "strategy", "symbol": _DEFAULT_SYMBOL, "exchange": _DEFAULT_EXCHANGE}
    if isinstance(config, StrategyConfig):
        return config.model_dump()
    return dict(config)


def _bars_payload(bars: Iterable[Bar]) -> list[dict[str, Any]]:
    return [
        {
            "symbol": bar.instrument_id.symbol,
            "exchange": bar.instrument_id.exchange,
            "ts": bar.ts,
            "open": bar.open,
            "high": bar.high,
            "low": bar.low,
            "close": bar.close,
            "volume": bar.volume,
        }
        for bar in bars
    ]


def _pythonpath() -> str:
    import honba

    return str(Path(honba.__file__).resolve().parent.parent)


def _child_env(tmp: str) -> dict[str, str]:
    return {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "TMPDIR": tmp,
        "LANG": "C.UTF-8",
        "PYTHONPATH": _pythonpath(),
        "PYTHONHASHSEED": "0",
    }


def _parse_result(result_path: Path) -> dict[str, Any] | None:
    try:
        lines = result_path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return None
    for line in reversed(lines):
        if line.startswith(RESULT_PREFIX):
            try:
                parsed = json.loads(line[len(RESULT_PREFIX) :])
            except ValueError:
                return None
            return parsed if isinstance(parsed, dict) else None
    return None


def _read_log(path: Path) -> str:
    """Read a child log file, capped to a small sanity bound (RLIMIT_FSIZE is the real cap)."""
    try:
        return path.read_bytes()[: 2 * 1024 * 1024].decode("utf-8", "replace")
    except OSError:
        return ""


def _signal_name(returncode: int) -> str:
    try:
        return signal.Signals(-returncode).name
    except ValueError:
        return f"signal {-returncode}"


def run_in_sandbox(
    source: str,
    *,
    filename: str = "<strategy>",
    config: StrategyConfig | Mapping[str, Any] | None = None,
    bars: Iterable[Bar] | None = None,
    limits: SandboxLimits | None = None,
    timeout_s: float = 30.0,
    check_look_ahead: bool = True,
) -> SandboxOutcome:
    """Run ``source`` in a resource-limited child and return its outcome.

    This does **not** run the static scan; the caller (``verify_source``) does that first. It is
    public so the runtime guards (network, fork, rlimits) can be tested directly.

    The verdict reaches the parent as a sentinel-prefixed line in a **result file** the child opens
    through a parent-provided path, not through the child's stdout: the strategy can write its own
    stdout, so stdout is captured for diagnostics only and never trusted. Child stdout/stderr are
    redirected to files under ``RLIMIT_FSIZE``, so a chatty strategy is capped, not buffered into
    the verifier's memory.
    """
    effective_limits = limits or SandboxLimits()
    bar_list = list(bars) if bars is not None else default_bars()
    command = [sys.executable, "-B", "-m", "honba.ai.verification._bootstrap"]
    start = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="honba-verify-") as tmp:
        tmp_dir = Path(tmp)
        result_path = tmp_dir / "result.ndjson"
        job = {
            "source": source,
            "filename": filename,
            "config": _config_dict(config),
            "bars": _bars_payload(bar_list),
            "limits": dataclasses.asdict(effective_limits),
            "check_look_ahead": check_look_ahead,
            "result_path": str(result_path),
        }
        stdout_path = tmp_dir / "out.log"
        stderr_path = tmp_dir / "err.log"
        try:
            with stdout_path.open("wb") as stdout_file, stderr_path.open("wb") as stderr_file:
                try:
                    completed = subprocess.run(
                        command,
                        input=json.dumps(job).encode("utf-8"),
                        stdout=stdout_file,
                        stderr=stderr_file,
                        timeout=timeout_s,
                        cwd=tmp,
                        env=_child_env(tmp),
                        check=False,
                    )
                except subprocess.TimeoutExpired:
                    duration = time.monotonic() - start
                    stdout = _read_log(stdout_path)
                    stderr = _read_log(stderr_path)
                    finding = Finding(
                        FindingCode.SANDBOX_TIMEOUT,
                        f"strategy did not finish within {timeout_s:g}s",
                    )
                    return SandboxOutcome(
                        False, None, duration, None, None, (finding,), stdout, stderr
                    )
        except OSError:
            duration = time.monotonic() - start
            finding = Finding(FindingCode.SANDBOX_CRASH, "sandbox could not open its log files")
            return SandboxOutcome(False, None, duration, None, None, (finding,), "", "")
        # Read the result *before* the TemporaryDirectory context exits (it deletes the dir on exit).
        result = _parse_result(result_path)
        stdout = _read_log(stdout_path)
        stderr = _read_log(stderr_path)
    duration = time.monotonic() - start
    if result is None:
        return _crashed(completed.returncode, duration, stdout, stderr)  # type: ignore[union-attr]
    findings = tuple(Finding.from_dict(item) for item in result.get("findings", []))
    return SandboxOutcome(
        bool(result.get("ok")),
        completed.returncode,  # type: ignore[union-attr]
        duration,
        result.get("smoke"),
        result.get("look_ahead"),
        findings,
        stdout,
        stderr,
    )


def _crashed(returncode: int | None, duration: float, stdout: str, stderr: str) -> SandboxOutcome:
    if returncode is not None and returncode < 0:
        name = _signal_name(returncode)
        finding = Finding(FindingCode.SANDBOX_LIMIT, f"strategy killed by {name} (resource limit)")
    else:
        finding = Finding(
            FindingCode.SANDBOX_CRASH,
            f"verifier child exited with code {returncode}",
        )
    return SandboxOutcome(False, returncode, duration, None, None, (finding,), stdout, stderr)
