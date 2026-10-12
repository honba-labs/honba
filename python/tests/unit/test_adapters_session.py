"""Unit tests for broker token sessions over a fake clock (E1-S5).

Acceptance: ``token_expiry_fake_clock``. No wall clock, no network: every test drives an
injected ``now_ns`` and asserts refresh happens only when it must, and that concurrent
``ensure_valid`` calls coalesce into one refresh.
"""

from __future__ import annotations

import asyncio

from honba.adapters.session import SECOND_NS, Token, TokenSession


def _session(now: list[int], refresh_count: list[int], *, expiry_s: int = 1000):
    async def refresh() -> Token:
        refresh_count.append(now[0])
        return Token("token-refreshed", expires_at=now[0] + 3600 * SECOND_NS)

    return TokenSession(
        Token("token-initial", expires_at=expiry_s * SECOND_NS),
        refresh_fn=refresh,
        clock=lambda: now[0],
    )


def test_token_expiry_fake_clock() -> None:
    now = [0]
    refreshes: list[int] = []
    session = _session(now, refreshes)

    assert session.expires_at == 1000 * SECOND_NS
    assert session.is_expired() is False
    assert session.should_refresh() is False

    now[0] = (1000 - 60) * SECOND_NS
    assert session.should_refresh() is True
    assert session.is_expired() is False

    now[0] = 1000 * SECOND_NS
    assert session.is_expired() is True


async def test_ensure_valid_refreshes_only_when_needed() -> None:
    now = [0]
    refreshes: list[int] = []
    session = _session(now, refreshes)

    await session.ensure_valid()
    await session.ensure_valid()
    assert refreshes == []

    now[0] = (1000 - 60) * SECOND_NS
    awaited = await session.ensure_valid()
    assert len(refreshes) == 1
    assert awaited.value == "token-refreshed"
    assert session.is_expired() is False

    now[0] += 10 * SECOND_NS
    await session.ensure_valid()
    assert len(refreshes) == 1


async def test_ensure_valid_refreshes_after_expiry() -> None:
    now = [0]
    refreshes: list[int] = []
    session = _session(now, refreshes)

    now[0] = 2000 * SECOND_NS
    assert session.is_expired() is True
    token = await session.ensure_valid()
    assert token.value == "token-refreshed"
    assert len(refreshes) == 1
    assert session.is_expired() is False


async def test_concurrent_ensure_valid_coalesces_into_one_refresh() -> None:
    now = [0]
    refreshes: list[int] = []
    gate = asyncio.Event()

    async def refresh() -> Token:
        refreshes.append(now[0])
        await gate.wait()
        return Token("token-refreshed", expires_at=now[0] + 3600 * SECOND_NS)

    session = TokenSession(
        Token("token-initial", expires_at=1000 * SECOND_NS),
        refresh_fn=refresh,
        clock=lambda: now[0],
    )
    now[0] = 1000 * SECOND_NS

    tasks = [asyncio.create_task(session.ensure_valid()) for _ in range(5)]
    await asyncio.sleep(0)
    gate.set()
    await asyncio.gather(*tasks)

    assert len(refreshes) == 1


async def test_invalidate_forces_a_refresh() -> None:
    now = [0]
    refreshes: list[int] = []
    session = _session(now, refreshes)

    await session.ensure_valid()
    assert refreshes == []

    session.invalidate()
    assert session.is_expired() is True
    await session.ensure_valid()
    assert len(refreshes) == 1


def test_repr_and_str_redact_the_token() -> None:
    now = [0]
    session = _session(now, [])
    assert "token-initial" not in repr(session)
    assert "token-initial" not in str(session)
    token = Token("token-initial", expires_at=1)
    assert "token-initial" not in repr(token)
