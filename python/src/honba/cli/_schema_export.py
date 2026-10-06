"""Write the generated contract artifacts (Pillar P2 / ADR 006).

Rust (`honba-codegen`) is the single owner of every generated artifact: the JSON Schema bundle
(`schema/domain/`), OpenAPI (`schema/openapi/`), MCP tool schemas (`schema/mcp/`), the frontend
TypeScript and the Python stubs (`honba/wire/generated/`). This module only writes what the
compiled extension renders (`honba._honba.codegen_render`), so the output is byte-for-byte what
the Rust `honba schema export` binary writes, and it needs neither cargo nor npx: it works from an
installed wheel.

Lives in the installed package so `honba schema export` works outside a repo checkout;
`scripts/export_schema.py` is the repo-checkout wrapper around the Rust binary.
"""

from __future__ import annotations

from pathlib import Path

from honba._native import native_attr


def export_artifact(kind: str, output_dir: Path) -> Path:
    """Write the artifact `kind` (see `_honba.codegen_artifacts()`) into `output_dir`."""
    file_name, content = native_attr("codegen_render")(kind)
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / file_name
    path.write_text(content)
    return path


def export_json_schema(output_dir: Path) -> Path:
    """Write `domain_schema.json`, the JSON Schema conformance bundle."""
    return export_artifact("json_schema", output_dir)


def generate_typescript(ts_output_dir: Path) -> Path:
    """Write `domain.ts`, the TypeScript declarations for honba-frontend."""
    return export_artifact("typescript", ts_output_dir)
