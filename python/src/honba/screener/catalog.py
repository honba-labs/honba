"""The metric catalog and natural-language metric resolution.

``MetricCatalog`` is pure: it is built from ``MetricDefinition`` values and does no
I/O. ``load_catalog`` is the thin file loader for ``schema/catalog/metric_catalog.json``.

Phrases are matched after ``normalize_phrase`` (lowercase, ``_`` and ``-`` as spaces,
whitespace collapsed) against the union of wire keys, UI ids (``uiId``) and aliases.
Resolution always returns the ``MetricDefinition``; callers put its ``.key`` (the wire
key) on the wire, never the UI id.
"""

from __future__ import annotations

import difflib
import json
import os
import re
from collections.abc import Iterable, Iterator
from pathlib import Path

from honba.entities.screener import MetricDefinition

CATALOG_ENV_VAR = "HONBA_METRIC_CATALOG"
"""Environment variable that overrides the catalog file location."""

_SEPARATORS = re.compile(r"[_\-\s]+")
_MAX_SUGGESTIONS = 3


def normalize_phrase(phrase: str) -> str:
    """Lowercase, treat ``_`` and ``-`` as spaces, collapse whitespace and strip."""
    return _SEPARATORS.sub(" ", phrase.lower()).strip()


class CatalogError(ValueError):
    """The catalog definitions are inconsistent (duplicates or alias clashes)."""


class MetricResolutionError(LookupError):
    """A phrase could not be resolved to exactly one metric."""


class UnknownMetric(MetricResolutionError):
    """No metric matches the phrase."""

    def __init__(self, phrase: str, suggestions: tuple[str, ...]) -> None:
        self.phrase = phrase
        self.suggestions = suggestions
        hint = f"; did you mean {', '.join(suggestions)}?" if suggestions else ""
        super().__init__(f"unknown metric {phrase!r}{hint}")


class AmbiguousMetric(MetricResolutionError):
    """Several different metrics match the phrase."""

    def __init__(self, phrase: str, candidates: tuple[str, ...]) -> None:
        self.phrase = phrase
        self.candidates = candidates
        super().__init__(f"ambiguous metric {phrase!r}: matches wire keys {', '.join(candidates)}")


class MetricCatalog:
    """An immutable, validated set of metric definitions with phrase lookup.

    Raises ``CatalogError`` when a wire key or UI id is duplicated, when two metrics
    share an alias, when an alias equals another metric's wire key or UI id (all after
    normalisation for aliases), or when ``groups`` is given and a metric's group is
    not in it.
    """

    def __init__(
        self,
        metrics: Iterable[MetricDefinition],
        groups: Iterable[str] | None = None,
    ) -> None:
        self._metrics: tuple[MetricDefinition, ...] = tuple(metrics)
        self.groups: tuple[str, ...] | None = tuple(groups) if groups is not None else None
        self._by_key: dict[str, MetricDefinition] = {}
        self._phrases: dict[str, list[str]] = {}
        self._build()

    def _build(self) -> None:
        ui_ids: dict[str, str] = {}
        for m in self._metrics:
            if m.key in self._by_key:
                raise CatalogError(f"duplicate metric key {m.key!r}")
            self._by_key[m.key] = m
            if m.ui_id is not None:
                if m.ui_id in ui_ids:
                    raise CatalogError(
                        f"duplicate uiId {m.ui_id!r} (metrics {ui_ids[m.ui_id]!r} and {m.key!r})"
                    )
                ui_ids[m.ui_id] = m.key
            if self.groups is not None and m.group not in self.groups:
                raise CatalogError(f"metric {m.key!r} has unknown group {m.group!r}")

        # Normalised wire keys and UI ids, to detect aliases that shadow another metric.
        names: dict[str, set[str]] = {}
        for m in self._metrics:
            for name in (m.key, m.ui_id):
                if name is not None:
                    names.setdefault(normalize_phrase(name), set()).add(m.key)

        alias_owner: dict[str, str] = {}
        for m in self._metrics:
            for alias in m.aliases:
                norm = normalize_phrase(alias)
                owner = alias_owner.get(norm)
                if owner is not None and owner != m.key:
                    raise CatalogError(f"alias {norm!r} is used by both {owner!r} and {m.key!r}")
                alias_owner[norm] = m.key
                clash = sorted(names.get(norm, set()) - {m.key})
                if clash:
                    raise CatalogError(
                        f"alias {alias!r} of metric {m.key!r} equals the wire key or uiId "
                        f"of metric {clash[0]!r}"
                    )

        for m in self._metrics:
            phrases = [m.key, *([m.ui_id] if m.ui_id is not None else []), *m.aliases]
            for phrase in phrases:
                owners = self._phrases.setdefault(normalize_phrase(phrase), [])
                if m.key not in owners:
                    owners.append(m.key)

    def __len__(self) -> int:
        return len(self._metrics)

    def __iter__(self) -> Iterator[MetricDefinition]:
        return iter(self._metrics)

    def __contains__(self, key: object) -> bool:
        return key in self._by_key

    def get(self, key: str) -> MetricDefinition | None:
        """The metric with exactly this wire key, or ``None``."""
        return self._by_key.get(key)

    def resolve(self, phrase: str) -> MetricDefinition:
        """Resolve a wire key, UI id or alias (any case/spacing) to one metric.

        Raises ``UnknownMetric`` (with up to three close-match wire keys) when nothing
        matches and ``AmbiguousMetric`` (listing every candidate wire key) when
        several different metrics match.
        """
        norm = normalize_phrase(phrase)
        owners = self._phrases.get(norm)
        if not owners:
            raise UnknownMetric(phrase, self._suggest(norm))
        if len(owners) > 1:
            raise AmbiguousMetric(phrase, tuple(owners))
        return self._by_key[owners[0]]

    def _suggest(self, norm: str) -> tuple[str, ...]:
        close = difflib.get_close_matches(norm, list(self._phrases), n=10, cutoff=0.6)
        keys: list[str] = []
        for phrase in close:
            for key in self._phrases[phrase]:
                if key not in keys:
                    keys.append(key)
        return tuple(keys[:_MAX_SUGGESTIONS])


def default_catalog_path() -> Path:
    """Where ``load_catalog`` looks when no path is given.

    ``$HONBA_METRIC_CATALOG`` if set, else ``schema/catalog/metric_catalog.json`` in the
    honba checkout that contains this module (``python/honba/screener/`` is three
    levels below the repo root). This works for a source checkout and an editable
    install; a wheel install must pass a path or set the environment variable.
    """
    override = os.environ.get(CATALOG_ENV_VAR)
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "schema" / "catalog" / "metric_catalog.json"


def load_catalog(path: str | os.PathLike[str] | None = None) -> MetricCatalog:
    """Read and validate a metric catalog file (``default_catalog_path()`` if no path)."""
    file = Path(path) if path is not None else default_catalog_path()
    if not file.is_file():
        raise FileNotFoundError(
            f"metric catalog not found at {file}; pass a path or set ${CATALOG_ENV_VAR}"
        )
    doc = json.loads(file.read_text(encoding="utf-8"))
    metrics = [MetricDefinition.model_validate(entry) for entry in doc["metrics"]]
    return MetricCatalog(metrics, groups=doc.get("groups"))
