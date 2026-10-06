"""Capability-aware registry helpers: runtime checks and the static typing contract."""

from __future__ import annotations

import shutil
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from honba.adapters.base import Adapter
from honba.adapters.errors import AdapterError
from honba.adapters.registry import AdapterRegistry
from honba.adapters.testing import FakeAdapter


class _LifecycleOnly(Adapter):
    """An adapter with neither market data nor execution methods."""

    def capabilities(self):  # type: ignore[no-untyped-def]
        return FakeAdapter().capabilities()

    async def connect(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError

    async def disconnect(self) -> None:
        return None

    def is_connected(self) -> bool:
        return False

    async def session(self):  # type: ignore[no-untyped-def]
        raise NotImplementedError


@pytest.fixture
def reg() -> AdapterRegistry:
    registry = AdapterRegistry()
    registry.register("fake", FakeAdapter)
    registry.register("bare", _LifecycleOnly)
    return registry


class TestRuntime:
    def test_market_data_helper_returns_the_adapter(self, reg: AdapterRegistry) -> None:
        adapter = reg.create_market_data("fake")
        assert isinstance(adapter, FakeAdapter)

    def test_execution_helper_returns_the_adapter(self, reg: AdapterRegistry) -> None:
        assert isinstance(reg.create_execution("fake"), FakeAdapter)

    def test_market_data_helper_rejects_adapter_without_the_capability(
        self, reg: AdapterRegistry
    ) -> None:
        with pytest.raises(AdapterError, match="market data"):
            reg.create_market_data("bare")

    def test_execution_helper_rejects_adapter_without_the_capability(
        self, reg: AdapterRegistry
    ) -> None:
        with pytest.raises(AdapterError, match="execution"):
            reg.create_execution("bare")

    def test_plain_create_is_unchanged(self, reg: AdapterRegistry) -> None:
        assert isinstance(reg.create("bare"), _LifecycleOnly)


_SNIPPET_TYPED = """
    from honba.adapters.registry import AdapterRegistry

    async def run(reg: AdapterRegistry) -> None:
        adapter = reg.create_market_data("fake")
        await adapter.connect()
        await adapter.search_instruments("INFY")
        await adapter.instruments()
        execution = reg.create_execution("fake")
        await execution.positions()
"""

_SNIPPET_UNTYPED = """
    from honba.adapters.registry import AdapterRegistry

    async def run(reg: AdapterRegistry) -> None:
        adapter = reg.create("fake")
        await adapter.search_instruments("INFY")
"""


def _mypy(tmp_path: Path, source: str) -> subprocess.CompletedProcess[str]:
    snippet = tmp_path / "snippet.py"
    snippet.write_text(textwrap.dedent(source))
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "mypy",
            "--no-incremental",
            "--cache-dir",
            str(tmp_path / "c"),
            "--strict",
            str(snippet),
        ],
        capture_output=True,
        text=True,
        check=False,
    )


@pytest.mark.skipif(shutil.which("mypy") is None, reason="mypy not installed")
class TestStaticTyping:
    """Pyright is not installed in CI; mypy checks the same attribute-access contract."""

    def test_capability_aware_helpers_type_check(self, tmp_path: Path) -> None:
        result = _mypy(tmp_path, _SNIPPET_TYPED)
        assert "snippet.py" not in result.stdout, result.stdout

    def test_bare_create_still_has_no_market_data_attributes(self, tmp_path: Path) -> None:
        result = _mypy(tmp_path, _SNIPPET_UNTYPED)
        assert 'has no attribute "search_instruments"' in result.stdout
