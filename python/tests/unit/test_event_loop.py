"""``honba.event_loop``: the explicit owner of the interpreter's one async runtime (ADR 0015)."""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator

import pytest

pytest.importorskip("honba._honba")

from honba import event_loop
from honba._native import native_attr


@pytest.fixture(autouse=True)
def _stopped() -> Iterator[None]:
    event_loop.stop()
    yield
    event_loop.stop()


def _os_threads() -> int:
    """Native thread count of this process (tokio workers are invisible to ``threading``)."""
    if not sys.platform.startswith("linux"):
        pytest.skip("native thread count needs /proc")
    return len(os.listdir("/proc/self/task"))


def test_not_running_by_default() -> None:
    assert event_loop.is_running() is False
    assert event_loop.info() is None


def test_start_reports_handle_info() -> None:
    info = event_loop.start(worker_threads=2)
    assert event_loop.is_running() is True
    assert info.flavor == "multi-thread"
    assert info.worker_threads == 2
    assert info.generation >= 1
    assert event_loop.info() == info


def test_start_is_idempotent() -> None:
    first = event_loop.start(worker_threads=2)
    assert event_loop.start() == first
    assert event_loop.start(worker_threads=2) == first


def test_start_with_a_different_worker_count_while_running_raises() -> None:
    event_loop.start(worker_threads=2)
    with pytest.raises(event_loop.EventLoopError, match="worker_threads"):
        event_loop.start(worker_threads=3)


def test_zero_worker_threads_is_a_value_error() -> None:
    with pytest.raises(ValueError, match="worker_threads"):
        event_loop.start(worker_threads=0)
    assert event_loop.is_running() is False


def test_stop_is_idempotent_and_reports_whether_it_stopped() -> None:
    assert event_loop.stop() is False
    event_loop.start()
    assert event_loop.stop() is True
    assert event_loop.stop() is False
    assert event_loop.is_running() is False
    assert event_loop.info() is None


def test_restart_after_stop_gets_a_new_generation() -> None:
    first = event_loop.start()
    event_loop.stop()
    second = event_loop.start()
    assert second.generation == first.generation + 1


def test_a_runtime_started_behind_its_back_makes_start_raise() -> None:
    native_attr("runtime_start")(1)
    try:
        with pytest.raises(event_loop.EventLoopError, match="already running"):
            event_loop.start()
        assert event_loop.stop() is False  # not owned: stop leaves it alone
        assert native_attr("runtime_info")() is not None
    finally:
        native_attr("runtime_stop")()


def test_concurrent_starts_yield_one_runtime() -> None:
    results: list[object] = []
    barrier = threading.Barrier(8)

    def go() -> None:
        barrier.wait()
        results.append(event_loop.start(worker_threads=2))

    threads = [threading.Thread(target=go) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(results) == 8
    assert len({r.generation for r in results}) == 1  # type: ignore[attr-defined]


def test_start_stop_cycles_leak_no_threads() -> None:
    event_loop.start(worker_threads=3)
    event_loop.stop()
    py_before = threading.enumerate()
    os_before = _os_threads()
    for _ in range(3):
        event_loop.start(worker_threads=3)
        assert _os_threads() > os_before
        event_loop.stop()
    assert _os_threads() == os_before
    assert threading.enumerate() == py_before


def test_running_context_manager_starts_and_stops() -> None:
    with event_loop.running(worker_threads=2) as info:
        assert event_loop.is_running() is True
        assert info == event_loop.info()
    assert event_loop.is_running() is False


def test_running_inside_a_started_loop_leaves_it_running() -> None:
    event_loop.start()
    with event_loop.running():
        pass
    assert event_loop.is_running() is True


def test_running_stops_on_exception() -> None:
    with pytest.raises(KeyError), event_loop.running():
        raise KeyError("boom")
    assert event_loop.is_running() is False


def test_legacy_runtime_helpers_follow_the_runtime() -> None:
    get_handle = native_attr("get_runtime_handle")
    with pytest.raises(RuntimeError, match="not running"):
        get_handle()
    with event_loop.running():
        assert get_handle() == "tokio-multi-thread"
