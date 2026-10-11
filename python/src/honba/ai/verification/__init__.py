"""AI strategy verifier (E5-S5): static AST scan plus a resource-limited sandbox run.

    from honba.ai.verification import verify_source

    report = verify_source(llm_output, bars=my_bars)
    if not report.ok:
        raise SystemExit([f.code for f in report.findings])

See ``docs/adr/0022-ai-strategy-verifier.md``. Verification is safety (allowlisted, no network, no
process, deterministic, causal), not correctness.
"""

from honba.ai.verification.findings import Finding, FindingCode
from honba.ai.verification.lookahead import (
    LookAheadReport,
    LookAheadViolation,
    check_look_ahead,
)
from honba.ai.verification.sandbox import SandboxLimits, SandboxOutcome, run_in_sandbox
from honba.ai.verification.static import ALLOWED_IMPORTS, scan_source
from honba.ai.verification.verifier import (
    VerificationReport,
    verify_source,
    verify_strategy_file,
)

__all__ = [
    "ALLOWED_IMPORTS",
    "Finding",
    "FindingCode",
    "LookAheadReport",
    "LookAheadViolation",
    "SandboxLimits",
    "SandboxOutcome",
    "VerificationReport",
    "check_look_ahead",
    "run_in_sandbox",
    "scan_source",
    "verify_source",
    "verify_strategy_file",
]
