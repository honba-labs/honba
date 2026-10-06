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


DATACLASS_SRC = """
from __future__ import annotations

from dataclasses import dataclass

from honba.strategies.base import Strategy


@dataclass
class Params:
    fast: int = 5


class {cls}(Strategy):
    name = "{name}"
    params = Params()
"""


def test_a_strategy_file_with_a_dataclass_and_future_annotations_loads(tmp_path: Path) -> None:
    path = tmp_path / "dc.py"
    path.write_text(DATACLASS_SRC.format(cls="Dc", name="dc"))
    assert load_strategy(path).params.fast == 5


def test_a_catalog_strategy_with_a_dataclass_and_future_annotations_loads(catalog: Path) -> None:
    d = catalog / "swing" / "dc"
    d.mkdir()
    (d / "strategy.py").write_text(DATACLASS_SRC.format(cls="Dc", name="dc"))
    (d / "config.toml").write_text(CONFIG.format(name="dc"))
    (d / "registry.json").write_text(json.dumps({"name": "dc"}))
    assert load_catalog_strategy("dc", catalog).cls.params.fast == 5


def test_a_failed_import_leaves_nothing_in_sys_modules(tmp_path: Path) -> None:
    import sys

    path = tmp_path / "boom.py"
    path.write_text("raise RuntimeError('boom')\n")
    before = set(sys.modules)
    with pytest.raises(RuntimeError, match="boom"):
        load_strategy(path)
    assert set(sys.modules) == before


def _registry(catalog: Path, entries: object) -> None:
    (catalog / "registry.json").write_text(json.dumps(entries))


def test_a_registry_path_escaping_the_catalog_is_refused(catalog: Path, tmp_path: Path) -> None:
    outside = tmp_path / "outside" / "evil"
    _strategy(outside, "evil", "Evil")
    _registry(catalog, {"strategies": [{"name": "evil", "path": "../outside/evil"}]})
    with pytest.raises(CatalogError, match="outside the catalog"):
        load_catalog_strategy("evil", catalog)
    _registry(catalog, {"strategies": [{"name": "evil", "path": str(outside)}]})
    with pytest.raises(CatalogError, match="outside the catalog"):
        load_catalog_strategy("evil", catalog)


def test_a_per_strategy_registry_path_escaping_the_catalog_is_refused(
    catalog: Path, tmp_path: Path
) -> None:
    _strategy(tmp_path / "elsewhere", "esc", "Esc")
    d = catalog / "swing" / "esc"
    d.mkdir()
    (d / "registry.json").write_text(json.dumps({"name": "esc", "path": "../../../elsewhere"}))
    with pytest.raises(CatalogError, match="outside the catalog"):
        load_catalog_strategy("esc", catalog)


@pytest.mark.parametrize("hidden", [".venv", "node_modules", ".git", "__pycache__", ".hidden"])
def test_hidden_and_vendor_dirs_are_not_scanned_for_registries(catalog: Path, hidden: str) -> None:
    d = catalog / hidden / "pkg" / "ghost"
    _strategy(d, "ghost", "Ghost")
    (d / "registry.json").write_text(json.dumps({"name": "ghost"}))
    with pytest.raises(CatalogError, match="available: alpha, beta$"):
        load_catalog_strategy("ghost", catalog)


@pytest.mark.parametrize(
    "bad",
    [
        [],
        {"strategies": "alpha"},
        {"strategies": ["alpha"]},
        {"strategies": [{"path": "momentum/trend/alpha"}]},
        {"strategies": [{"name": "alpha"}]},
        {"strategies": [{"name": 3, "path": "x"}]},
    ],
)
def test_a_malformed_registry_raises_a_clear_error(catalog: Path, bad: object) -> None:
    _registry(catalog, bad)
    with pytest.raises(CatalogError, match="registry"):
        load_catalog_strategy("alpha", catalog)


def test_unparseable_registry_json_raises_a_clear_error(catalog: Path) -> None:
    (catalog / "registry.json").write_text("{not json")
    with pytest.raises(CatalogError, match="registry"):
        load_catalog_strategy("alpha", catalog)


def test_loading_restores_sys_path(catalog: Path) -> None:
    import sys

    before = list(sys.path)
    load_catalog_strategy("alpha", catalog)
    assert sys.path == before


def test_sibling_modules_do_not_collide_between_strategies(tmp_path: Path) -> None:
    root = tmp_path / "cat"
    entries = []
    for name, value in (("one", 1), ("two", 2)):
        d = root / "g" / name
        d.mkdir(parents=True)
        (d / "helpers.py").write_text(f"VALUE = {value}\n")
        (d / "strategy.py").write_text(
            "from honba.strategies.base import Strategy\nimport helpers\n\n\n"
            f"class S(Strategy):\n    name = '{name}'\n    value = helpers.VALUE\n"
        )
        (d / "config.toml").write_text(CONFIG.format(name=name))
        entries.append({"name": name, "path": f"g/{name}"})
    _registry(root, {"strategies": entries})
    assert load_catalog_strategy("one", root).cls.value == 1
    assert load_catalog_strategy("two", root).cls.value == 2


def _digest(catalog: Path) -> str:
    return load_catalog_strategy("alpha", catalog).source_sha256


def test_the_source_hash_covers_the_whole_strategy_directory(catalog: Path) -> None:
    d = catalog / "momentum" / "trend" / "alpha"
    before = _digest(catalog)
    (d / "helpers.py").write_text("X = 1\n")
    with_helper = _digest(catalog)
    assert with_helper != before
    (d / "helpers.py").write_text("X = 2\n")
    assert _digest(catalog) != with_helper


def test_the_source_hash_ignores_pycache_and_hidden_files(catalog: Path) -> None:
    d = catalog / "momentum" / "trend" / "alpha"
    before = _digest(catalog)
    (d / "__pycache__").mkdir(exist_ok=True)
    (d / "__pycache__" / "strategy.cpython-314.pyc").write_bytes(b"\x00\x01")
    (d / ".DS_Store").write_bytes(b"junk")
    (d / ".cache").mkdir()
    (d / ".cache" / "x").write_text("x")
    assert _digest(catalog) == before


def test_the_source_hash_is_unambiguous_about_file_boundaries(tmp_path: Path) -> None:
    def digest(files: dict[str, str]) -> str:
        root = tmp_path / f"c{abs(hash(tuple(files.items())))}"
        d = root / "s"
        _strategy(d, "alpha", "Alpha")
        for fname, text in files.items():
            (d / fname).write_text(text)
        _registry(root, {"strategies": [{"name": "alpha", "path": "s"}]})
        return load_catalog_strategy("alpha", root).source_sha256

    assert digest({"a.txt": "xy", "b.txt": "z"}) != digest({"a.txt": "x", "b.txt": "yz"})
    assert digest({"a.txt": "x", "b.txt": "y"}) == digest({"b.txt": "y", "a.txt": "x"})
