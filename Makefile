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

# Rust is the codegen source of truth (honba-codegen). Every surface derives from it.
# `scripts/export_schema.py` is a thin wrapper around `cargo run --bin honba`.

schema:
	cargo run --bin honba -- schema export --typescript $(FRONTEND_TS_DIR)

check-schema:
	cargo run --bin honba -- schema export
	git diff --exit-code schema/domain

check-schema-ts:
	cargo run --bin honba -- schema export --typescript $(FRONTEND_TS_DIR)
	git diff --exit-code schema/domain
	git -C ../honba-frontend diff --exit-code src/core/types/generated

openapi:
	cargo run --bin honba -- schema export --openapi schema/openapi

check-openapi:
	cargo run --bin honba -- schema export --openapi schema/openapi
	git diff --exit-code schema/openapi

pyi:
	cargo run --bin honba -- schema export --pyi python/src/honba/wire/generated

check-pyi:
	cargo run --bin honba -- schema export --pyi python/src/honba/wire/generated
	git diff --exit-code python/src/honba/wire/generated

mcp:
	cargo run --bin honba -- schema export --mcp schema/mcp

check-mcp:
	cargo run --bin honba -- schema export --mcp schema/mcp
	git diff --exit-code schema/mcp

check-codegen: check-schema check-schema-ts check-openapi check-pyi check-mcp
	@echo "all codegen drift checks passed"
