# CI: the vgi-wikipedia worker integration suite

[`.github/workflows/ci.yml`](../.github/workflows/ci.yml) runs the unit tests
and this repo's sqllogictest suite (`test/sql/*.test`) against the vgi-wikipedia
VGI worker through the **real DuckDB `vgi` extension** on every push / PR.

## How it works (no C++ build)

Rather than building the vgi DuckDB extension from source, CI drives a
**prebuilt** standalone `haybarn-unittest` (the DuckDB/Haybarn sqllogictest
runner, published in Haybarn's releases) and installs the **signed** `vgi`
extension from the Haybarn community channel:

1. **Install the worker** — `uv sync --frozen --extra http` into a venv.
   `wikipedia_worker.py` is a self-contained PEP 723 stdio worker the extension
   can spawn via `uv run wikipedia_worker.py`.
2. **Download the runner** — the matching `haybarn_unittest-*` asset per platform.
3. **Preprocess** — [`preprocess-require.awk`](preprocess-require.awk) rewrites
   each `require <ext>` into an explicit signed `INSTALL <ext> FROM
   {community,core}; LOAD <ext>;`, and injects `INSTALL vgi FROM community;`
   before each bare `LOAD vgi;` (haybarn silently SKIPs `require vgi`).
   `require-env` and everything else pass through.
4. **Run** — [`run-integration.sh`](run-integration.sh) stages the preprocessed
   tree, starts the mock MediaWiki server, resolves `VGI_WIKIPEDIA_WORKER` (the
   ATTACH `LOCATION`) per `$TRANSPORT`, warms the extension cache once, then runs
   the suite in a single `haybarn-unittest` invocation. The silent-skip guard
   (below) fails the leg unless the runner reports a real pass.

## The mock MediaWiki server (all transports)

sqllogictest cannot launch a sidecar, so `run-integration.sh` starts the mock
MediaWiki server (`tests/mock_server.py`, run as `python -m tests.mock_server`),
which serves the captured fixture JSON at the paths the worker's `WikiClient`
hits — deterministic, no keys, no live network egress. It prints `PORT:<n>` once
bound and **stays up for ALL THREE transports** (the worker still needs a backend
no matter how DuckDB reaches it).

The worker is pointed at the mock by **exporting** the api.php URL into the
environment:

```sh
export VGI_WIKIPEDIA_API_URL="http://127.0.0.1:<mock-port>/w/api.php"
```

This is the key transport-portable detail: the env var is **exported, not baked
in as an `env VAR=… <cmd>` command prefix**. A command prefix only works when
DuckDB spawns the worker (the subprocess leg). For http/unix the harness boots
the worker out-of-band, so it cannot prefix the command — exporting the var
instead lets BOTH the DuckDB-spawned subprocess worker AND the out-of-band
http/unix worker inherit it.

## Transport matrix (subprocess | http | unix)

The same `test/sql/*.test` suite is run over all three VGI transports — the
extension picks the transport from the `LOCATION` string the `.test` files
`ATTACH`, and `run-integration.sh` builds that string from `$TRANSPORT`:

| `TRANSPORT`  | `VGI_WIKIPEDIA_WORKER` (LOCATION)        | How the worker is reached |
|--------------|-------------------------------------------|---------------------------|
| `subprocess` | `.venv/bin/python wikipedia_worker.py`    | extension spawns the worker per query; Arrow IPC over stdin/stdout (default) |
| `http`       | `http://127.0.0.1:<port>`                 | harness boots `wikipedia_worker.py --http --port 0 --port-file <f>`, waits for the port-file, then ATTACHes that URL |
| `unix`       | `unix:///tmp/wiki-<pid>.sock`             | harness boots `wikipedia_worker.py --unix <sock>`, waits for the socket, then ATTACHes it |

The CI `integration` job is a `transport: [subprocess, http, unix]` × `os`
matrix; each leg runs `ci/run-integration.sh` with `TRANSPORT=<t>`. Run a single
transport locally with e.g. `TRANSPORT=http ci/run-integration.sh`.

### Port / readiness discovery

- **http**: the worker writes its auto-selected port to `--port-file` atomically
  (tmp + rename), so the harness watches for that file (not stdout). Boot line:
  `wikipedia_worker.py --http --port 0 --port-file <f>`.
- **unix**: the worker binds the socket and prints `UNIX:<abs-path>`; the harness
  polls for the socket file (`test -S`). Boot line:
  `wikipedia_worker.py --unix <sock>`.

Both out-of-band worker processes run with cwd = the repo root (so they resolve
`tests.mock_server` / staged fixtures) and are trap-killed on exit, alongside the
mock server.

### HTTP transport needs the `httpfs` extension (resolved, not gated)

The vgi extension implements HTTP transport on top of DuckDB's **httpfs**
extension, so an `http://` ATTACH binds with `VGI HTTP transport requires the
httpfs extension` unless httpfs is loaded first. This is a **dependency**, not a
protocol limitation, so we resolve it: the http leg injects a signed `INSTALL
httpfs FROM core; LOAD httpfs;` into each staged `.test` (after the awk-injected
`LOAD vgi;`). The leg also needs the worker's `http` extra (waitress) —
`pyproject.toml` ships an `http` extra (`vgi-python[http]`), the
`wikipedia_worker.py` PEP 723 header lists `vgi-python[http]`, and CI runs
`uv sync --frozen --extra http`.

> **Sharp edge — the runner silently SKIPs HTTP errors.** The haybarn/DuckDB
> sqllogictest runner's default skip list skips any statement whose error
> contains `"HTTP"` or `"Unable to connect"`, so a broken http setup reports
> "All tests were skipped" — a green-looking **fake pass**.
> `run-integration.sh` fails the leg unless the runner reports `All tests passed
> (N assertions …)` with N > 0 and zero skips.

### `wiki_search` `sroffset` paging over HTTP (externalized cursor — no gate)

`wiki_search` is a streaming/paging table function: it follows MediaWiki's
`sroffset` continuation and streams each page to DuckDB in `CHUNK_ROWS`-sized
batches across multiple `process` ticks. Streaming table functions run fine over
the **stateless** HTTP transport **because the cursor is externalized**: the
per-scan position lives in a plain-serializable `ScanState`
(`ArrowSerializableDataclass` — `cur_sroffset` / `pos` / `pages_done` /
`exhausted`) that the framework round-trips through its continuation token on
every tick; the fetched page rows themselves live in a process-local
`_PAGE_CACHE` keyed by `(execution key, sroffset)` and are re-fetched
deterministically if evicted. So the http leg runs the **full** suite including
`wiki_search.test`'s `max_pages := 2` batch/page-boundary round-trip — nothing
is gated. (Same "externalize the scan position into the serialized state"
pattern as the vgi-cve cursor fix; no worker change was needed here — the cursor
was already externalized.)

### Per-transport status

- **subprocess**: GREEN — 51 assertions.
- **http**: GREEN — 55 assertions (51 + the injected httpfs INSTALL/LOAD across
  the two `.test` files). Full suite incl. `wiki_search.test` `sroffset` paging.
- **unix**: GREEN — 51 assertions.

## Run it locally

```bash
uv sync --python 3.13 --extra http
HAYBARN_UNITTEST=/path/to/haybarn-unittest \
WORKER_CMD="uv run --python 3.13 wikipedia_worker.py" \
  TRANSPORT=subprocess ci/run-integration.sh    # or TRANSPORT=http / TRANSPORT=unix
```

`TRANSPORT` defaults to `subprocess`, and `WORKER_CMD` defaults to
`uv run --python 3.13 <repo>/wikipedia_worker.py`.
