"""Public AI strategy verifier (E5-S5).

``verify_source`` runs the static scan first and short-circuits on any violation, so unsafe code
is never executed; a statically clean source is then run in the resource-limited sandbox
(``sandbox.py``) for the deterministic smoke and look-ahead checks. ``verify_strategy_file`` is the
file-shaped convenience the research loop and example scripts use.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from honba.ai.verification.findings import Finding
from honba.ai.verification.sandbox import SandboxLimits, SandboxOutcome, run_in_sandbox
from honba.ai.verification.static import scan_source
from honba.entities.bar import Bar
from honba.strategies.config import StrategyConfig

__all__ = [
    "DEFAULT_LIMITS",
    "DEFAULT_TIMEOUT_S",
    "VerificationReport",
    "verify_source",
    "verify_strategy_file",
]

DEFAULT_TIMEOUT_S = 30.0
DEFAULT_LIMITS = SandboxLimits()


@dataclass(frozen=True, slots=True)
class VerificationReport:
    """The verifier's verdict: the static findings and, unless short-circuited, the sandbox run."""

    source: str
    static: tuple[Finding, ...] = ()
    sandbox: SandboxOutcome | None = None

    @property
    def ok(self) -> bool:
        """True only when the static scan is clean and the sandbox run succeeded."""
        if self.static:
            return False
        return self.sandbox is not None and self.sandbox.ok

    @property
    def findings(self) -> tuple[Finding, ...]:
        """Every finding, static first then sandbox (empty sandbox when short-circuited)."""
        if self.sandbox is None:
            return self.static
        return self.static + self.sandbox.findings


def verify_source(
    source: str,
    *,
    filename: str = "<strategy>",
    config: StrategyConfig | Mapping[str, Any] | None = None,
    bars: Iterable[Bar] | None = None,
    limits: SandboxLimits | None = None,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    check_look_ahead: bool = True,
) -> VerificationReport:
    """Verify untrusted strategy ``source``: static scan, then sandboxed smoke and look-ahead."""
    static_findings = tuple(scan_source(source, filename=filename))
    if static_findings:
        return VerificationReport(source, static_findings, None)
    outcome = run_in_sandbox(
        source,
        filename=filename,
        config=config,
        bars=bars,
        limits=limits or DEFAULT_LIMITS,
        timeout_s=timeout_s,
        check_look_ahead=check_look_ahead,
    )
    return VerificationReport(source, (), outcome)


def verify_strategy_file(path: str | Path, **kwargs: Any) -> VerificationReport:
    """Verify the strategy file at ``path``, reading ``config.toml`` beside it when present."""
    strategy_path = Path(path)
    source = strategy_path.read_text(encoding="utf-8")
    if kwargs.get("config") is None:
        config_path = strategy_path.parent / "config.toml"
        if config_path.is_file():
            kwargs["config"] = StrategyConfig.from_toml(config_path)
    kwargs.setdefault("filename", str(strategy_path))
    return verify_source(source, **kwargs)
