"""Unit tests for the token-bucket rate limiter (E1-S5).

Acceptance: ``bucket_refill`` (refill at the expected rate over a fake clock) and
``limits_hold_across_workers`` (two independent limiter instances sharing one store never
exceed the global capacity). No wall clock: the clock is injected.
"""

from __future__ import annotations

import pytest
from honba.adapters.rate_limit import (
    SECOND_NS,
    InMemoryBucket,
    RateLimiter,
    RateLimiterRegistry,
)


def test_bucket_refill() -> None:
    now = [0]
    limiter = RateLimiter(capacity=3.0, refill_rate=1.0, key="dhan:orders", clock=lambda: now[0])

    assert limiter.available_tokens() == pytest.approx(3.0)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False
    assert limiter.available_tokens() == pytest.approx(0.0)

    now[0] = 1 * SECOND_NS
    assert limiter.available_tokens() == pytest.approx(1.0)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False

    now[0] = 3 * SECOND_NS
    assert limiter.available_tokens() == pytest.approx(2.0)
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is True
    assert limiter.try_acquire() is False


def test_refill_is_capped_at_capacity() -> None:
    now = [0]
    limiter = RateLimiter(capacity=2.0, refill_rate=5.0, clock=lambda: now[0])
    assert limiter.try_acquire() is True
    now[0] = 100 * SECOND_NS
    assert limiter.available_tokens() == pytest.approx(2.0)


def test_limits_hold_across_instances_sharing_a_store() -> None:
    store = InMemoryBucket()
    now = [0]
    first = RateLimiter(
        capacity=2.0, refill_rate=0.0, key="shared", store=store, clock=lambda: now[0]
    )
    second = RateLimiter(
        capacity=2.0, refill_rate=0.0, key="shared", store=store, clock=lambda: now[0]
    )

    assert first.try_acquire() is True
    assert second.try_acquire() is True
    assert first.try_acquire() is False
    assert second.try_acquire() is False


def test_registry_separates_adapter_and_endpoint_buckets() -> None:
    registry = RateLimiterRegistry(store=InMemoryBucket(), clock=lambda: 0)
    orders = registry.get("dhan", "orders", capacity=1.0, refill_rate=0.0)
    quotes = registry.get("dhan", "quotes", capacity=1.0, refill_rate=0.0)

    assert registry.get("dhan", "orders", capacity=1.0, refill_rate=0.0) is orders
    assert orders.key == "dhan:orders"
    assert quotes.key == "dhan:quotes"
    assert orders.try_acquire() is True
    assert orders.try_acquire() is False
    assert quotes.try_acquire() is True


async def test_acquire_returns_immediately_when_a_token_is_available() -> None:
    limiter = RateLimiter(capacity=1.0, refill_rate=1.0, clock=lambda: 0)
    await limiter.acquire()
    assert limiter.available_tokens() == pytest.approx(0.0)
    assert limiter.try_acquire() is False


def test_rejects_invalid_parameters() -> None:
    with pytest.raises(ValueError):
        RateLimiter(capacity=0.0, refill_rate=1.0)
    with pytest.raises(ValueError):
        RateLimiter(capacity=1.0, refill_rate=-1.0)
