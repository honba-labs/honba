"""``honba.strategies.loader``: load a strategy from a ``.py`` file or a catalog by name."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from honba.strategies.base import Strategy
from honba.strategies.loader import (
    CATALOG_ENV,
    CatalogError,
    find_catalog,
    load_catalog_strategy,
    load_strategy,
)

STRATEGY_SRC = """
from honba.strategies.base import Strategy


class Helper:
    pass


class {cls}(Strategy):
    name = "{name}"
    warmup_bars = 7

    def __init__(self, config=None):
        self.config = config
"""

CONFIG = 'name = "{name}"\nsymbol = "X"\nwarmup_bars = 3\n\n[params]\nfast = 5\n'


def _strategy(dirpath: Path, name: str, cls: str = "Probe") -> None:
    dirpath.mkdir(parents=True)
    (dirpath / "strategy.py").write_text(STRATEGY_SRC.format(cls=cls, name=name))
    (dirpath / "config.toml").write_text(CONFIG.format(name=name))


@pytest.fixture
def catalog(tmp_path: Path) -> Path:
    root = tmp_path / "catalog"
    _strategy(root / "momentum" / "trend" / "alpha", "alpha", "Alpha")
    _strategy(root / "swing" / "beta", "beta", "Beta")
    (root / "registry.json").write_text(
        json.dumps({"strategies": [{"name": "alpha", "path": "momentum/trend/alpha"}]})
    )
    # A per-strategy registry is enough to be found.
    (root / "swing" / "beta" / "registry.json").write_text(json.dumps({"name": "beta"}))
    return root


def test_load_strategy_from_a_file(tmp_path: Path) -> None:
    path = tmp_path / "mine.py"
    path.write_text(STRATEGY_SRC.format(cls="Mine", name="mine"))
    cls = load_strategy(path)
    assert issubclass(cls, Strategy) and cls.name == "mine"


def test_load_strategy_refuses_a_file_without_exactly_one_strategy(tmp_path: Path) -> None:
    none = tmp_path / "none.py"
    none.write_text("x = 1\n")
    with pytest.raises(CatalogError, match="no Strategy subclass"):
        load_strategy(none)
    two = tmp_path / "two.py"
    two.write_text(
        STRATEGY_SRC.format(cls="One", name="one") + STRATEGY_SRC.format(cls="Two", name="two")
    )
    with pytest.raises(CatalogError, match="2 Strategy subclasses"):
        load_strategy(two)
    with pytest.raises(CatalogError, match="not found"):
        load_strategy(tmp_path / "missing.py")


def test_find_catalog_prefers_explicit_then_env(
    catalog: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CATALOG_ENV, raising=False)
    assert find_catalog(catalog) == catalog.resolve()
    with pytest.raises(CatalogError, match="no registry.json"):
        find_catalog(tmp_path)
    monkeypatch.setenv(CATALOG_ENV, str(catalog))
    assert find_catalog() == catalog.resolve()


def test_find_catalog_without_hints_fails_with_instructions(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(CATALOG_ENV, raising=False)
    with pytest.raises(CatalogError, match=CATALOG_ENV):
        find_catalog()


def test_find_catalog_searches_siblings_only_when_asked(catalog: Path, monkeypatch) -> None:
    monkeypatch.delenv(CATALOG_ENV, raising=False)
    sibling = catalog.parent / "honba-strategies"
    catalog.rename(sibling)
    repo = catalog.parent / "honba-examples"
    repo.mkdir()
    assert find_catalog(search_from=repo) == sibling.resolve()


def test_load_catalog_strategy_by_registry_name(catalog: Path) -> None:
    loaded = load_catalog_strategy("alpha", catalog)
    assert loaded.cls.name == "alpha"
    assert loaded.config.params == {"fast": 5} and loaded.config.warmup_bars == 3
    assert loaded.path == (catalog / "momentum" / "trend" / "alpha").resolve()
    assert len(loaded.source_sha256) == 64
    strategy = loaded.instantiate()
    assert strategy.config is loaded.config
    assert load_catalog_strategy("beta", catalog).cls.name == "beta"


def test_unknown_names_list_what_is_available(catalog: Path) -> None:
    with pytest.raises(CatalogError, match="available: alpha, beta"):
        load_catalog_strategy("gamma", catalog)


def test_the_source_hash_tracks_strategy_and_config(catalog: Path) -> None:
    before = load_catalog_strategy("alpha", catalog).source_sha256
    cfg = catalog / "momentum" / "trend" / "alpha" / "config.toml"
    cfg.write_text(cfg.read_text() + "exchange = 'BSE'\n")
    assert load_catalog_strategy("alpha", catalog).source_sha256 != before
