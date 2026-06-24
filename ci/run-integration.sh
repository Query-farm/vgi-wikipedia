#!/usr/bin/env bash
# Copyright 2026 Query Farm LLC - https://query.farm
#
# Run this repo's sqllogictest suite (test/sql/*.test) against the vgi-wikipedia
# VGI worker, using a prebuilt standalone `haybarn-unittest` and the signed
# community `vgi` extension — no C++ build from source. See ci/README.md.
#
# sqllogictest cannot launch a sidecar, so this wrapper starts a local MOCK
# MediaWiki server (tests/mock_server.py) — deterministic, no keys, no live
# egress — and EXPORTS VGI_WIKIPEDIA_API_URL (pointed at it) into the
# environment, so BOTH a DuckDB-spawned subprocess worker AND an out-of-band
# http/unix worker inherit it. The mock server stays up for ALL THREE transports.
#
# The SAME suite is exercised over three VGI transports, selected by $TRANSPORT.
# The vgi extension picks the transport from the LOCATION string the .test files
# ATTACH (`${VGI_WIKIPEDIA_WORKER}`):
#
#   subprocess : a bare stdio command (`wikipedia_worker.py`) — the extension
#                spawns the worker per query and talks Arrow IPC over
#                stdin/stdout. Default; current behavior.
#   http       : the worker is started out-of-band in `--http` mode on an auto
#                port; LOCATION becomes `http://127.0.0.1:<port>`.
#   unix       : the worker is started out-of-band on an AF_UNIX socket;
#                LOCATION becomes `unix:///path/to.sock`.
#
# Required environment:
#   HAYBARN_UNITTEST     path to the haybarn-unittest binary
#   TRANSPORT            subprocess | http | unix (default: subprocess)
#   WORKER_CMD           the stdio command that runs the worker. Used directly
#                        as the LOCATION for subprocess, and as the process to
#                        boot the server for http/unix. Defaults to
#                        `uv run --python 3.13 <repo>/wikipedia_worker.py`.
# Optional:
#   STAGE                scratch dir for the preprocessed test tree (default: mktemp)
set -euo pipefail

: "${HAYBARN_UNITTEST:?path to the haybarn-unittest binary}"

HERE="$(cd "$(dirname "$0")" && pwd)"
REPO="$(cd "$HERE/.." && pwd)"
STAGE="${STAGE:-$(mktemp -d)}"
TRANSPORT="${TRANSPORT:-subprocess}"
WORKER_CMD="${WORKER_CMD:-uv run --python 3.13 $REPO/wikipedia_worker.py}"

echo "Staging preprocessed tests into $STAGE ..."
mkdir -p "$STAGE/test/sql"
for f in "$REPO"/test/sql/*.test; do
  awk -f "$HERE/preprocess-require.awk" "$f" > "$STAGE/test/sql/$(basename "$f")"
done

# ---------------------------------------------------------------------------
# Start the mock MediaWiki server for ALL transports (the worker still needs a
# backend regardless of how DuckDB reaches it). It prints `PORT:<n>` once bound
# and stays up until killed.
# ---------------------------------------------------------------------------
MOCK_OUT="$(mktemp)"
( cd "$REPO" && uv run --no-sync python -m tests.mock_server >"$MOCK_OUT" 2>/dev/null ) &
MOCK_PID=$!

SERVER_PID=""
SOCK=""
PORT_FILE=""

cleanup() {
  # Capture the real exit status FIRST: an EXIT trap whose last command returns
  # non-zero (e.g. a short-circuited `[[ -n "" ]] && …` on legs where nothing
  # needs cleaning) would otherwise become the script's exit status under
  # `set -e` and fail an already-passing run.
  local rc=$?
  if [[ -n "$SERVER_PID" ]]; then
    kill "$SERVER_PID" 2>/dev/null || true
    wait "$SERVER_PID" 2>/dev/null || true
  fi
  if [[ -n "$MOCK_PID" ]]; then
    kill "$MOCK_PID" 2>/dev/null || true
    wait "$MOCK_PID" 2>/dev/null || true
  fi
  if [[ -n "$SOCK" ]]; then rm -f "$SOCK"; fi
  if [[ -n "$PORT_FILE" ]]; then rm -f "$PORT_FILE"; fi
  rm -f "$MOCK_OUT"
  return "$rc"
}
trap cleanup EXIT

MOCK_PORT=""
for _ in $(seq 1 100); do
  MOCK_PORT="$(sed -n 's/^PORT:\([0-9]*\)$/\1/p' "$MOCK_OUT" | head -n1)"
  [ -n "$MOCK_PORT" ] && break
  sleep 0.1
done
if [ -z "$MOCK_PORT" ]; then
  echo "ERROR: mock MediaWiki server did not report a PORT" >&2
  exit 1
fi
echo "Mock MediaWiki server on 127.0.0.1:${MOCK_PORT}"

# EXPORT the mock api.php URL into the environment (NOT as an `env VAR=… <cmd>`
# command prefix). This way it is inherited by BOTH the DuckDB-spawned
# subprocess worker AND the out-of-band http/unix worker we boot below — the
# command-prefix trick only worked when DuckDB spawned the command.
export VGI_WIKIPEDIA_API_URL="http://127.0.0.1:${MOCK_PORT}/w/api.php"

# ---------------------------------------------------------------------------
# Per-transport: resolve VGI_WIKIPEDIA_WORKER (the LOCATION) and, for the
# out-of-band transports, boot the worker server + arrange trap-cleanup.
# ---------------------------------------------------------------------------
case "$TRANSPORT" in
  subprocess)
    export VGI_WIKIPEDIA_WORKER="$WORKER_CMD"
    ;;

  http)
    # The vgi extension's HTTP transport is implemented on top of DuckDB's
    # httpfs extension, so an `http://` ATTACH binds with
    #   "Binder Error: VGI HTTP transport requires the httpfs extension."
    # unless httpfs is loaded first. (The haybarn sqllogictest runner's default
    # skip list swallows any error containing "HTTP", so without this the whole
    # suite would silently SKIP rather than fail — a fake pass.) The .test files
    # are transport-agnostic; inject a signed `INSTALL httpfs FROM core; LOAD
    # httpfs;` right after the awk-injected `LOAD vgi;` in each staged file, so
    # httpfs is present only when we actually run over HTTP.
    echo "Injecting httpfs load into staged tests (HTTP transport needs it) ..."
    for sf in "$STAGE"/test/sql/*.test; do
      awk '
        { print }
        /^LOAD[ \t]+vgi[ \t]*;[ \t]*$/ && !done {
          print "";
          print "statement ok";
          print "INSTALL httpfs FROM core;";
          print "";
          print "statement ok";
          print "LOAD httpfs;";
          done = 1
        }
      ' "$sf" > "$sf.tmp" && mv "$sf.tmp" "$sf"
    done

    # Boot the worker in HTTP mode on an auto-selected port. The worker writes
    # the chosen port to --port-file atomically (tmp + rename), so we watch for
    # the file to appear rather than parsing stdout. HTTP mode needs the `http`
    # extra (waitress); WORKER_CMD must resolve it — CI runs
    # `uv sync --frozen --extra http` and the PEP 723 header lists the extra.
    PORT_FILE="$(mktemp -u "${TMPDIR:-/tmp}/wiki-port.XXXXXX")"
    LOG_FILE="${TMPDIR:-/tmp}/wiki-http-server.log"
    echo "Starting HTTP worker: $WORKER_CMD --http --port 0 --port-file $PORT_FILE"
    # cwd = repo root so the worker resolves staged relative-path fixtures.
    # shellcheck disable=SC2086
    ( cd "$REPO" && exec $WORKER_CMD --http --port 0 --port-file "$PORT_FILE" ) > "$LOG_FILE" 2>&1 &
    SERVER_PID=$!

    PORT=""
    for _ in $(seq 1 240); do
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "ERROR: HTTP worker exited before reporting a port. Log:" >&2
        cat "$LOG_FILE" >&2
        exit 1
      fi
      if [[ -s "$PORT_FILE" ]]; then
        PORT="$(tr -d '[:space:]' < "$PORT_FILE")"
        [[ -n "$PORT" ]] && break
      fi
      sleep 0.5
    done
    if [[ -z "$PORT" ]]; then
      echo "ERROR: timed out waiting for HTTP worker port-file. Log:" >&2
      cat "$LOG_FILE" >&2
      exit 1
    fi
    echo "HTTP worker ready on port $PORT (pid $SERVER_PID)"
    export VGI_WIKIPEDIA_WORKER="http://127.0.0.1:$PORT"
    ;;

  unix)
    # Boot the worker bound to an AF_UNIX socket. The worker prints
    # `UNIX:<abs-path>` once bound; we poll for the socket file to appear.
    SOCK="${TMPDIR:-/tmp}/wiki-$$.sock"
    rm -f "$SOCK"
    LOG_FILE="${TMPDIR:-/tmp}/wiki-unix-server.log"
    echo "Starting unix worker: $WORKER_CMD --unix $SOCK"
    # cwd = repo root so the worker resolves staged relative-path fixtures.
    # shellcheck disable=SC2086
    ( cd "$REPO" && exec $WORKER_CMD --unix "$SOCK" ) > "$LOG_FILE" 2>&1 &
    SERVER_PID=$!

    READY=""
    for _ in $(seq 1 240); do
      if ! kill -0 "$SERVER_PID" 2>/dev/null; then
        echo "ERROR: unix worker exited before binding the socket. Log:" >&2
        cat "$LOG_FILE" >&2
        exit 1
      fi
      if [[ -S "$SOCK" ]]; then
        READY=1
        break
      fi
      sleep 0.5
    done
    if [[ -z "$READY" ]]; then
      echo "ERROR: timed out waiting for unix worker socket. Log:" >&2
      cat "$LOG_FILE" >&2
      exit 1
    fi
    echo "unix worker ready on $SOCK (pid $SERVER_PID)"
    export VGI_WIKIPEDIA_WORKER="unix://$SOCK"
    ;;

  *)
    echo "ERROR: unknown TRANSPORT '$TRANSPORT' (want subprocess|http|unix)" >&2
    exit 2
    ;;
esac

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

# Run the whole suite in one invocation, capturing the runner's native
# sqllogictest report so we can both stream it AND assert on the summary line.
echo "Running suite (transport: $TRANSPORT, worker: $VGI_WIKIPEDIA_WORKER) ..."
RUN_LOG="$STAGE/run.log"
set +e
"$HAYBARN_UNITTEST" "test/sql/*" 2>&1 | tee "$RUN_LOG"
status=${PIPESTATUS[0]}
set -e

# SILENT-SKIP GUARD (critical for the http leg). DuckDB's sqllogictest runner
# auto-SKIPS (exit 0!) any test whose error message contains "HTTP" or "Unable
# to connect" — so a broken http setup reports "All tests were skipped" and the
# job goes GREEN while testing nothing. Fail the leg unless the runner reports a
# real pass with N>0 assertions and reported zero skips.
if [[ $status -ne 0 ]]; then
  echo "ERROR: haybarn-unittest exited $status" >&2
  exit "$status"
fi
if grep -Eqi 'were skipped|tests were skipped' "$RUN_LOG"; then
  echo "ERROR: tests were SKIPPED (likely a masked $TRANSPORT transport error — see above)." >&2
  exit 1
fi
if ! grep -Eq 'All tests passed \([1-9][0-9]* assertions' "$RUN_LOG"; then
  echo "ERROR: did not find an 'All tests passed (N assertions ...)' summary with N>0." >&2
  exit 1
fi
echo "Suite GREEN over transport: $TRANSPORT"
