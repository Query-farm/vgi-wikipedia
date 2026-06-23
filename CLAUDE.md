# CLAUDE.md — vgi-wikipedia

Contributor/agent notes. User-facing docs live in `README.md`; this is the
"how it's built and where the sharp edges are" companion.

## What this is

A [VGI](https://query.farm) worker exposing **Wikipedia / MediaWiki full-text
search and page retrieval** to DuckDB/SQL over the FREE official MediaWiki Action
API + REST summary endpoint (no key, no scraping). `wikipedia_worker.py`
assembles every function into one `wiki` catalog (single `main` schema) over
stdio. It is an **egress connector** — queries leave the engine for the
MediaWiki API; the durable value is RAG/knowledge-grounding glue, pairing with
vgi-embed / vgi-rerank.

## Layout

```
wikipedia_worker.py       repo-root stdio entry; PEP 723 inline deps; main()
serve.py                  HTTP entry (forces --http)
vgi_wikipedia/
  client.py               the ONLY networked module: polite MediaWiki HTTP
                          client (required User-Agent, timeout, bounded retry on
                          429/5xx, configurable api_url). Raises WikiError; never
                          crashes the worker.
  parse.py                pure JSON->row mapping (no network): search list +
                          summary; strip_html; sroffset continuation; missing->NULL
  tables.py               wiki_search table function + sroffset SCAN STATE
  tables_page.py          wiki_page_summary table function (rich multi-column row)
  scalars.py              wiki_page scalar (arity overloads, positional-only)
  schema_utils.py         Arrow column-comment helper
tests/                    pytest: parse (fixtures), tables (scan-state round-trip,
                          network mocked), mock-server E2E (real httpx), scalars,
                          live (gated, real Wikipedia)
tests/fixtures/*.json     captured Action-API search + REST summary shapes
tests/mock_server.py      canned-response MediaWiki server (pytest + haybarn)
test/sql/*.test           haybarn-unittest sqllogictest — authoritative E2E
scripts/run_sql_e2e.sh    starts the mock, points the worker at it, runs haybarn
Makefile                  test / test-unit / test-live / test-sql / lint
```

## Scalars vs table functions — core convention (read first)

VGI **scalars are positional-only** (`name := value` is a table-function
feature). So:

- `wiki_page` is two arity overloads: `wiki_page(title)` (English) and
  `wiki_page(title, lang)` — a `Param` (the title column) + a `ConstParam`
  (lang). It returns the extract VARCHAR; NULL on missing/blank, never errors.
- `wiki_search` and `wiki_page_summary` are TABLE functions and take named args
  (`lang :=`, `count :=`, `max_pages :=`, `api_url :=`). Named args route by the
  **dataclass FIELD NAME**, so the field name and `Arg(...)` alias must match.
- STRUCT/LIST/JSON/TIMESTAMPTZ returns require an explicit Arrow schema. We keep
  it simple: `extra` is VARCHAR(JSON) — a plain string column, reached into with
  DuckDB's `->>` (load the `json` extension first).

## The scan state (the load-bearing part)

`wiki_search` pages with MediaWiki's `sroffset`. Each response carries a
`continue.sroffset` token for the next page. `ScanState` (an
`ArrowSerializableDataclass`, plain ints/bool) carries: `cur_sroffset` (the page
we're emitting), `pos` (offset within that page), `pages_done`, `exhausted`.
This round-trips across every `process` tick (and so across batch boundaries).
The fetched page rows live in a process-local `_PAGE_CACHE`, keyed by
`(execution_key, sroffset)`.

### Sharp edges learned the hard way (do NOT regress)

1. **Pin `max_workers=1` (`on_init`).** A source table function is run by
   MULTIPLE parallel scan instances by default; each gets its own fresh `state`
   and would re-emit the WHOLE result, so the output was duplicated ~N×. Pinning
   to a single scan worker makes paging (which is inherently sequential) produce
   the result exactly once. Both `wiki_search` and `wiki_page_summary` set it.

2. **Every non-finishing `process` tick MUST emit a batch.** The framework
   validates `out.validate()` -> `RuntimeError: No data batch was emitted` if a
   tick neither emits nor finishes. So `process` LOOPS internally across
   page-advance / empty pages until it either emits a chunk or calls
   `out.finish()` — it never returns empty-handed without finishing.

3. **Cache by `(key, sroffset)`, not a shared growing list.** An earlier design
   `extend`-ed one buffer per execution id; parallel scans corrupted it. Keying
   each page by its offset makes re-fetch idempotent.

4. **`require vgi` SKIPS under haybarn-unittest.** The `.test` files use an
   explicit `LOAD vgi;` instead. They also `INSTALL json; LOAD json;` because the
   `extra->>'key'` extraction needs the json extension (it isn't autoloaded in
   the haybarn build).

## Network discipline

All HTTP is in `client.py`: required descriptive `User-Agent`
(`VGI_WIKIPEDIA_USER_AGENT` to override), per-call timeout, bounded retry with
backoff on 429/5xx, and every failure surfaced as `WikiError` (a clean DuckDB
error) — the worker process never crashes. `api_url` is injectable
(`VGI_WIKIPEDIA_API_URL`, may contain `{lang}`) so tests point it at the mock and
so the worker can target any MediaWiki wiki.

## Testing — the CI gate never touches live Wikipedia

- **`make test-unit`** (`pytest -m "not live"`): fixture parser tests, the
  in-process scan-state round-trip (`harness.run_table_function(...,
  serialize_state=True)`), the mock-server E2E (real httpx vs canned responses),
  and the scalar tests. Network is mocked at the `WikiClient` boundary or the
  mock HTTP server.
- **`make test-sql`**: `scripts/run_sql_e2e.sh` starts the mock MediaWiki server,
  bakes `VGI_WIKIPEDIA_API_URL` (pointed at it) into the worker command, and runs
  the worker as a real DuckDB subprocess under haybarn-unittest. Asserts the
  columns, the `sroffset` scan-state round-trip across a batch/page boundary
  (`max_pages := 2` reaches a page-2-only title), plain-text snippets, and a
  clean missing-page path.
- **`make test-live`** (gated, `-m live`): real `en.wikipedia.org` — free, no
  key, NOT in CI. Loose assertions (content drifts).

## Licensing — DO NOT regress this decision

- **Worker code: MIT** (`LICENSE`, this repo).
- **Retrieved CONTENT is CC-BY-SA** (Wikipedia/Wikimedia text). The README leads
  with a prominent warning: attribution & share-alike are the **user's**
  responsibility; the worker only fetches content and grants no rights to it. The
  `url` column exists so users can link back / attribute. Keep that warning
  accurate on any change.
- No API key is ever needed or accepted (the MediaWiki API is free/open).

## Deferred / non-goals

- `api_url :=` lets `wiki_search` (and `wiki_page_summary`) target arbitrary
  MediaWiki wikis — implemented, not just Wikipedia. The REST-summary derivation
  is best-effort for non-Wikimedia bases and falls back to the Action-API
  `prop=extracts` shape.
- No full article wikitext/HTML body, no category/link graph, no edit/write
  paths, no caching layer.
```
