"""Integration test: rate-limit buckets survive independent limiter instances (E1-S5).

Acceptance: ``limits_hold_across_workers``. The shared state lives on disk
(:class:`FileBackedBucket`), so the two limiters below are as independent as two worker
processes would be; they must never exceed the global capacity. No wall clock.
"""

from __future__ import annotations

from honba.adapters.rate_limit import FileBackedBucket, RateLimiter


def test_file_backed_bucket_limits_hold_across_instances(tmp_path) -> None:
    store = FileBackedBucket(tmp_path / "buckets")
    now = [0]
    first = RateLimiter(
        capacity=2.0, refill_rate=0.0, key="dhan:orders", store=store, clock=lambda: now[0]
    )
    second = RateLimiter(
        capacity=2.0, refill_rate=0.0, key="dhan:orders", store=store, clock=lambda: now[0]
    )

    assert first.try_acquire() is True
    assert second.try_acquire() is True
    assert first.try_acquire() is False
    assert second.try_acquire() is False

    now[0] = 1_000_000_000
    refreshed = RateLimiter(
        capacity=2.0, refill_rate=1.0, key="dhan:orders", store=store, clock=lambda: now[0]
    )
    assert refreshed.available_tokens() == 1.0
    assert refreshed.try_acquire() is True
    assert refreshed.try_acquire() is False
