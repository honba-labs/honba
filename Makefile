.PHONY: build test lint fmt python schema check-schema check-schema-ts openapi check-openapi \
	pyi check-pyi mcp check-mcp codegen check-codegen check-codegen-ci

build:
	cargo build --workspace

test:
	cargo test --workspace

lint:
	cargo clippy --workspace --all-targets -- -D warnings

fmt:
	cargo fmt --all

python:
	cd python && maturin develop

FRONTEND_TS_DIR ?= ../honba-frontend/src/core/types/generated

# Rust is the codegen source of truth (honba-codegen). Every surface derives from it:
# schema/domain, schema/openapi, schema/mcp, python/src/honba/wire/generated and the frontend
# TypeScript. `scripts/export_schema.py` and the Python `honba schema export` are thin wrappers.

HONBA := cargo run --quiet --bin honba --

# Drift check for one generated dir: any modified, deleted or untracked (new) file fails.
# `git diff --exit-code` alone misses a newly generated file nobody committed.
# Usage: $(call no_drift,<git -C dir>,<pathspec>)
define no_drift
	@out="$$(git -C $(1) status --porcelain --untracked-files=all -- $(2))"; \
	if [ -n "$$out" ]; then \
		echo "codegen drift in $(1)/$(2) (regenerate and commit):"; echo "$$out"; \
		git -C $(1) --no-pager diff --stat -- $(2); exit 1; \
	fi
endef

schema:
	$(HONBA) schema export --typescript $(FRONTEND_TS_DIR)

check-schema:
	$(HONBA) schema export
	$(call no_drift,.,schema/domain)

# LOCAL ONLY: writes into the sibling ../honba-frontend checkout.
check-schema-ts:
	$(HONBA) schema export --typescript $(FRONTEND_TS_DIR)
	$(call no_drift,.,schema/domain)
	$(call no_drift,$(FRONTEND_TS_DIR),.)

openapi:
	$(HONBA) schema export --openapi schema/openapi

check-openapi:
	$(HONBA) schema export --openapi schema/openapi
	$(call no_drift,.,schema/openapi)

pyi:
	$(HONBA) schema export --pyi python/src/honba/wire/generated

check-pyi:
	$(HONBA) schema export --pyi python/src/honba/wire/generated
	$(call no_drift,.,python/src/honba/wire/generated)

mcp:
	$(HONBA) schema export --mcp schema/mcp

check-mcp:
	$(HONBA) schema export --mcp schema/mcp
	$(call no_drift,.,schema/mcp)

# Regenerate everything this repo commits, plus the frontend TypeScript.
codegen: schema openapi pyi mcp

# Every drift check, including the cross-repo TypeScript one (local only).
check-codegen: check-schema check-schema-ts check-openapi check-pyi check-mcp
	@echo "all codegen drift checks passed"

# Every drift check that needs only this repo (what CI runs).
check-codegen-ci: check-schema check-openapi check-pyi check-mcp
	@echo "codegen drift checks passed (TypeScript: run make check-schema-ts locally)"
