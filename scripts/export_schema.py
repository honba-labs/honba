#!/usr/bin/env python3
"""Export canonical JSON Schema and generate TypeScript contracts from Honba domain models.

Single source of truth pipeline (Pillar P2 / ADR 006):
Rust serde domain types (L0/L1/L4) -> Python pydantic models (honba.entities.wire)
  -> JSON Schema export (honba/schema/domain/)
  -> TypeScript interface definitions (honba-frontend/src/core/types/generated/)
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

# Ensure honba is importable
repo_root = Path(__file__).resolve().parents[2]
honba_python = repo_root / "honba" / "python"
if str(honba_python) not in sys.path:
    sys.path.insert(0, str(honba_python))

from honba.entities import wire  # noqa: E402


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
        },
        "$defs": {},
    }

    for name, adapter in wire._ADAPTERS.items():
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


def generate_typescript(schema_file: Path, ts_output_dir: Path) -> None:
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
    subprocess.run(cmd, check=True, cwd=str(repo_root))
    print(f"Generated TypeScript definitions: {out_file}")


def main() -> None:
    schema_dir = repo_root / "honba" / "schema" / "domain"
    ts_dir = repo_root / "honba-frontend" / "src" / "core" / "types" / "generated"

    schema_file = export_json_schema(schema_dir)
    generate_typescript(schema_file, ts_dir)
    print("Codegen complete and consistent across Rust -> Python -> Schema -> TypeScript.")


if __name__ == "__main__":
    main()
