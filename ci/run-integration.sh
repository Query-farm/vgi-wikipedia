#!/usr/bin/env bash
# Copyright 2026 Query Farm LLC - https://query.farm
#
# Run this repo's sqllogictest suite (test/sql/*.test) against the vgi-wikipedia
# VGI worker, using a prebuilt standalone `haybarn-unittest` and the signed
# community `vgi` extension — no C++ build from source. See ci/README.md.
#
# sqllogictest cannot launch a sidecar, so this wrapper starts a local MOCK
# MediaWiki server (tests/mock_server.py) and bakes VGI_WIKIPEDIA_API_URL
# (pointed at it) into the worker command haybarn ATTACHes — deterministic, no
# keys, no live egress.
#
# Required environment:
#   HAYBARN_UNITTEST      path to the haybarn-unittest binary
#   VGI_WIKIPEDIA_WORKER  base worker LOCATION (a stdio command); the mock URL is
#                         prepended as `env VGI_WIKIPEDIA_API_URL=... <command>`
# Optional:
#   STAGE                 scratch dir for the preprocessed test tree (default: mktemp)
set -euo pipefail

: "${HAYBARN_UNITTEST:?path to the haybarn-unittest binary}"
: "${VGI_WIKIPEDIA_WORKER:?base worker LOCATION (stdio command)}"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
STAGE="${STAGE:-$(mktemp -d)}"

echo "Staging preprocessed tests into $STAGE ..."
mkdir -p "$STAGE/test/sql"
for f in "$REPO"/test/sql/*.test; do
  awk -f "$HERE/preprocess-require.awk" "$f" > "$STAGE/test/sql/$(basename "$f")"
done

# Start the mock MediaWiki server (from the repo root so `tests.mock_server`
# imports). It prints `PORT:<n>` once bound and stays up until killed.
MOCK_OUT="$(mktemp)"
( cd "$REPO" && uv run --no-sync python -m tests.mock_server >"$MOCK_OUT" 2>/dev/null ) &
MOCK_PID=$!
cleanup() { kill "$MOCK_PID" 2>/dev/null || true; rm -f "$MOCK_OUT"; }
trap cleanup EXIT

PORT=""
for _ in $(seq 1 100); do
  PORT="$(sed -n 's/^PORT:\([0-9]*\)$/\1/p' "$MOCK_OUT" | head -n1)"
  [ -n "$PORT" ] && break
  sleep 0.1
done
if [ -z "$PORT" ]; then
  echo "ERROR: mock MediaWiki server did not report a PORT" >&2
  exit 1
fi
echo "Mock MediaWiki server on 127.0.0.1:${PORT}"

# Bake the mock api.php URL into the worker command haybarn ATTACHes.
export VGI_WIKIPEDIA_WORKER="env VGI_WIKIPEDIA_API_URL=http://127.0.0.1:${PORT}/w/api.php ${VGI_WIKIPEDIA_WORKER}"

cd "$STAGE"

# Warm the extension cache once: vgi from the signed community channel. A miss
# here is only a warning — the per-test INSTALL/LOAD (injected by
# preprocess-require.awk) is what actually gates each file.
echo "Warming the extension cache (vgi from community) ..."
mkdir -p "$STAGE/test"
cat > "$STAGE/test/_warm.test" <<'EOF'
# name: test/_warm.test
# group: [warm]
statement ok
INSTALL vgi FROM community;
EOF
"$HAYBARN_UNITTEST" "test/_warm.test" >/dev/null 2>&1 || echo "::warning::extension warm step did not fully succeed"
rm -f "$STAGE/test/_warm.test"

# Run the whole suite in one invocation, streaming the runner's native
# sqllogictest report. Any failed assertion exits non-zero and fails the job.
echo "Running suite (worker: $VGI_WIKIPEDIA_WORKER) ..."
"$HAYBARN_UNITTEST" "test/sql/*"
