"""Report builder with a fact snapshot and numeric quality gate (ADR 0027, E5-S9).

An LLM-written narrative can silently invent numbers, so this module constrains it
against a frozen, self-contained :class:`FactSnapshot`: the ``narrator`` is the only
producer of the narrative and the snapshot is its only argument, the narrative is
wrapped as untrusted, and :func:`quality_gate` rejects any numeric token that is not
a normalised form of a number the snapshot already contains.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Iterable, Iterator, Mapping
from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation
from types import MappingProxyType
from typing import Any

from honba.ai.trust import TrustEnvelope, render_for_llm, wrap_untrusted

__all__ = [
    "FactSnapshot",
    "QualityReport",
    "QualityViolation",
    "Report",
    "build_report",
    "build_snapshot",
    "quality_gate",
]

_CURRENCY_SYMBOLS: tuple[str, ...] = ("₹", "$", "€", "£", "INR", "Rs.", "Rs")
_CURRENCY_PATTERN = r"(?:₹|\$|€|£|Rs\.?|INR)?"
_NUMBER_PATTERN = r"[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?(?:[eE][+-]?\d+)?"
_TOKEN_RE = re.compile(rf"{_CURRENCY_PATTERN}\s*{_NUMBER_PATTERN}\s*%?")
_MISSING = object()


def _group_int(text: str) -> str:
    """Group the integer part of a plain numeric string with thousands separators."""
    negative = text.startswith("-")
    body = text[1:] if negative else text
    if "." in body:
        int_part, frac = body.split(".", 1)
        grouped = f"{int(int_part):,}.{frac}"
    else:
        grouped = f"{int(body):,}"
    return f"-{grouped}" if negative else grouped


def _add_forms(forms: set[str], value: float) -> None:
    if not math.isfinite(value):
        forms.add(str(value))
        return
    forms.add(str(value))
    if value == int(value):
        forms.add(str(int(value)))
    two_dp = f"{value:.2f}"
    forms.add(two_dp)
    forms.add(_group_int(two_dp))
    forms.add(_group_int(str(value)))


def _number_forms(value: float) -> set[str]:
    """Every normalised textual form of ``value`` across raw, 2-dp, grouped, ×100 and ÷100."""
    forms: set[str] = set()
    _add_forms(forms, value)
    _add_forms(forms, value * 100.0)
    _add_forms(forms, value / 100.0)
    return forms


def _normalise_token(token: str) -> tuple[str, str, Decimal] | None:
    """Normalise an extracted numeric token to ``(plain, key, value)``.

    ``plain`` has currency, commas, spaces and ``%`` stripped (e.g. ``"1234.56"``);
    ``key`` keeps a trailing ``%`` when the token carried one. Returns ``None`` when
    the token cannot be parsed as a number.
    """
    text = token.strip()
    had_percent = text.endswith("%")
    if had_percent:
        text = text[:-1].strip()
    for symbol in _CURRENCY_SYMBOLS:
        if text.startswith(symbol):
            text = text[len(symbol) :].strip()
            break
    text = text.replace(",", "").replace(" ", "").replace("\u00a0", "")
    if not text:
        return None
    try:
        value = Decimal(text)
    except InvalidOperation:
        return None
    if not value.is_finite():
        return None
    key = f"{text}%" if had_percent else text
    return text, key, value


def _iter_token_values(text: str) -> Iterator[Decimal]:
    for match in _TOKEN_RE.finditer(text):
        parsed = _normalise_token(match.group())
        if parsed is not None:
            yield parsed[2]


def _allowed_decimals(strings: Iterable[str]) -> set[Decimal]:
    values: set[Decimal] = set()
    for entry in strings:
        text = entry.strip().removesuffix("%")
        text = text.replace(",", "").replace(" ", "")
        if not text:
            continue
        try:
            decimals = Decimal(text)
        except InvalidOperation:
            continue
        if decimals.is_finite():
            values.add(decimals)
    return values


@dataclass(frozen=True, slots=True)
class FactSnapshot:
    """An immutable, self-contained set of facts a narrative may cite."""

    run_id: str
    strategy: str
    metrics: Mapping[str, float]
    journal_ids: tuple[str, ...] = ()
    assumptions: tuple[str, ...] = ()
    schema_version: int = 1
    _metrics_raw: dict[str, float] | None = field(
        default=None, init=False, repr=False, compare=False
    )

    def __post_init__(self) -> None:
        raw = {str(name): float(value) for name, value in self.metrics.items()}
        object.__setattr__(self, "_metrics_raw", raw)
        object.__setattr__(self, "metrics", MappingProxyType(raw))

    def allowed_numbers(self) -> frozenset[str]:
        """The whole numeric vocabulary a narrative may cite, as normalised forms."""
        forms: set[str] = set()
        for value in self.metrics.values():
            forms |= _number_forms(float(value))
        for text in (self.run_id, *self.journal_ids, *self.assumptions):
            for token_value in _iter_token_values(text):
                forms |= _number_forms(float(token_value))
        return frozenset(forms)

    def to_dict(self) -> dict[str, Any]:
        """The snapshot as a JSON-shaped mapping."""
        return {
            "run_id": self.run_id,
            "strategy": self.strategy,
            "metrics": dict(self.metrics),
            "journal_ids": list(self.journal_ids),
            "assumptions": list(self.assumptions),
            "schema_version": self.schema_version,
        }


@dataclass(frozen=True, slots=True)
class QualityViolation:
    """A numeric token in a narrative that the snapshot does not contain."""

    number: str
    context: str


@dataclass(frozen=True, slots=True)
class QualityReport:
    """Outcome of the numeric quality gate."""

    ok: bool
    violations: tuple[QualityViolation, ...]


@dataclass(frozen=True, slots=True)
class Report:
    """A fact snapshot, a gated narrative, and its trust envelope."""

    snapshot: FactSnapshot
    narrative: str
    untrusted: TrustEnvelope
    quality: QualityReport
    title: str = "Backtest report"

    def as_markdown(self) -> str:
        """Render the report: heading, facts table, then the untrusted narrative."""
        lines = [
            f"# {self.title}",
            "",
            f"- run_id: {self.snapshot.run_id}",
            f"- strategy: {self.snapshot.strategy}",
            f"- schema_version: {self.snapshot.schema_version}",
            "",
            "## Facts",
            "",
            "| metric | value |",
            "|---|---|",
        ]
        for name in sorted(self.snapshot.metrics):
            lines.append(f"| {name} | {self.snapshot.metrics[name]} |")
        lines += ["", "## Narrative", "", render_for_llm(self.untrusted)]
        return "\n".join(lines)


def _lookup(source: object, *names: str, default: object = _MISSING) -> Any:
    for name in names:
        if hasattr(source, name):
            return getattr(source, name)
    if isinstance(source, Mapping):
        for name in names:
            if name in source:
                return source[name]
    if default is _MISSING:
        raise AttributeError(f"source has none of {names!r}")
    return default


def _as_str(value: object) -> str:
    return "" if value is None else str(value)


def build_snapshot(
    source: object,
    *,
    run_id: str | None = None,
    strategy: str | None = None,
    schema_version: int = 1,
) -> FactSnapshot:
    """Build a :class:`FactSnapshot` from a ``BacktestResult``, mapping or attr-object.

    Raises ``TypeError`` when no metrics mapping can be found on ``source``.
    """
    raw_metrics = _lookup(source, "metrics", default=None)
    if raw_metrics is None:
        raise TypeError("cannot build a FactSnapshot: source has no 'metrics'")
    if not isinstance(raw_metrics, Mapping):
        raise TypeError(f"metrics must be a mapping, got {type(raw_metrics).__name__}")
    try:
        metrics = {str(name): float(value) for name, value in raw_metrics.items()}
    except (TypeError, ValueError) as exc:
        raise TypeError("metrics values must be numbers") from exc

    resolved_strategy = (
        str(strategy)
        if strategy is not None
        else _as_str(_lookup(source, "strategy_name", "strategy", default=""))
    )
    resolved_run_id = (
        run_id if run_id is not None else _as_str(_lookup(source, "run_id", default=""))
    )
    journal_ids = tuple(
        _as_str(item) for item in (_lookup(source, "journal_ids", default=()) or ())
    )
    assumptions = tuple(
        _as_str(item) for item in (_lookup(source, "assumptions", default=()) or ())
    )

    return FactSnapshot(
        run_id=resolved_run_id,
        strategy=resolved_strategy,
        metrics=metrics,
        journal_ids=journal_ids,
        assumptions=assumptions,
        schema_version=schema_version,
    )


def quality_gate(
    narrative: str,
    snapshot: FactSnapshot,
    *,
    extra_allowed: Iterable[str | float] = (),
) -> QualityReport:
    """Reject any numeric token in ``narrative`` not present in ``snapshot``."""
    allowed_strings: set[str] = set(snapshot.allowed_numbers())
    for entry in extra_allowed:
        if isinstance(entry, str):
            for token_value in _iter_token_values(entry):
                allowed_strings |= _number_forms(float(token_value))
        else:
            allowed_strings |= _number_forms(float(entry))

    comma_free = {form.replace(",", "") for form in allowed_strings}
    allowed_values = _allowed_decimals(allowed_strings)

    violations: list[QualityViolation] = []
    for match in _TOKEN_RE.finditer(narrative):
        parsed = _normalise_token(match.group())
        if parsed is None:
            continue
        plain, key, value = parsed
        if plain in comma_free or key in comma_free:
            continue
        if (
            value in allowed_values
            or value * 100 in allowed_values
            or value / 100 in allowed_values
        ):
            continue
        start = max(0, match.start() - 10)
        end = min(len(narrative), match.end() + 10)
        violations.append(QualityViolation(number=plain, context=narrative[start:end].strip()))

    return QualityReport(ok=not violations, violations=tuple(violations))


def build_report(
    snapshot: FactSnapshot,
    narrator: Callable[[FactSnapshot], str],
    *,
    title: str = "Backtest report",
    extra_allowed: Iterable[str | float] = (),
    origin: str = "llm:narrative",
) -> Report:
    """Run ``narrator`` over the snapshot, wrap it untrusted, and gate its numbers."""
    narrative = narrator(snapshot)
    wrapped = wrap_untrusted(narrative, origin=origin)
    quality = quality_gate(narrative, snapshot, extra_allowed=extra_allowed)
    return Report(
        snapshot=snapshot,
        narrative=narrative,
        untrusted=wrapped,
        quality=quality,
        title=title,
    )
