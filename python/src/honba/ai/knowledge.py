"""Knowledge pack generation and drift check for LLM query translation (Design.md Section 13.5).

Generates a versioned, content-hashed dictionary/JSON artifact containing:
- EBNF filter grammar
- Wire enums (FilterOp, Timeframe, MetricPeriod)
- Metric catalog: keys, aliases, lookbacks, units, groups
- Presets: keys, labels, aliases, target metrics
- Currency units (India / shared)
- Golden examples for few-shot prompting
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from typing import Any

from honba.entities.screener import FilterOp, MetricPeriod, Timeframe
from honba.markets.india.units import INDIA_CURRENCY_SYMBOLS, INDIA_MULTIPLIERS
from honba.query.quantity import SHARED_MULTIPLIERS
from honba.screener.catalog import load_catalog
from honba.screener.presets import LOOKBACK_BARS, PRESETS


FILTER_GRAMMAR_EBNF = """
filters    := or_expr
or_expr    := and_expr { "or" and_expr }
and_expr   := term { "and" term }
term       := "either" or_expr "end"
            | filter
filter     := metric [ "on" TIMEFRAME ] [ "for" PERIOD ] predicate
predicate  := cmp quantity
            | "between" quantity "and" quantity
            | ("in" | "not in") value { "," value }
            | ("contains" | "like") text
            | ("crosses above" | "crosses below") operand
            | ("at" | "near") preset_target
            | "within" quantity "of" preset_target
cmp        := "above" | "over" | "greater than" | "more than" | ">"
            | "at least" | "no less than" | ">="
            | "below" | "under" | "less than" | "<"
            | "at most" | "no more than" | "<="
            | "equals" | "is" | "="
            | "is not" | "not equal to" | "!="
quantity   := NUMBER [ SUFFIX ]
operand    := quantity | metric
"""

FEW_SHOT_EXAMPLES: list[dict[str, Any]] = [
    {
        "query": "market cap above 10000 Cr and rsi below 30",
        "filter_text": "market cap above 10000 Cr and rsi below 30",
        "resolved_predicates": [
            {"key": "market_cap_basic", "op": "gt", "value": 100000000000.0},
            {"key": "RSI", "op": "lt", "value": 30.0},
        ],
    },
    {
        "query": "stocks near 52 week low with pe under 20",
        "filter_text": "close near 52 week low and pe ratio under 20",
        "resolved_predicates": [
            {"key": "close", "op": "lte", "value": {"metric": "price_52_week_low", "tolerance": 0.05}},
            {"key": "price_earnings_ttm", "op": "lt", "value": 20.0},
        ],
    },
    {
        "query": "nifty companies with volume at least 5 Lk",
        "filter_text": "volume at least 5 Lk",
        "resolved_predicates": [
            {"key": "volume", "op": "gte", "value": 500000.0},
        ],
    },
]


@dataclass(frozen=True)
class KnowledgePack:
    version: str
    content_hash: str
    grammar_ebnf: str
    enums: dict[str, list[str]]
    metrics: list[dict[str, Any]]
    presets: list[dict[str, Any]]
    units: dict[str, float]
    few_shot_examples: list[dict[str, Any]]

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "content_hash": self.content_hash,
            "grammar_ebnf": self.grammar_ebnf,
            "enums": self.enums,
            "metrics": self.metrics,
            "presets": self.presets,
            "units": self.units,
            "few_shot_examples": self.few_shot_examples,
        }

    def to_json(self, indent: int = 2) -> str:
        return json.dumps(self.to_dict(), indent=indent, sort_keys=True)


def build_knowledge_pack(version: str = "1.0.0") -> KnowledgePack:
    """Build the versioned Knowledge Pack from active single sources of truth."""
    catalog = load_catalog()

    metrics_list: list[dict[str, Any]] = []
    for m in catalog:
        metrics_list.append(
            {
                "key": m.key,
                "group": m.group,
                "value_type": m.value_type.value,
                "unit": m.unit.value if m.unit else None,
                "aliases": list(m.aliases),
                "lookback_bars": LOOKBACK_BARS.get(m.key, 1),
                "has_timeframe": m.has_timeframe,
                "has_period": m.has_period,
            }
        )
    metrics_list.sort(key=lambda x: x["key"])

    presets_list: list[dict[str, Any]] = []
    for p in PRESETS.values():
        presets_list.append(
            {
                "key": p.key,
                "label": p.label,
                "description": p.description,
                "target_metric": p.target_metric,
                "aliases": list(p.aliases),
                "lookback_bars": p.lookback_bars,
            }
        )
    presets_list.sort(key=lambda x: x["key"])

    all_units: dict[str, float] = {}
    for spec in SHARED_MULTIPLIERS.values():
        all_units[spec.canonical] = spec.multiplier
    for spec in INDIA_MULTIPLIERS.values():
        all_units[spec.canonical] = spec.multiplier

    enums_map = {
        "FilterOp": [op.value for op in FilterOp],
        "Timeframe": [tf.value for tf in Timeframe],
        "MetricPeriod": [p.value for p in MetricPeriod],
    }

    # Compute stable content hash across all components
    payload_to_hash = {
        "version": version,
        "grammar": FILTER_GRAMMAR_EBNF.strip(),
        "enums": enums_map,
        "metrics": metrics_list,
        "presets": presets_list,
        "units": {k: float(v) for k, v in sorted(all_units.items())},
        "examples": FEW_SHOT_EXAMPLES,
    }
    dumped = json.dumps(payload_to_hash, sort_keys=True)
    content_hash = hashlib.sha256(dumped.encode("utf-8")).hexdigest()[:16]

    return KnowledgePack(
        version=version,
        content_hash=content_hash,
        grammar_ebnf=FILTER_GRAMMAR_EBNF.strip(),
        enums=enums_map,
        metrics=metrics_list,
        presets=presets_list,
        units={k: float(v) for k, v in sorted(all_units.items())},
        few_shot_examples=FEW_SHOT_EXAMPLES,
    )
