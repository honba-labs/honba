.PHONY: build test lint fmt python schema check-schema check-schema-ts

build:
	cargo build --workspace

test:
	cargo test --workspace

lint:
	cargo clippy --all-targets -- -D warnings

fmt:
	cargo fmt --all

python:
	cd python && maturin develop

FRONTEND_TS_DIR ?= ../honba-frontend/src/core/types/generated

schema:
	PYTHONPATH=python python3 scripts/export_schema.py --frontend-dir $(FRONTEND_TS_DIR)

# JSON-only drift check; works in a single-repo checkout (this is what CI runs).
check-schema:
	HONBA_FRONTEND_DIR= PYTHONPATH=python python3 scripts/export_schema.py
	git diff --exit-code schema/domain

# LOCAL ONLY: cross-repo check, writes into the sibling ../honba-frontend checkout.
check-schema-ts:
	PYTHONPATH=python python3 scripts/export_schema.py --frontend-dir $(FRONTEND_TS_DIR)
	git diff --exit-code schema/domain
	git -C ../honba-frontend diff --exit-code src/core/types/generated
