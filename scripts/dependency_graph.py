#!/usr/bin/env python3
"""Enforce the Honba crate dependency hierarchy and layering rules."""
import sys, tomllib
from pathlib import Path

# Crate layering hierarchy (L0 to L7) — plan.md / 0006 — Rust is schema source.
# L0: honba-messages
# L1: honba-entities
# L2: honba-market
# L3: honba-engine, honba-indicators
# L4: honba-sim, honba-strategy
# L5: honba-analytics, honba-data, honba-codegen (schemars source-of-truth)
# L6: honba-testing (fixtures / test helpers only)
# L7: honba-py (cdylib), honba-cli (bin; consumes codegen for `honba schema`)

ALLOWED_PROD = {
    "honba-messages": set(),
    "honba-entities": {"honba-messages"},
    "honba-market": {"honba-entities", "honba-messages"},
    "honba-engine": {"honba-messages", "honba-entities"},
    "honba-indicators": {"honba-messages", "honba-entities"},
    "honba-sim": {"honba-messages", "honba-entities", "honba-engine"},
    "honba-strategy": {"honba-engine", "honba-indicators", "honba-messages", "honba-entities"},
    "honba-testing": {"honba-engine", "honba-messages", "honba-entities", "honba-sim"},
    "honba-analytics": {"honba-messages", "honba-entities"},
    "honba-data": {"honba-messages", "honba-entities", "honba-analytics"},
    "honba-codegen": {"honba-messages", "honba-entities", "honba-strategy"},
    "honba-py": {"honba-messages", "honba-entities", "honba-engine", "honba-strategy", "honba-sim"},
    "honba-cli": {
        "honba-messages", "honba-entities", "honba-market", "honba-engine",
        "honba-analytics", "honba-data", "honba-strategy", "honba-testing", "honba-sim",
        "honba-codegen",
    },
}

# Dev-dependencies allowed per crate (strict layering: no upward dependencies from lower tiers)
ALLOWED_DEV = {
    "honba-messages": set(),
    "honba-entities": set(),
    "honba-market": set(),
    "honba-engine": set(),
    "honba-indicators": set(),
    "honba-sim": set(),
    "honba-strategy": {"honba-analytics", "honba-data", "honba-sim", "honba-testing"},
    "honba-testing": set(),
    "honba-analytics": set(),
    "honba-data": set(),
    "honba-codegen": set(),
    "honba-py": set(),
    "honba-cli": set(),
}

# Core crates that must NEVER enable market-specific packs (like the `india` feature of honba-market)
CORE_CRATES = {
    "honba-messages", "honba-entities", "honba-engine", "honba-indicators",
    "honba-sim", "honba-strategy", "honba-analytics", "honba-data", "honba-testing"
}

def main() -> int:
    root = Path(__file__).resolve().parent.parent / "crates"
    errs = 0
    for d in sorted(root.iterdir()):
        f = d / "Cargo.toml"
        if not f.exists():
            continue
        data = tomllib.loads(f.read_text())
        name = data.get("package", {}).get("name", d.name)

        # 1. Check production dependencies
        prod_deps = {k for k in data.get("dependencies", {}) if k.startswith("honba-")}
        bad_prod = prod_deps - ALLOWED_PROD.get(name, set())
        if bad_prod:
            print(f"VIOLATION: {name} -> {sorted(bad_prod)} (production dependency not allowed)")
            errs += 1

        # 2. Check dev-dependencies (prevent upward edges like honba-sim -> honba-testing)
        dev_deps = {k for k in data.get("dev-dependencies", {}) if k.startswith("honba-")}
        bad_dev = dev_deps - ALLOWED_DEV.get(name, set())
        if bad_dev:
            print(f"VIOLATION: {name} -> {sorted(bad_dev)} (dev-dependency not allowed / upward edge)")
            errs += 1

        # 3. Check pyo3 restriction (pyo3 only in honba-py)
        all_deps = set(data.get("dependencies", {}).keys()) | set(data.get("dev-dependencies", {}).keys())
        if "pyo3" in all_deps and name != "honba-py":
            print(f"VIOLATION: {name} depends on pyo3 (pyo3 is restricted to honba-py only)")
            errs += 1

        # 4. Check that core crates never enable the `india` feature of honba-market directly
        for section_name in ("dependencies", "dev-dependencies"):
            section = data.get(section_name, {})
            if "honba-market" in section and name in CORE_CRATES:
                dep_info = section["honba-market"]
                if isinstance(dep_info, dict):
                    features = dep_info.get("features", [])
                    if "india" in features:
                        print(f"VIOLATION: core crate {name} enables 'india' feature of honba-market")
                        errs += 1

    if errs:
        return 1
    print("Dependency hierarchy and layering rules OK.")
    return 0

if __name__ == "__main__":
    sys.exit(main())
