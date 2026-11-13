#!/usr/bin/env bash
# Use the same mock-backed transport harness locally and in CI.
set -euo pipefail
cd "$(dirname "$0")/.."
export HAYBARN_UNITTEST="${HAYBARN_UNITTEST:-${HAYBARN:-$PWD/.tools/bin/haybarn-unittest}}"
export WORKER_CMD="${WORKER_CMD:-$PWD/.venv/bin/python $PWD/wikipedia_worker.py}"
exec bash ci/run-integration.sh
