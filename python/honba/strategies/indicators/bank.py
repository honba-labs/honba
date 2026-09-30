"""Config-driven indicator sets.

A strategy's ``config.toml`` declares indicators as tables::

    [indicators.trend]
    kind = "supertrend"
    atr_period = 10
    factor = 3.0

and the strategy builds them with ``IndicatorBank(config.indicators)`` and feeds each
bar with ``bank.update(bar)``. Changing an indicator or its parameters is a config change.
"""
from __future__ import annotations

from typing import Any, Mapping

from honba.strategies.indicators import _base

_BAR_FIELDS = {"open", "high", "low", "close", "volume", "ts"}


class IndicatorBank:
    def __init__(self, specs: Mapping[str, Mapping[str, Any]]) -> None:
        self._inds: dict[str, _base.Indicator] = {}
        self._last: dict[str, Any] = {}
        for name, spec in specs.items():
            if "kind" not in spec:
                raise ValueError(f"indicator {name!r}: spec needs a 'kind'")
            params = {k: v for k, v in spec.items() if k != "kind"}
            ind = _base.get(spec["kind"])(**params)
            extra = [f for f in ind.inputs if f not in _BAR_FIELDS]
            if extra:
                raise ValueError(
                    f"indicator {name!r} ({spec['kind']}) needs non-bar inputs {extra}; "
                    "feed it directly instead of through a bank"
                )
            self._inds[name] = ind
            self._last[name] = None

    @property
    def names(self) -> list[str]:
        return list(self._inds)

    @property
    def ready(self) -> bool:
        """True once every indicator has produced a value."""
        return bool(self._inds) and all(v is not None for v in self._last.values())

    def __getitem__(self, name: str) -> Any:
        return self._last[name]

    def update(self, bar) -> dict[str, Any]:
        """Feeds ``bar`` to every indicator; returns the latest output per name."""
        for name, ind in self._inds.items():
            self._last[name] = ind.update_bar(bar)
        return dict(self._last)

    def reset(self) -> None:
        for name, ind in self._inds.items():
            ind.reset()
            self._last[name] = None
