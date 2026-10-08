"""`import honba` and the pure-Python paths work without the compiled extension.

Each case runs in a fresh interpreter so the import is clean. The extension is hidden by
making ``honba._honba`` raise ``ImportError`` (missing) or by installing a stub module that
lacks a symbol (stale). Native-backed values raise a clear error on first use only.
"""

from __future__ import annotations

import subprocess
import sys
import textwrap

import pytest

HIDE = "import sys; sys.modules['honba._honba'] = None\n"
STALE = "import sys, types; m = types.ModuleType('honba._honba'); sys.modules['honba._honba'] = m\n"


def _run(prelude: str, body: str) -> subprocess.CompletedProcess[str]:
    code = prelude + textwrap.dedent(body)
    return subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, check=False, timeout=120
    )


@pytest.mark.parametrize(
    "module",
    [
        "honba",
        "honba.domain",
        "honba.entities",
        "honba.wire",
        "honba.strategies",
        "honba.strategies.manifest",
        "honba.strategies.verify",
        "honba.report",
        "honba.screener",
        "honba.backtest",
        "honba.risk",
    ],
)
def test_pure_python_modules_import_without_extension(module):
    r = _run(HIDE, f"import importlib; importlib.import_module({module!r})\n")
    assert r.returncode == 0, r.stderr


def test_domain_names_usable_without_extension():
    r = _run(
        HIDE,
        """
        import honba
        from honba.domain import Bar, Instrument, OrderIntent
        assert honba.Strategy and honba.Portfolio
        """,
    )
    assert r.returncode == 0, r.stderr


@pytest.mark.parametrize(
    "access",
    [
        "honba.SCHEMA_VERSION",
        "honba.wire.API_VERSION",
        "honba.entities.SCHEMA_VERSION",
        "honba.strategies.manifest.STRATEGY_API_VERSION",
        "honba.domain.money.Currency.INR.minor_exponent",
    ],
)
def test_native_use_raises_clear_import_error_when_missing(access):
    r = _run(
        HIDE, f"import honba, honba.wire, honba.entities, honba.strategies.manifest\n{access}\n"
    )
    assert r.returncode != 0
    assert "ImportError" in r.stderr
    assert "native extension" in r.stderr
    assert "maturin" in r.stderr


def test_verify_manifest_raises_clear_import_error_when_missing():
    r = _run(HIDE, "from honba.strategies.verify import verify_manifest\nverify_manifest('{}')\n")
    assert r.returncode != 0
    assert "ImportError" in r.stderr and "native extension" in r.stderr


def test_stale_extension_names_the_missing_symbol():
    r = _run(STALE, "import honba\nhonba.SCHEMA_VERSION\n")
    assert r.returncode != 0
    assert "RuntimeError" in r.stderr
    assert "SCHEMA_VERSION" in r.stderr
    assert "rebuild" in r.stderr


def test_values_match_the_extension_when_present():
    r = _run(
        "",
        """
        import honba, honba._honba as n
        from honba.strategies.manifest import STRATEGY_API_VERSION
        assert honba.SCHEMA_VERSION == n.SCHEMA_VERSION
        assert honba.wire.API_VERSION == n.API_VERSION
        assert STRATEGY_API_VERSION == n.STRATEGY_API_VERSION
        """,
    )
    assert r.returncode == 0, r.stderr
