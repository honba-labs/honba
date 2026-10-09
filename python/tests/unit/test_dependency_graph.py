"""The crate-layering script enforces the allowed crate dependencies."""

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


def test_unlisted_crate_may_not_use_tokio(graph, tmp_path):
    d = _crate(tmp_path, "honba-indicators", 'tokio = "1"')
    assert graph.check_crate(d) == 1
