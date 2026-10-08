"""The crate-layering script knows about the broker adapter crates."""

import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[3]
SCRIPT = ROOT / "scripts" / "dependency_graph.py"


@pytest.fixture(scope="module")
def graph():
    spec = importlib.util.spec_from_file_location("dependency_graph", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _crate(tmp_path, name, deps="", dev=""):
    d = tmp_path / name
    d.mkdir()
    (d / "Cargo.toml").write_text(
        f'[package]\nname = "{name}"\n[dependencies]\n{deps}\n[dev-dependencies]\n{dev}\n'
    )
    return d


def test_real_workspace_passes(graph):
    assert graph.main() == 0


def test_zerodha_registered_with_inward_deps_only(graph):
    allowed = graph.ALLOWED_PROD["honba-broker-zerodha"]
    assert allowed == {"honba-messages", "honba-entities", "honba-ports"}


def test_zerodha_may_not_reach_engine(graph, tmp_path):
    d = _crate(tmp_path, "honba-broker-zerodha", 'honba-engine = { path = "x" }')
    assert graph.check_crate(d) == 1


def test_zerodha_may_not_depend_on_pyo3(graph, tmp_path):
    d = _crate(tmp_path, "honba-broker-zerodha", 'pyo3 = "0.22"')
    assert graph.check_crate(d) == 1


def test_no_core_crate_depends_on_a_broker_crate(graph):
    for name, allowed in graph.ALLOWED_PROD.items():
        assert "honba-broker-zerodha" not in allowed, name


def test_zerodha_is_an_async_boundary_but_not_a_core_crate(graph):
    assert "honba-broker-zerodha" in graph.ASYNC_BOUNDARY_CRATES
    assert "honba-broker-zerodha" not in graph.SYNC_KERNEL_CRATES
    assert "honba-broker-zerodha" not in graph.WASM_CRATES


def test_unlisted_crate_may_not_use_tokio(graph, tmp_path):
    d = _crate(tmp_path, "honba-indicators", 'tokio = "1"')
    assert graph.check_crate(d) == 1
