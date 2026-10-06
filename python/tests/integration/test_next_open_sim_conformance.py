"""Replays ``schema/conformance/next_open_sim.json`` against the Python ``NextOpenExecution``.

The file is generated from the reference by ``scripts/gen_next_open_vectors.py`` (ADR 0016) and
the Rust test ``crates/honba-sim/tests/next_open_conformance.rs`` replays the same scenarios. This
test fails when the committed vectors drift from the reference (regenerate deliberately) or when
the file is not what the generator renders.
"""

from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from typing import Any

import pytest

ROOT = Path(__file__).resolve().parents[3]
VECTORS = ROOT / "schema" / "conformance" / "next_open_sim.json"
DOC = json.loads(VECTORS.read_text(encoding="utf-8"))

_spec = importlib.util.spec_from_file_location(
    "gen_next_open_vectors", ROOT / "scripts" / "gen_next_open_vectors.py"
)
assert _spec is not None and _spec.loader is not None
gen = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(gen)


@pytest.mark.parametrize("scenario", DOC["scenarios"], ids=lambda s: s["name"])
def test_next_open_vector_matches_the_python_reference(scenario: dict[str, Any]) -> None:
    got = gen.replay(scenario)
    assert got["steps"] == scenario["steps"]
    assert got["expect"]["drains"] == scenario["expect"]["drains"]
    final = {k: v for k, v in got["expect"].items() if k != "drains"}
    assert final == scenario["expect"]["final"]


def test_vectors_are_what_the_generator_renders() -> None:
    assert VECTORS.read_text(encoding="utf-8") == gen.render(gen.build())


def test_every_scenario_is_tagged_with_a_chunk_and_names_are_unique() -> None:
    names = [s["name"] for s in DOC["scenarios"]]
    assert len(names) == len(set(names))
    assert {s["chunk"] for s in DOC["scenarios"]} <= {1, 2, 3}


def test_only_the_intended_scenarios_record_an_error_step() -> None:
    """A vector authoring mistake (an invalid intent) must not masquerade as a port error."""
    failing = {s["name"] for s in DOC["scenarios"] if any(x.get("error") for x in s["steps"])}
    assert failing == {
        "duplicate_working_order_id_is_an_error",
        "bar_before_the_session_is_non_monotonic",
        "second_bar_for_an_instrument_in_a_session_is_an_error",
        "open_session_must_advance",
    }
