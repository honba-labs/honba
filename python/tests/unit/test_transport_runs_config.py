"""``InprocTransport`` hands the journals root and run-service config to the native call."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

import honba.client.transport as transport_module
from honba.client import InprocTransport


class FakeNative:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[Any, ...], dict[str, Any]]] = []

    def __call__(self, *args: Any, **kwargs: Any) -> tuple[int, str]:
        self.calls.append((args, kwargs))
        return 200, "{}"


@pytest.fixture
def native(monkeypatch: pytest.MonkeyPatch) -> FakeNative:
    fake = FakeNative()
    monkeypatch.setattr(transport_module, "native_attr", lambda name: fake)
    return fake


def merged(call: tuple[tuple[Any, ...], dict[str, Any]]) -> dict[str, Any]:
    names = ["data_dir", "method", "path", "query_json", "body_json"]
    args, kwargs = call
    return {**dict(zip(names, args, strict=False)), **kwargs}


def test_journals_dir_and_config_reach_the_native_call(tmp_path: Path, native: FakeNative) -> None:
    data, journals = tmp_path / "bars", tmp_path / "journals"
    data.mkdir()
    InprocTransport(data, journals_dir=journals, max_concurrent_runs=2, max_queued_runs=3).request(
        "GET", "/health"
    )
    sent = merged(native.calls[0])
    assert sent["journals_dir"] == str(journals)
    assert (sent["max_concurrent_runs"], sent["max_queued_runs"]) == (2, 3)


def test_without_a_journals_dir_nothing_extra_is_sent(tmp_path: Path, native: FakeNative) -> None:
    InprocTransport(tmp_path).request("GET", "/health")
    sent = merged(native.calls[0])
    assert sent.get("journals_dir") is None


def test_the_journals_dir_is_created_when_missing(tmp_path: Path, native: FakeNative) -> None:
    journals = tmp_path / "deep" / "journals"
    InprocTransport(tmp_path, journals_dir=journals)
    assert journals.is_dir()


@pytest.mark.parametrize("bad", [0, -1])
def test_non_positive_run_limits_are_rejected(tmp_path: Path, bad: int) -> None:
    with pytest.raises(ValueError, match="max_concurrent_runs"):
        InprocTransport(tmp_path, journals_dir=tmp_path / "j", max_concurrent_runs=bad)
    with pytest.raises(ValueError, match="max_queued_runs"):
        InprocTransport(tmp_path, journals_dir=tmp_path / "j", max_queued_runs=bad)
