.PHONY: build test lint fmt python schema check-schema

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

schema:
	PYTHONPATH=python python3 scripts/export_schema.py

check-schema:
	PYTHONPATH=python python3 scripts/export_schema.py
	git diff --exit-code schema/domain
	git -C ../honba-frontend diff --exit-code src/core/types/generated
