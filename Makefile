# vgi-wikipedia — dev and test targets.
#
# Usage:
#   make test        # unit/fixture tests + mock-server E2E + SQL (haybarn) E2E
#   make test-unit   # pytest: fixture parsers, scan-state round-trip, mock-server E2E
#   make test-live   # OPTIONAL gated smoke against the REAL Wikipedia API (needs network)
#   make test-sql    # DuckDB sqllogictest E2E via haybarn-unittest, mock-server-driven
#   make lint        # ruff + mypy
#
# The SQL E2E drives the *real* worker as a DuckDB subprocess through the
# haybarn-unittest sqllogictest runner, with the worker's VGI_WIKIPEDIA_API_URL
# pointed at a local mock HTTP server (started by scripts/run_sql_e2e.sh).
# Deterministic, no keys, no cost, no real network egress — the CI gate never
# depends on live Wikipedia.

# haybarn-unittest is a uv tool; ~/.local/bin must be on PATH to find it.
HAYBARN ?= haybarn-unittest
LOCAL_BIN := $(HOME)/.local/bin

.PHONY: test test-unit test-live test-sql lint typecheck ensure-haybarn

test: test-unit test-sql

# Full unit suite: fixture parser tests, in-process scan-state round-trip, and
# the mock-server E2E. The live smoke is gated by the `live` marker and excluded.
test-unit:
	uv run --no-sync pytest -q -m "not live"

# Optional live smoke: hits the real Wikipedia API (free, no key). Needs network;
# NOT part of the CI gate.
test-live:
	uv run --no-sync pytest -q -m live

# Install the haybarn-unittest sqllogictest runner if it isn't already present.
ensure-haybarn:
	@if ! PATH="$(LOCAL_BIN):$$PATH" command -v $(HAYBARN) >/dev/null 2>&1; then \
		echo "Installing haybarn-unittest..."; \
		uv tool install haybarn-unittest; \
	fi

# End-to-end SQL tests: start the mock server, ATTACH the worker against it, run
# the .test glob. CRITICAL: under haybarn-unittest, `require vgi` SKIPS — the
# .test files use an explicit `LOAD vgi;` instead.
test-sql: ensure-haybarn
	PATH="$(LOCAL_BIN):$$PATH" bash scripts/run_sql_e2e.sh

lint:
	uv run --no-sync ruff check .
	uv run --no-sync mypy vgi_wikipedia/

typecheck:
	uv run --no-sync mypy vgi_wikipedia/
