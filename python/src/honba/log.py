"""Event-based logging and filtering for Honba strategies and runners.

Event types come from two layers:

1. **Wire layer** (``honba-messages`` canonical types):
   ``bar``, ``quote``, ``trade``, ``order``, ``order_accepted``,
   ``order_filled``, ``order_rejected``, ``order_cancelled``

2. **Strategy layer** (decision events, ``EVENT_*`` prefix convention):
   ``EVENT_BUY``, ``EVENT_SELL``, ``EVENT_MEMBERSHIP_ADD``,
   ``EVENT_MEMBERSHIP_DEL``, and any custom event a strategy emits via
   :func:`register_event`.

Both naming conventions are understood by :class:`EventFilter`.

Public API
----------
- :class:`EventFilter`: logging.Filter for selective event admission
- :func:`setup_event_logging`: one-liner configuration helper
- :func:`register_event`: register a custom event type (adds to catalogue)
- ``WIRE_EVENT_TYPES``: frozenset of canonical wire-layer names
- ``STRATEGY_EVENT_TYPES``: dict of built-in strategy event names → descriptions
- ``KNOWN_EVENTS``: full catalogue (wire + strategy) — use ``python -m honba.log``

Environment
-----------
``HONBA_LOG_EVENTS`` (comma-separated) sets allowed events without code changes::

    HONBA_LOG_EVENTS="order_filled,EVENT_MEMBERSHIP" python my_backtest.py

Discover available events at any time::

    python -m honba.log
"""

from __future__ import annotations

import logging
import os
import re
import sys
from typing import Any, Iterable

DEFAULT_LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"

# Matches strategy-layer EVENT_* prefix
_STRATEGY_EVENT_PATTERN = re.compile(r"^(EVENT_[A-Z0-9_]+)")

# ---------------------------------------------------------------------------
# Wire layer — mirrors honba-messages::Event (ADR 006)
# ---------------------------------------------------------------------------
WIRE_EVENT_TYPES: frozenset[str] = frozenset({
    "bar",
    "quote",
    "trade",
    "order",
    "order_accepted",
    "order_filled",
    "order_rejected",
    "order_cancelled",
})

_WIRE_EVENT_DESCRIPTIONS: dict[str, str] = {
    "bar":              "Aggregated OHLCV bar (market data)",
    "quote":            "Top-of-book quote tick (market data)",
    "trade":            "Market trade print / last-sale tick (market data)",
    "order":            "Intent submitted to execution port (DEBUG)",
    "order_accepted":   "Exchange acknowledged the order",
    "order_filled":     "Fill received from execution port (qty, px, cost)",
    "order_rejected":   "Intent failed validation or was rejected by exchange (WARNING)",
    "order_cancelled":  "Order cancelled by exchange or strategy",
}

# ---------------------------------------------------------------------------
# Strategy layer — EVENT_* convention
# ---------------------------------------------------------------------------
# Built-in events emitted by honba.strategies.base.Strategy
_BUILTIN_STRATEGY_EVENTS: dict[str, str] = {
    "EVENT_BUY":              "Strategy issued a buy order (auto-emitted by Strategy.buy())",
    "EVENT_SELL":             "Strategy issued a sell order (auto-emitted by Strategy.sell())",
    "EVENT_MEMBERSHIP_ADD":   "Symbols added to a dynamic universe (e.g. index rebalance)",
    "EVENT_MEMBERSHIP_DEL":   "Symbols removed from a dynamic universe",
}

# Mutable registry — strategies add entries via register_event()
STRATEGY_EVENT_TYPES: dict[str, str] = dict(_BUILTIN_STRATEGY_EVENTS)


def register_event(name: str, description: str = "") -> None:
    """Register a custom strategy event type so it appears in ``KNOWN_EVENTS``.

    Call once at module level in your strategy file::

        from honba.log import register_event
        register_event("EVENT_SIGNAL_CROSS", "EMA crossover signal triggered")

    Parameters
    ----------
    name : str
        Event name — must start with ``EVENT_`` (convention enforced).
    description : str
        Human-readable description shown by ``python -m honba.log``.
    """
    if not name.startswith("EVENT_"):
        raise ValueError(f"Custom event names must start with 'EVENT_', got: {name!r}")
    STRATEGY_EVENT_TYPES[name] = description


@property  # type: ignore[misc]
def KNOWN_EVENTS() -> dict[str, str]:  # noqa: N802
    """Full event catalogue (wire + strategy). Read-only view."""
    return {**_WIRE_EVENT_DESCRIPTIONS, **STRATEGY_EVENT_TYPES}


# Make KNOWN_EVENTS usable as a plain dict at module level
class _KnownEventsProxy:
    """Lazy dict-like proxy merging wire + strategy event catalogues."""

    def __repr__(self) -> str:
        return repr(dict(self))

    def items(self):
        return {**_WIRE_EVENT_DESCRIPTIONS, **STRATEGY_EVENT_TYPES}.items()

    def keys(self):
        return {**_WIRE_EVENT_DESCRIPTIONS, **STRATEGY_EVENT_TYPES}.keys()

    def values(self):
        return {**_WIRE_EVENT_DESCRIPTIONS, **STRATEGY_EVENT_TYPES}.values()

    def __iter__(self):
        return iter({**_WIRE_EVENT_DESCRIPTIONS, **STRATEGY_EVENT_TYPES})

    def __getitem__(self, key: str) -> str:
        return {**_WIRE_EVENT_DESCRIPTIONS, **STRATEGY_EVENT_TYPES}[key]

    def __contains__(self, key: object) -> bool:
        return key in _WIRE_EVENT_DESCRIPTIONS or key in STRATEGY_EVENT_TYPES


KNOWN_EVENTS: _KnownEventsProxy = _KnownEventsProxy()


class EventFilter(logging.Filter):
    """Selectively admit log records matching allowed event names or prefixes.

    Understands both naming conventions:

    * **Wire types** (``honba-messages``): ``order_filled``, ``order_rejected``,
      ``bar``, ``trade``, ``quote``, ``order``, ``order_accepted``, ``order_cancelled``
    * **Strategy events** (``EVENT_*`` prefix): ``EVENT_BUY``, ``EVENT_SELL``,
      ``EVENT_MEMBERSHIP_ADD``, ``EVENT_MEMBERSHIP_DEL``, etc.

    Resolution order for each log record:

    1. ``record.event_type`` extra field (set by runners and ``log_event``)
    2. Parsed from the start of the log message string
    3. Records with no detectable event type are excluded when a filter is active

    ``WARNING`` and above are always admitted regardless of filter.

    Parameters
    ----------
    allowed_events : Iterable[str] | None
        Allowed event names/prefixes to admit. ``None`` reads from
        ``HONBA_LOG_EVENTS`` env var (comma-separated). If that is also unset,
        all events are admitted.

    Examples
    --------
    Filter for fills and membership changes only:

    >>> EventFilter(["order_filled", "EVENT_MEMBERSHIP"])

    Filter via environment variable::

        HONBA_LOG_EVENTS="order_filled,order_rejected,EVENT_MEMBERSHIP" python bt.py
    """

    def __init__(self, allowed_events: Iterable[str] | None = None) -> None:
        super().__init__()
        if allowed_events is None:
            env_val = os.environ.get("HONBA_LOG_EVENTS", "").strip()
            if env_val:
                allowed_events = [e.strip() for e in env_val.split(",") if e.strip()]

        self.allowed_events: frozenset[str] | None = (
            frozenset(allowed_events) if allowed_events is not None else None
        )

    def filter(self, record: logging.LogRecord) -> bool:
        # Always pass warnings, errors, criticals
        if record.levelno >= logging.WARNING:
            return True

        # No filter active → pass everything
        if self.allowed_events is None:
            return True

        # 1. Explicit extra field (set by StrategyRunner and Strategy.log_event)
        ev_type = getattr(record, "event_type", None)
        if ev_type:
            return self._matches(str(ev_type))

        # 2. Parse from message start
        msg = record.getMessage().strip()
        # Strategy EVENT_* pattern
        m = _STRATEGY_EVENT_PATTERN.match(msg)
        if m:
            return self._matches(m.group(1))
        # Wire type pattern (lowercase word, possibly with underscores)
        first_token = msg.split(":")[0].strip().lower()
        if first_token in WIRE_EVENT_TYPES:
            return self._matches(first_token)

        # Not an event record; exclude when a filter is active
        return False

    def _matches(self, event_name: str) -> bool:
        if self.allowed_events is None:
            return True
        for allowed in self.allowed_events:
            if event_name == allowed or event_name.startswith(allowed):
                return True
        return False


def setup_event_logging(
    allowed_events: Iterable[str] | None = None,
    level: int | str = logging.INFO,
    stream: Any = sys.stdout,
    fmt: str = DEFAULT_LOG_FORMAT,
    logger_name: str | None = None,
) -> logging.Handler:
    """Configure structured event logging across Honba.

    Call once at the top of a backtest script or entry point.  When no
    arguments are given the function checks ``HONBA_LOG_EVENTS`` in the
    environment and configures the root logger accordingly.

    Parameters
    ----------
    allowed_events : Iterable[str] | None
        Allowed event names or prefixes, e.g.
        ``["order_filled", "EVENT_MEMBERSHIP"]``.
        If *None* and ``HONBA_LOG_EVENTS`` is set in the environment, the
        env var is used.  If both are absent, all events are emitted.
    level : int | str
        Logging threshold (default: ``logging.INFO``).
    stream : Any
        Output stream (default: ``sys.stdout``).
    fmt : str
        ``logging.Formatter`` format string.
    logger_name : str | None
        Logger to configure.  ``None`` (the default) configures the root
        logger, capturing all honba sub-loggers automatically.

    Returns
    -------
    logging.Handler
        The configured and attached handler.

    Examples
    --------
    Log everything::

        from honba import setup_event_logging
        setup_event_logging()

    Log fills and membership changes only::

        setup_event_logging(["order_filled", "EVENT_MEMBERSHIP"])

    Via env variable::

        HONBA_LOG_EVENTS="order_filled,EVENT_BUY,EVENT_SELL" python bt.py
    """
    if isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    target_logger = logging.getLogger(logger_name)
    target_logger.setLevel(level)

    handler = logging.StreamHandler(stream)
    handler.setLevel(level)
    handler.setFormatter(logging.Formatter(fmt))

    if allowed_events is not None or "HONBA_LOG_EVENTS" in os.environ:
        handler.addFilter(EventFilter(allowed_events))

    # Remove existing StreamHandlers to prevent duplicate output
    for h in list(target_logger.handlers):
        if isinstance(h, logging.StreamHandler):
            target_logger.removeHandler(h)

    target_logger.addHandler(handler)
    return handler


# ---------------------------------------------------------------------------
# Auto-initialization: if HONBA_LOG_EVENTS is set in the environment when
# this module is first imported, configure logging immediately.
# Scripts and examples don't need to call setup_event_logging() explicitly.
# ---------------------------------------------------------------------------
if os.environ.get("HONBA_LOG_EVENTS", "").strip():
    setup_event_logging()


