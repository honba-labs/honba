"""`honba/_honba.pyi` matches the compiled extension (mypy stubtest, as run in CI)."""

from __future__ import annotations

import subprocess
import sys

import pytest

pytest.importorskip("mypy.stubtest")


def test_native_stub_matches_the_extension() -> None:
    try:
        from honba import _honba  # noqa: F401
    except ImportError:
        pytest.skip("native extension not built")
    done = subprocess.run(
        [sys.executable, "-m", "mypy.stubtest", "honba._honba"],
        capture_output=True,
        text=True,
        timeout=300,
        check=False,
    )
    assert done.returncode == 0, done.stdout + done.stderr
