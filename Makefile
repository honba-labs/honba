.PHONY: build test lint fmt python

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
