"""CLI entry point for honba.log: list all known event types.

Usage::

    python -m honba.log
"""

from __future__ import annotations

from honba.log import _WIRE_EVENT_DESCRIPTIONS, STRATEGY_EVENT_TYPES


def main() -> None:
    wire = sorted(_WIRE_EVENT_DESCRIPTIONS.items())
    strategy = sorted(STRATEGY_EVENT_TYPES.items())

    print("Honba event types  ·  use with HONBA_LOG_EVENTS or EventFilter")
    print("=" * 60)
    print("\n── Wire layer  (honba-messages canonical types) ─────────────")
    for name, desc in wire:
        print(f"  {name:<22}  {desc}")

    print("\n── Strategy layer  (EVENT_* decision events) ────────────────")
    for name, desc in strategy:
        print(f"  {name:<22}  {desc}")

    print("\n── Prefix matching ───────────────────────────────────────────")
    print("  A prefix matches all events that start with it:")
    print("    EVENT_MEMBERSHIP  →  EVENT_MEMBERSHIP_ADD, EVENT_MEMBERSHIP_DEL")
    print("    order             →  order, order_filled, order_rejected, …")

    print("\n── Usage ─────────────────────────────────────────────────────")
    print('  HONBA_LOG_EVENTS="order_filled,EVENT_MEMBERSHIP" python bt.py')
    print('  HONBA_LOG_EVENTS="" python bt.py              # log everything')
    print()


if __name__ == "__main__":
    main()
