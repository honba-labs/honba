#!/usr/bin/env python3
"""Repo-checkout wrapper: regenerate schema/domain and, optionally, the frontend TypeScript contracts.

The logic lives in `honba.cli._schema_export` (shipped in the wheel); this script only
supplies the checkout paths. It locates the honba checkout from its own location, so it works
in a single-repo checkout (CI) as well as in the multi-repo workspace.

    python3 scripts/export_schema.py                          # JSON bundle only
    python3 scripts/export_schema.py --frontend-dir DIR       # also emit DIR/domain.ts
    HONBA_FRONTEND_DIR=DIR python3 scripts/export_schema.py   # same, via environment

A relative frontend dir is resolved against the current working directory.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

honba_root = Path(__file__).resolve().parents[1]
honba_python = honba_root / "python"
if str(honba_python) not in sys.path:
    sys.path.insert(0, str(honba_python))

from honba.cli._schema_export import export_json_schema, generate_typescript


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument(
        "--frontend-dir",
        default=os.environ.get("HONBA_FRONTEND_DIR") or None,
        help="directory for generated domain.ts (default: $HONBA_FRONTEND_DIR; unset = skip)",
    )
    args = parser.parse_args(argv)

    schema_file = export_json_schema(honba_root / "schema" / "domain")
    if args.frontend_dir is None:
        print(
            "Skipping TypeScript step: no --frontend-dir / HONBA_FRONTEND_DIR given "
            "(JSON schema export only)."
        )
        return
    generate_typescript(schema_file, Path(args.frontend_dir), cwd=honba_root)
    print("Codegen complete and consistent across Rust -> Python -> Schema -> TypeScript.")


if __name__ == "__main__":
    main()
