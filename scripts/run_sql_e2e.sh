#!/usr/bin/env bash
# Run the haybarn-unittest SQL E2E against a local MOCK MediaWiki server.
#
# sqllogictest can't itself launch a sidecar HTTP server, so this wrapper:
#   1. starts the mock server (tests/mock_server.py), reading the chosen PORT,
#   2. bakes VGI_WIKIPEDIA_API_URL (pointed at the mock) into the worker command
#      that haybarn will ATTACH via ${VGI_WIKIPEDIA_WORKER},
#   3. runs the haybarn glob,
#   4. tears the mock down.
#
# Deterministic, no keys, no cost, no real network egress.
set -euo pipefail

cd "$(dirname "$0")/.."

HAYBARN="${HAYBARN:-haybarn-unittest}"
TEST_DIR="${TEST_DIR:-.}"
TEST_PATTERN="${TEST_PATTERN:-test/sql/*}"

# Start the mock server; it prints "PORT:<n>" once bound.
MOCK_OUT="$(mktemp)"
uv run --no-sync python -m tests.mock_server >"$MOCK_OUT" 2>/dev/null &
MOCK_PID=$!
cleanup() { kill "$MOCK_PID" 2>/dev/null || true; rm -f "$MOCK_OUT"; }
trap cleanup EXIT

# Wait for the PORT line (up to ~5s).
PORT=""
for _ in $(seq 1 50); do
  PORT="$(sed -n 's/^PORT:\([0-9]*\)$/\1/p' "$MOCK_OUT" | head -n1)"
  [ -n "$PORT" ] && break
  sleep 0.1
done
if [ -z "$PORT" ]; then
  echo "ERROR: mock server did not report a PORT" >&2
  exit 1
fi
echo "Mock MediaWiki server on 127.0.0.1:${PORT}"

# The worker command haybarn ATTACHes, with the mock URL baked into its env.
# (A fixed URL with no {lang} placeholder targets the single mock wiki.)
export VGI_WIKIPEDIA_WORKER="env VGI_WIKIPEDIA_API_URL=http://127.0.0.1:${PORT}/w/api.php uv run --python 3.13 wikipedia_worker.py"

exec "$HAYBARN" --test-dir "$TEST_DIR" "$TEST_PATTERN"
