<p align="center">
  <img src="https://query.farm/media-kit/products/vgi/vgi-logo.png" alt="Vector Gateway Interface (VGI)" width="320">
</p>

<p align="center"><em>A <a href="https://query.farm">Query.Farm</a> VGI worker for DuckDB.</em></p>

# Wikipedia Articles and Wikidata Graph Queries in DuckDB

> **vgi-wikipedia** · a [Query.Farm](https://query.farm) VGI worker

A [VGI](https://github.com/query-farm/vgi-python) worker that brings
**Wikipedia articles and Wikidata structured knowledge** into DuckDB/SQL.
Search articles, resolve their entity IDs, inspect statements with qualifiers and
references, and run read-only SPARQL graph queries over the **free official APIs**. No API key, no scraping. Great for RAG / knowledge
grounding; pairs naturally with [`vgi-embed`](https://github.com/Query-farm/vgi-embed)
and `vgi-rerank`.

```sql
INSTALL vgi FROM query_farm; LOAD vgi FROM query_farm;
ATTACH 'wiki' (TYPE vgi, LOCATION '/absolute/path/to/vgi-wikipedia/.venv/bin/vgi-wikipedia-worker');

-- Full-text search (table function; named args)
SELECT title, snippet, url
FROM wiki.wiki_search('apache arrow', lang := 'en', count := 10);

-- A page's summary extract (scalar; positional only)
SELECT wiki.wiki_page('DuckDB');             -- English
SELECT wiki.wiki_page('DuckDB', 'de');       -- German Wikipedia

-- The rich, multi-column page summary (table function)
SELECT title, extract, url, thumbnail_url, pageid
FROM wiki.wiki_page_summary('DuckDB', lang := 'en');

-- Structured knowledge, with precise statement context
SELECT value.entity_id AS spouse, rank
FROM wiki.wikidata.statements(['Q42'], property_id := 'P26');
```

## Quick start

The structured-data features are available in **v0.5.0**. With
[uv](https://docs.astral.sh/uv/) installed, run the demo from a source checkout:

```bash
cd /path/to/vgi-wikipedia
uv sync --locked --extra dev --extra http
uv run --no-sync python examples/wikidata_demo.py         # local fixture data
uv run --no-sync python examples/wikidata_demo.py --live  # public APIs
```

The demo uses stock DuckDB 2.0.0 and the signed Query.Farm VGI extension. It resolves
an article, joins a local reading list to entity labels, reads statement context
and follows an instance-of/subclass-of graph path. Fixture mode needs network only
if the SQL extension has not been installed yet.

For a DuckDB 2.0.0 session, register the extension repository once, then attach:

```sql
SET allow_extension_repositories = 'allowed';
CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm
  WITH PREFIX 'https://duckdb-extensions.query.farm';
INSTALL vgi FROM query_farm;
LOAD vgi FROM query_farm;
ATTACH 'wiki' AS wiki (TYPE vgi,
  LOCATION '/absolute/path/to/vgi-wikipedia/.venv/bin/vgi-wikipedia-worker');

SELECT title, entity_id FROM wiki.main.wiki_entity('Douglas Adams');
SELECT entity_id, label, label_language FROM wiki.wikidata.entity(['Q42']);
```

The worker requires Python 3.13+; the lockfile resolves `vgi-python==0.51.1`
(protocol 3.3). Use matching current VGI engine extensions. The earlier `v0.4.0`
GitHub/Docker release provides the article functions only.

To run the release without a checkout, use this location in the same ATTACH:

```sql
ATTACH 'wiki' AS wiki (TYPE vgi,
  LOCATION 'uvx --from git+https://github.com/Query-farm/vgi-wikipedia@v0.5.0 vgi-wikipedia-worker');
```

No API key is needed. Live queries need outbound access. See the different article
and structured-data licenses below and [Configuration](#configuration) for endpoint
and User-Agent overrides.

### Build from source

To build and test the worker yourself from a checkout, see [Development & testing](#development--testing).

## ⚠️ Content licensing — read this first

The **worker code in this repository is MIT** (see [LICENSE](LICENSE)).

**Article text has its own license.** Text from Wikipedia and other Wikimedia
projects is licensed **[CC-BY-SA](https://creativecommons.org/licenses/by-sa/4.0/)**
(article text is CC-BY-SA 4.0; some media differ). That means if you store,
redisplay, or redistribute the snippets / extracts this worker returns —
including feeding them into a product or a published dataset — **attribution and
share-alike are your responsibility.** This worker does not and cannot grant you
any rights to the content; it only fetches it. When in doubt, link back to the
source article (the `url` column gives you the canonical URL) and credit
Wikipedia.

**Wikidata structured data is [CC0](https://www.wikidata.org/wiki/Wikidata:Licensing).**
That includes entity labels and statements, qualifiers and reference metadata.
Following a reference URL does not change the referenced site's content license.
Custom MediaWiki, Wikibase and SPARQL endpoints may have different terms.

Please also respect the [Wikimedia API etiquette](https://www.mediawiki.org/wiki/API:Etiquette):
the worker sends a descriptive `User-Agent` (required), uses per-call timeouts,
and retries politely with backoff. For heavy/automated use, set your own
`User-Agent` (see [Configuration](#configuration)) and consider the rate limits.

## How it maps Wikipedia onto SQL

| Task | SQL surface | VGI primitive |
| --- | --- | --- |
| **Full-text search** | `wiki_search('query', lang := 'en', count := 10)` | table function |
| **A page's summary text** | `wiki_page('DuckDB'[, 'de'])` | scalar function |
| **A page's full summary row** | `wiki_page_summary('DuckDB', lang := 'en')` | table function |

**Conventions**

- VGI **scalars are positional-only** (DuckDB's `name := value` is a
  table-function feature). So `wiki_page` is two arity overloads:
  `wiki_page(title)` (English) and `wiki_page(title, lang)`. The **table**
  functions `wiki_search` / `wiki_page_summary` take named args.
- Search **snippets are stripped to plain text** — the MediaWiki API returns
  HTML (`<span class="searchmatch">…</span>` and entities); this worker removes
  the markup so the `snippet` column is clean text.
- Missing fields become **NULL** (e.g. a hit with no timestamp, a summary with
  no thumbnail).

## Function catalog

### `wiki_search(query, lang := 'en', count := 10, max_pages := 1, api_url := '')` → table

Full-text search via the Action API (`action=query&list=search`). Returns:

| column | type | notes |
| --- | --- | --- |
| `title` | VARCHAR | article title |
| `snippet` | VARCHAR | excerpt, **HTML stripped to plain text** |
| `pageid` | BIGINT | MediaWiki page id |
| `wordcount` | INTEGER | article word count |
| `url` | VARCHAR | canonical article URL |
| `lang` | VARCHAR | language the result came from |
| `extra` | VARCHAR (JSON) | `{timestamp, size}` when present, else NULL |

- `count` — results per API page (1–50, default 10).
- `max_pages` — how many `sroffset` pages to follow (1–20, default **1**: a
  single top-N call). The MediaWiki continuation token (`sroffset`) is carried
  as **externalized scan state** so paging survives DuckDB's batch boundaries.
  This is deliberately the *easy* (non-defensible) kind of scan state — a plain
  int — and is documented as such.
- `api_url` — point at **any MediaWiki wiki** (e.g.
  `https://www.wikidata.org/w/api.php`) instead of Wikipedia. May contain a
  `{lang}` placeholder.

```sql
-- Follow up to 3 pages of German results
SELECT title FROM wiki.wiki_search('datenbank', lang := 'de', count := 5, max_pages := 3);
```

### `wiki_page(title[, lang])` → VARCHAR *(scalar)*

The plain-text **summary extract** (lead paragraph) of a page, or NULL for a
missing page / NULL input. Never errors a scan.

```sql
SELECT wiki.wiki_page('DuckDB');          -- English
SELECT wiki.wiki_page('DuckDB', 'fr');    -- French Wikipedia
```

### `wiki_page_summary(title, lang := 'en', api_url := '')` → table

The rich, multi-column summary row (the multi-column companion to the scalar):
`title`, `extract`, `url`, `thumbnail_url`, `pageid`. A missing page yields **zero
rows** (not an error).

## Wikidata statements and graph queries

The existing `wiki.main` article functions keep their names and behavior.
`wiki.main.wiki_entity(title, lang := 'en', api_url := '')` adds the article bridge:
it follows redirects and returns `title`, `pageid`, `entity_id`, `url`, `lang`.
A missing article returns zero rows; an article with no associated item returns
NULL `entity_id`. On custom wikis that ID may belong to a different Wikibase.

The `wiki.wikidata` schema exposes these table functions:

| Function | Purpose |
| --- | --- |
| `search(query, count := 10, lang := 'en', entity_type := 'item')` | Candidate discovery by label/alias; `entity_type` also accepts `property`. Follows continuation up to `count` (1–500). |
| `entity(ids, lang := 'en')` | Labels, descriptions, aliases, sitelinks and revision for 1–500 Q/P IDs. |
| `statements(ids, property_id := '', rank := 'all')` | One row per full statement, including typed value, rank and statement ID. |
| `qualifiers(ids, property_id := '', rank := 'all')` | One row per qualifier snak, linked to its statement. |
| `statement_references(ids, property_id := '', rank := 'all')` | One row per reference snak, grouped by reference index/hash. |
| `sparql(query, endpoint_url := '')` | Remote SPARQL SELECT/ASK, including graph patterns and property paths. |

Entity inputs are `VARCHAR[]`, such as `['Q42', 'Q5']`. Duplicate requested IDs
are fetched once; redirects preserve `requested_id` alongside canonical `entity_id`.
Missing IDs get an entity row with `missing = true` and produce no statement rows.
Only Q/P IDs are supported, not lexemes. Batch arguments must be constants at SQL
bind time: collect IDs in your application and pass one list, then join results to
local tables. Correlated per-row API calls are not supported.

Entities include `entity_revision`, `source_api`, `modified`, `label`,
`label_language`, `description`, `aliases`, `aliases_language`, `sitelinks_json`.
Descriptions use the exact requested language; unavailable descriptions are NULL.
Labels/aliases prefer that language and fall back only to Wikidata's shared
[`mul` defaults](https://www.wikidata.org/wiki/Help:Default_values_for_labels_and_aliases).
The actual language is returned; there is no implicit English fallback.

### Statement meaning and source context

`snak_type` distinguishes `value`, `somevalue` (unknown) and `novalue` (explicitly
no value). The latter two have a NULL `value` struct, not fabricated empty values.
`rank` accepts `all`, `nondeprecated`, or `best`: best selects preferred statements
when any exist for that property, otherwise normal statements. It never invents
a single winning value when multiple statements have the chosen rank.

Typed `value` fields preserve:

- `entity_id` for entity references, and `text` / `language` for textual values.
- `time`, `time_precision`, `timezone`, `before`, `after`, `calendar_model` for dates.
  The time remains a string so BCE dates, huge years and coarse precision survive.
- `amount`, `lower_bound`, `upper_bound`, `unit` for quantities. Decimal strings
  preserve exact precision; cast explicitly for arithmetic.
- `latitude`, `longitude`, `coordinate_precision`, `globe` for coordinates.
- `raw_json` with the complete upstream datavalue, including unfamiliar value types.

Qualifier/reference calls filter by the **main statement's** `property_id`.
Their output `statement_property_id` identifies that main property; output
`property_id` identifies the qualifier or reference property. Reference snaks
sharing `reference_hash` and `reference_index` belong to one reference group.
Indices are zero-based; qualifier/snak indices are within each property group.

```sql
SELECT s.value.entity_id AS spouse, q.property_id, q.value.time
FROM wiki.wikidata.statements(['Q42'], property_id := 'P26') s
JOIN wiki.wikidata.qualifiers(['Q42'], property_id := 'P26') q
USING (source_api, entity_revision, requested_id, statement_id)
ORDER BY q.property_id;

SELECT reference_hash, property_id, value.text, value.entity_id
FROM wiki.wikidata.statement_references(['Q42'], property_id := 'P26')
ORDER BY reference_hash, property_id;
```

Keep revision and source in joins: independent API calls can observe different
revisions. No global transaction snapshot across entities or calls is promised.

### SPARQL and RDF terms

```sql
SELECT ancestor.kind, ancestor.value
FROM wiki.wikidata.sparql('SELECT DISTINCT ?ancestor WHERE {
  <http://www.wikidata.org/entity/Q42>
  <http://www.wikidata.org/prop/direct/P31>/<http://www.wikidata.org/prop/direct/P279>*
  ?ancestor
} LIMIT 20')
ORDER BY ancestor.value;

SELECT answer FROM wiki.wikidata.sparql('ASK {
  <http://www.wikidata.org/entity/Q42> <http://www.wikidata.org/prop/direct/P31> ?class
}');
```

Each SELECT variable becomes a `STRUCT(kind VARCHAR, value VARCHAR,
datatype VARCHAR, language VARCHAR)`. `kind` is `iri`, `blank`, or `literal`;
`value` is the unchanged lexical value. SQL NULL denotes an unbound variable.
Blank-node identifiers are scoped to that result, not durable entity IDs.
ASK returns one `answer BOOLEAN`. Empty SELECT results preserve their columns.
Variables that collide under SQL case folding are rejected.

Only syntactically valid SELECT/ASK queries are accepted; updates and
CONSTRUCT/DESCRIBE graph results are rejected. Prefixes must be declared in the
query; full IRIs also work. The remote endpoint evaluates graph patterns,
aggregates, property paths and any SERVICE clauses. This connector does not
implement a local graph store or local inference.

**SPARQL runs at SQL bind time to discover its columns.** DESCRIBE/EXPLAIN can
therefore issue requests. Execution consumes the frozen bound result; a prepared
plan may retain that result until it is rebound. Reprepare to request fresh data,
and set `cache_seconds := 0` to bypass the response cache. Queries are not rewritten
or paginated: express ordering and LIMIT in SPARQL when needed. Dataset selection,
replication lag, federation and query limits are properties of your chosen endpoint.

### Bounds, caching and failure behavior

All new functions accept these named options. Non-SPARQL functions also accept
`api_url := ''`; structured functions use the independent Wikibase endpoint.

| Option | Default | Accepted range / meaning |
| --- | --- | --- |
| `max_rows` | 10,000 | 1–100,000; larger results fail instead of being silently truncated. |
| `max_bytes` | 16 MiB | 1 KiB–64 MiB; cumulative response bytes and, separately, the Arrow snapshot must fit. |
| `timeout_ms` | 30,000 | 1–60,000; shared HTTP/retry budget checked between reads, not a hard CPU deadline. |
| `cache_seconds` | 300 | 0–3,600; maximum accepted cache age. Zero bypasses reads and writes. |

Entity requests batch at most 50 IDs per HTTP call. Successful responses use a
process-local cache bounded to 128 entries and 32 MiB of encoded response data.
Up to three attempts handle retryable transport, rate-limit and upstream errors;
Retry-After is honored within the remaining budget. Partial-response indicators,
API warnings, malformed results and exceeded bounds fail explicitly. An endpoint
that silently truncates an otherwise valid response cannot be detected reliably.

Each scan freezes the full bounded result in serialized Arrow scan state before
emitting rows. Batches remain consistent even when another worker process resumes
the scan; the response cache is only an optimization. This is designed for bounded
enrichment and graph lookups. It materializes responses in memory, carries the
snapshot with scan state, and is not a bulk Wikidata dump importer. JSON parsing,
Arrow construction and query parsing may consume more memory/time than the encoded
byte/request limits; SPARQL query text is capped at 64 KiB. Public endpoints offer
no worker-controlled availability guarantee.

## RAG composition

```sql
-- search Wikipedia, then embed the snippets for retrieval (with vgi-embed)
WITH hits AS (
  SELECT title, url, snippet FROM wiki.wiki_search('vector database', count := 10)
)
SELECT title, url, embed.embed(snippet) AS vec FROM hits;

-- search, then fetch each top hit's lead paragraph with the wiki_page scalar
SELECT h.title, wiki.wiki_page(h.title) AS summary
FROM wiki.wiki_search('apache arrow', count := 3) AS h
ORDER BY h.title;

-- ground an article: its summary text and URL plus the linked Wikidata item
SELECT s.title, e.entity_id, s.url, s.extract
FROM wiki.wiki_page_summary('DuckDB') AS s
JOIN wiki.wiki_entity('DuckDB') AS e USING (pageid);
```

## Configuration

| env var | purpose | default |
| --- | --- | --- |
| `VGI_WIKIPEDIA_API_URL` | override the `api.php` base (another wiki, or a mock server). May contain `{lang}`. | `https://{lang}.wikipedia.org/w/api.php` |
| `VGI_WIKIDATA_API_URL` | independent Wikibase Action API base | `https://www.wikidata.org/w/api.php` |
| `VGI_WIKIDATA_SPARQL_URL` | independent SPARQL query service | `https://query.wikidata.org/sparql` |
| `VGI_WIKIPEDIA_USER_AGENT` | override the (required) descriptive User-Agent. | `vgi-wikipedia/<version> (…)` |

No API key is ever needed or accepted — the MediaWiki API is free and open.

## Endpoints used

- **Action API** `api.php?action=query&list=search` — full-text search, with
  `srprop=snippet|wordcount|timestamp` and `sroffset` paging.
- **REST summary** `/api/rest_v1/page/summary/{title}` — the rich page summary
  (extract + thumbnail + canonical URL), with an Action-API `prop=extracts`
  fallback when REST is unavailable (e.g. a non-Wikimedia wiki).

## Development & testing

```bash
uv sync --locked --extra dev --extra http
make check        # all gates, three transports, strict metadata and fixture demo
make test         # unit/fixture tests + mock-server E2E + SQL (haybarn) E2E
make test-unit    # pytest: fixture parsers, scan-state round-trip, mock-server E2E
make test-sql     # DuckDB sqllogictest E2E via haybarn-unittest, mock-server-driven
make test-live    # OPTIONAL gated smoke against the real Wikipedia API (needs network)
make lint         # formatting, ruff, mypy and isolated pydoclint
make metadata     # strict catalog metadata and executable examples on fixtures
make test-sql-all # same SQL assertions over subprocess, HTTP and Unix sockets
make demo         # stock DuckDB end-to-end fixture demo
```

The default test gate **never depends on live Wikipedia or Wikidata**: every networked path
is exercised against a local mock HTTP server serving captured fixtures
(deterministic, no keys, no cost). The SQL E2E (`make test-sql`) launches that
mock, points the worker's `VGI_WIKIPEDIA_API_URL` at it, and runs the worker as a
real DuckDB subprocess — asserting the columns, the `sroffset` scan-state
round-trip across a batch boundary, plain-text snippets, and a clean error path.

The Python suite also drives the signed VGI extension from stock DuckDB 2.0.0.
The SQLLogicTest suite uses Haybarn 2.0.0rc11 locally; CI additionally tests the
current 2.0 release on Linux. Metadata checks execute catalog examples
against the local fixture server. Pydoclint runs in an isolated `uvx` environment
because its docstring-parser fork overlaps the SDK's parser package.

`tests/fixtures/wikidata_q42.json` is a captured Q42 response (revision
2563066692), reduced to P31/P569/P26/P214, English/German/shared language terms
and English/German sitelinks. Tests add synthetic missing IDs and redirects. The
mock SPARQL endpoint evaluates queries with RDFLib over a small independent graph.
Live tests are opt-in and may fail when public endpoints are unavailable.

## Licensing summary

- **Worker code:** MIT (this repository).
- **Article text:** **CC-BY-SA** (Wikipedia/Wikimedia) — attribution &
  share-alike are the **user's** responsibility (see the warning at the top).
- **Wikidata structured data:** CC0; other endpoints may differ.
- **Dependencies:** permissive (`httpx`, `pyarrow`, `vgi-python`, `rdflib`).

---

## Authorship & License

Written by [Query.Farm](https://query.farm).

Copyright 2026 Query Farm LLC - https://query.farm

