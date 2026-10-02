"""The Honba Indicator contract.

Every indicator is a streaming object: feed it one bar's worth of inputs with
``update(*inputs)`` (or a whole :class:`~honba.entities.bar.Bar` with ``update_bar``);
it returns ``None`` until warmed up. Indicators declare, as class metadata, their
``kind`` (config name), ``family``, ``inputs`` and ``outputs``, and expose a JSON
``spec()`` so configs, the CLI and AI agents (MCP) can discover and parameterise them.
"""

from __future__ import annotations

import inspect
from typing import Any, Callable, ClassVar

FAMILIES = (
    "moving_average",
    "trend",
    "momentum",
    "volatility",
    "volume",
    "support_resistance",
    "breadth",
    "statistical",
)

_REGISTRY: dict[str, type[Indicator]] = {}
_UNSET: Any = object()


class Indicator:
    kind: ClassVar[str]
    family: ClassVar[str]
    inputs: ClassVar[tuple[str, ...]] = ("close",)
    outputs: ClassVar[tuple[str, ...]] = ("value",)

    def __new__(cls, *args, **kwargs):
        self = super().__new__(cls)
        bound = inspect.signature(cls.__init__).bind(self, *args, **kwargs)
        bound.apply_defaults()
        self._init_args = {k: v for k, v in list(bound.arguments.items())[1:]}
        return self

    @property
    def warmup(self) -> int | None:
        """Number of updates before the first value, or ``None`` if data-dependent."""
        raise NotImplementedError(f"{type(self).__name__} must define warmup")

    def update(self, *values: float) -> Any:
        raise NotImplementedError

    def update_bar(self, bar) -> Any:
        """Feeds the fields named in ``inputs`` from a Bar (``ts`` is unix ns)."""
        return self.update(*(getattr(bar, f) for f in self.inputs))

    def reset(self) -> None:
        """Returns to the freshly constructed state."""
        self.__init__(**self._init_args)


def indicator(
    kind: str,
    family: str,
    inputs: tuple[str, ...] = ("close",),
    outputs: tuple[str, ...] = ("value",),
    warmup: Callable[[Any], int | None] | None = _UNSET,
):
    """Class decorator: declares metadata, defines ``warmup`` and registers the indicator.

    ``warmup=None`` (explicit) marks a data-dependent warm-up, e.g. session-anchored
    indicators; omitting it is an error unless the class defines ``warmup`` itself.
    """
    if family not in FAMILIES:
        raise ValueError(f"unknown family {family!r}")

    def wrap(cls):
        if kind in _REGISTRY:
            raise ValueError(f"indicator {kind!r} registered twice")
        cls.kind, cls.family, cls.inputs, cls.outputs = kind, family, tuple(inputs), tuple(outputs)
        if warmup is None:
            cls.warmup = property(lambda self: None)
        elif warmup is not _UNSET:
            cls.warmup = property(warmup)
        _REGISTRY[kind] = cls
        return cls

    return wrap


def get(kind: str) -> type[Indicator]:
    try:
        return _REGISTRY[kind]
    except KeyError:
        raise ValueError(f"unknown indicator {kind!r}; choose from {sorted(_REGISTRY)}") from None


def spec(kind: str) -> dict:
    """JSON-serialisable description: kind, family, inputs, outputs, params (name/default/type), summary."""
    cls = get(kind)
    params = []
    for name, p in list(inspect.signature(cls.__init__).parameters.items())[1:]:
        entry: dict[str, Any] = {"name": name}
        if p.default is not inspect.Parameter.empty:
            entry["default"] = p.default
        if p.annotation is not inspect.Parameter.empty:
            entry["type"] = (
                p.annotation
                if isinstance(p.annotation, str)
                else getattr(p.annotation, "__name__", str(p.annotation))
            )
        params.append(entry)
    doc = (inspect.getdoc(cls) or "").strip().splitlines()
    return {
        "kind": kind,
        "family": cls.family,
        "inputs": list(cls.inputs),
        "outputs": list(cls.outputs),
        "params": params,
        "summary": doc[0] if doc else "",
    }
