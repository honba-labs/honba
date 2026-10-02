"""Export the canonical JSON Schema and generate TypeScript contracts (Pillar P2 / ADR 006).

Single source of truth pipeline:
Rust serde domain types (L0/L1/L4) -> Python pydantic models (honba.entities.wire)
  -> JSON Schema export (schema/domain/)
  -> TypeScript interface definitions (honba-frontend/src/core/types/generated/)

Lives in the installed package so `honba schema export` works from a wheel;
`scripts/export_schema.py` is a thin repo-checkout wrapper around it.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

from honba.entities import screener
from honba.entities import wire
from pydantic import TypeAdapter


def export_json_schema(output_dir: Path) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    bundle_path = output_dir / "domain_schema.json"

    schema = {
        "$schema": "http://json-schema.org/draft-07/schema#",
        "title": "HonbaDomainEnvelope",
        "description": "Honba canonical wire models generated from Rust single source of truth contracts",
        "type": "object",
        "properties": {
            "message": {"$ref": "#/$defs/Message"},
            "event": {"$ref": "#/$defs/Event"},
            "order": {"$ref": "#/$defs/Order"},
            "order_intent": {"$ref": "#/$defs/OrderIntent"},
            "trade": {"$ref": "#/$defs/Trade"},
            "position": {"$ref": "#/$defs/Position"},
            "bar": {"$ref": "#/$defs/Bar"},
            "instrument_id": {"$ref": "#/$defs/InstrumentId"},
            "metric_key_spec": {"$ref": "#/$defs/MetricKeySpec"},
            "metric_definition": {"$ref": "#/$defs/MetricDefinition"},
            "screener_filter_predicate": {"$ref": "#/$defs/ScreenerFilterPredicate"},
            "screener_filter_group": {"$ref": "#/$defs/ScreenerFilterGroup"},
            "screener_sort_spec": {"$ref": "#/$defs/ScreenerSortSpec"},
            "screener_scan_request": {"$ref": "#/$defs/ScreenerScanRequest"},
            "screener_row": {"$ref": "#/$defs/ScreenerRow"},
            "screener_scan_response": {"$ref": "#/$defs/ScreenerScanResponse"},
        },
        "$defs": {},
    }

    all_adapters = dict(wire._ADAPTERS)
    screener_adapters = {
        "MetricKeySpec": TypeAdapter(screener.MetricKeySpec),
        "MetricDefinition": TypeAdapter(screener.MetricDefinition),
        "ScreenerFilterPredicate": TypeAdapter(screener.ScreenerFilterPredicate),
        "ScreenerFilterGroup": TypeAdapter(screener.ScreenerFilterGroup),
        "ScreenerSortSpec": TypeAdapter(screener.ScreenerSortSpec),
        "ScreenerScanRequest": TypeAdapter(screener.ScreenerScanRequest),
        "ScreenerRow": TypeAdapter(screener.ScreenerRow),
        "ScreenerScanResponse": TypeAdapter(screener.ScreenerScanResponse),
    }
    all_adapters.update(screener_adapters)

    for name, adapter in all_adapters.items():
        s = adapter.json_schema()
        if "$defs" in s:
            for def_name, def_schema in s["$defs"].items():
                schema["$defs"][def_name] = def_schema
            s_copy = dict(s)
            del s_copy["$defs"]
            schema["$defs"][name] = s_copy
        else:
            schema["$defs"][name] = s

    bundle_path.write_text(json.dumps(schema, indent=2) + "\n")
    print(f"Exported JSON Schema: {bundle_path} ({len(schema['$defs'])} definitions)")
    return bundle_path


def generate_typescript(schema_file: Path, ts_output_dir: Path, cwd: Path | None = None) -> None:
    ts_output_dir.mkdir(parents=True, exist_ok=True)
    out_file = ts_output_dir / "domain.ts"

    cmd = [
        "npx",
        "--yes",
        "json-schema-to-typescript",
        "-i",
        str(schema_file),
        "-o",
        str(out_file),
    ]

    print(f"Generating TypeScript contracts via: {' '.join(cmd)}")
    subprocess.run(cmd, check=True, cwd=str(cwd or Path.cwd()))
    print(f"Generated TypeScript definitions: {out_file}")
