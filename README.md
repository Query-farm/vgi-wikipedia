<p align="center">
  <img src="https://raw.githubusercontent.com/Query-farm/vgi/main/docs/vgi-logo.png" alt="Vector Gateway Interface (VGI)" width="320">
</p>

<p align="center"><em>A <a href="https://query.farm">Query.Farm</a> VGI worker for DuckDB.</em></p>

# Wikipedia & MediaWiki Search and Page Summaries in DuckDB

> **vgi-wikipedia** · a [Query.Farm](https://query.farm) VGI worker

A [VGI](https://github.com/query-farm/vgi-python) worker that brings
**Wikipedia / MediaWiki full-text search and page retrieval** into DuckDB/SQL —
search articles and fetch page summaries as SQL functions, over the **free,
official MediaWiki API**. No API key, no scraping. Great for RAG / knowledge
grounding; pairs naturally with [`vgi-embed`](https://github.com/Query-farm/vgi-embed)
and `vgi-rerank`.

```sql
INSTALL vgi FROM community; LOAD vgi;
ATTACH 'wiki' (TYPE vgi, LOCATION 'uv run wikipedia_worker.py');

-- Full-text search (table function; named args)
SELECT title, snippet, url
FROM wiki.wiki_search('apache arrow', lang := 'en', count := 10);

-- A page's summary extract (scalar; positional only)
SELECT wiki.wiki_page('DuckDB');             -- English
SELECT wiki.wiki_page('DuckDB', 'de');       -- German Wikipedia

-- The rich, multi-column page summary (table function)
SELECT title, extract, url, thumbnail_url, pageid
FROM wiki.wiki_page_summary('DuckDB', lang := 'en');
```

## ⚠️ Content licensing — read this first

The **worker code in this repository is MIT** (see [LICENSE](LICENSE)).

**The CONTENT it retrieves is not.** Text from Wikipedia and other Wikimedia
projects is licensed **[CC-BY-SA](https://creativecommons.org/licenses/by-sa/4.0/)**
(article text is CC-BY-SA 4.0; some media differ). That means if you store,
redisplay, or redistribute the snippets / extracts this worker returns —
including feeding them into a product or a published dataset — **attribution and
share-alike are your responsibility.** This worker does not and cannot grant you
any rights to the content; it only fetches it. When in doubt, link back to the
source article (the `url` column gives you the canonical URL) and credit
Wikipedia.

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
SELECT wiki_page('DuckDB');          -- English
SELECT wiki_page('DuckDB', 'fr');    -- French Wikipedia
```

### `wiki_page_summary(title, lang := 'en', api_url := '')` → table

The rich, multi-column summary row (the multi-column companion to the scalar):
`title`, `extract`, `url`, `thumbnail_url`, `pageid`. A missing page yields **zero
rows** (not an error).

## RAG composition

```sql
-- search Wikipedia, then embed the snippets for retrieval (with vgi-embed)
WITH hits AS (
  SELECT title, url, snippet FROM wiki.wiki_search('vector database', count := 10)
)
SELECT title, url, embed.embed(snippet) AS vec FROM hits;
```

## Configuration

| env var | purpose | default |
| --- | --- | --- |
| `VGI_WIKIPEDIA_API_URL` | override the `api.php` base (another wiki, or a mock server). May contain `{lang}`. | `https://{lang}.wikipedia.org/w/api.php` |
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
make test         # unit/fixture tests + mock-server E2E + SQL (haybarn) E2E
make test-unit    # pytest: fixture parsers, scan-state round-trip, mock-server E2E
make test-sql     # DuckDB sqllogictest E2E via haybarn-unittest, mock-server-driven
make test-live    # OPTIONAL gated smoke against the real Wikipedia API (needs network)
make lint         # ruff + mypy
```

The default test gate **never depends on live Wikipedia**: every networked path
is exercised against a local mock HTTP server serving captured fixtures
(deterministic, no keys, no cost). The SQL E2E (`make test-sql`) launches that
mock, points the worker's `VGI_WIKIPEDIA_API_URL` at it, and runs the worker as a
real DuckDB subprocess — asserting the columns, the `sroffset` scan-state
round-trip across a batch boundary, plain-text snippets, and a clean error path.

## Licensing summary

- **Worker code:** MIT (this repository).
- **Retrieved content:** **CC-BY-SA** (Wikipedia/Wikimedia) — attribution &
  share-alike are the **user's** responsibility (see the warning at the top).
- **Dependencies:** permissive (`httpx`, `pyarrow`, `vgi-python`).

---

## Authorship & License

Written by [Query.Farm](https://query.farm).

Copyright 2026 Query Farm LLC - https://query.farm

