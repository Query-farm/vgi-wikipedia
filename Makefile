# vgi-wikipedia — dev and test targets. `make help` lists them.
#
#   make test        # unit/fixture tests + mock-server E2E + SQL (haybarn) E2E
#   make test-unit   # pytest: fixture parsers, scan-state round-trip, mock-server E2E
#   make test-live   # OPTIONAL gated smoke against the REAL Wikipedia API (needs network)
#   make test-sql    # test/sql/*.test through DuckDB over stdio, launch and http
#   make lint        # ruff + mypy
#
# ci/run-integration.sh points the worker at a local mock MediaWiki/Wikidata
# server (tests/mock_server.py). Deterministic, no keys, no cost, no real
# network egress — the CI gate never depends on live Wikipedia.

HAYBARN_VERSION  ?= 2.0.0rc11
WORKER           := $(CURDIR)/.venv/bin/vgi-wikipedia-worker
TOOLS            := $(CURDIR)/.tools
HAYBARN_UNITTEST := $(TOOLS)/bin/haybarn-unittest
TRANSPORTS       ?= stdio launch http

build: ## Install the worker (all extras: HTTP serving needs `http`) into .venv
	uv sync --locked --all-extras

tools: ## Install the pinned SQL test runner into .tools
	@test -x $(HAYBARN_UNITTEST) || { uv venv -q $(TOOLS) && uv pip install -q -p $(TOOLS) haybarn-unittest==$(HAYBARN_VERSION); }

test-sql: build tools ## SQL tests through DuckDB over each transport: make test-sql TRANSPORTS=launch
	@set -e; for t in $(TRANSPORTS); do \
	  WORKER=$(WORKER) HAYBARN_UNITTEST=$(HAYBARN_UNITTEST) TRANSPORT=$$t ci/run-integration.sh; \
	done

test-sql-all: test-sql ## Alias: SQL tests over every transport

.DEFAULT_GOAL := help
.PHONY: help build tools check test test-unit test-live test-sql test-sql-all lint typecheck metadata demo

help: ## List the targets
	@awk 'BEGIN{FS=":.*## "} /^[a-zA-Z_-]+:.*## /{printf "  %-14s %s\n",$$1,$$2}' $(MAKEFILE_LIST)

check: lint test-unit test-sql metadata demo ## Everything CI runs

test: test-unit test-sql ## Unit + SQL tests

# Full unit suite: fixture parser tests, in-process scan-state round-trip, and
# the mock-server E2E. The live smoke is gated by the `live` marker and excluded.
test-unit: build ## Unit tests (pytest, no live network)
	uv run --no-sync pytest -q -m "not live"

# Optional live smoke: hits the real Wikipedia API (free, no key). Needs network;
# NOT part of the CI gate.
test-live: ## Live smoke against the real Wikipedia API
	uv run --no-sync pytest -q -m live

metadata: ## vgi-lint against the local API fixtures
	uv run --no-sync python scripts/check_metadata.py

demo: ## Run the Wikidata demo
	uv run --no-sync python examples/wikidata_demo.py

lint: ## ruff + mypy + pydoclint
	uv run --no-sync ruff format --check .
	uv run --no-sync ruff check .
	uv run --no-sync mypy vgi_wikipedia/
	uvx pydoclint==0.12.0 --config pyproject.toml vgi_wikipedia/ wikipedia_worker.py serve.py

typecheck: ## mypy
	uv run --no-sync mypy vgi_wikipedia/
