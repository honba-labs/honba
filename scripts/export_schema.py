#!/usr/bin/env python3
"""Repo-checkout wrapper: regenerate schema/domain and the frontend TypeScript contracts.

The logic lives in `honba.cli._schema_export` (shipped in the wheel); this script only
supplies the checkout paths. Run as `PYTHONPATH=python python3 scripts/export_schema.py`.
"""

from __future__ import annotations

import sys
from pathlib import Path

repo_root = Path(__file__).resolve().parents[2]
honba_python = repo_root / "honba" / "python"
if str(honba_python) not in sys.path:
    sys.path.insert(0, str(honba_python))

from honba.cli._schema_export import export_json_schema, generate_typescript


def main() -> None:
    schema_dir = repo_root / "honba" / "schema" / "domain"
    ts_dir = repo_root / "honba-frontend" / "src" / "core" / "types" / "generated"

    schema_file = export_json_schema(schema_dir)
    generate_typescript(schema_file, ts_dir, cwd=repo_root)
    print("Codegen complete and consistent across Rust -> Python -> Schema -> TypeScript.")


if __name__ == "__main__":
    main()
