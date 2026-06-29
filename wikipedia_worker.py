# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.5",
#     "httpx>=0.27",
# ]
# ///
"""VGI worker exposing Wikipedia / MediaWiki search and page retrieval to SQL.

Assembles the functions in ``vgi_wikipedia`` into a single ``wiki`` catalog and
runs the worker over stdio (a DuckDB subprocess) or HTTP (via serve.py).

This is an **egress connector** over the FREE official MediaWiki Action API +
REST summary endpoint -- clean, no key, no scraping. Great for RAG / knowledge
grounding; pairs with vgi-embed / vgi-rerank.

Usage:
    uv run wikipedia_worker.py            # serve over stdio (DuckDB subprocess)
    python serve.py --port 8000           # serve over HTTP

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'wiki' (TYPE vgi, LOCATION 'uv run wikipedia_worker.py');

    SELECT title, snippet, url
      FROM wiki.wiki_search('apache arrow', lang := 'en', count := 10);
    SELECT wiki.wiki_page('DuckDB');
    SELECT * FROM wiki.wiki_page_summary('DuckDB', lang := 'en');

Configuration (env, optional):
    VGI_WIKIPEDIA_API_URL      override the api.php base (e.g. a non-Wikimedia
                               wiki, or a mock server). May contain ``{lang}``.
    VGI_WIKIPEDIA_USER_AGENT   override the (required) descriptive User-Agent.

LICENSING: the worker code is MIT. CONTENT retrieved from Wikipedia / Wikimedia
projects is **CC-BY-SA** (text) -- attribution and share-alike are the user's
responsibility. See README.md.
"""

from __future__ import annotations

from vgi import Worker
from vgi.catalog import Catalog, Schema

from vgi_wikipedia.meta import keywords_json
from vgi_wikipedia.scalars import SCALAR_FUNCTIONS
from vgi_wikipedia.tables import TABLE_FUNCTIONS
from vgi_wikipedia.tables_page import PAGE_TABLE_FUNCTIONS

_CATALOG_DESCRIPTION_LLM = (
    "Search Wikipedia (or any MediaWiki) and retrieve page summaries from SQL. "
    "Run full-text searches that return ranked article titles, plain-text snippets, page ids, "
    "word counts and canonical URLs; fetch a single page's lead-paragraph summary extract; and "
    "pull a page's rich summary row (title, extract, url, thumbnail, page id). All over the FREE "
    "official MediaWiki Action API + REST summary endpoint (no API key). Use it for "
    "retrieval-augmented generation (RAG), knowledge grounding, fact lookup, and enriching rows "
    "with encyclopedic context. Choose any wiki language via 'lang' and target non-Wikimedia "
    "wikis via 'api_url'. NOTE: retrieved article text is CC-BY-SA — attribution and share-alike "
    "are the caller's responsibility."
)

_CATALOG_DESCRIPTION_MD = (
    "# Wikipedia & MediaWiki Search in SQL\n\n"
    "Run Wikipedia full-text search and page-summary retrieval directly from DuckDB SQL — "
    "powered by the free, official MediaWiki API, with no API key, no scraping, and no setup.\n\n"
    "This VGI extension turns any DuckDB session into a live Wikipedia and MediaWiki client. "
    "It is built for engineers and data teams who want to ground large language models with "
    "encyclopedic facts, enrich rows with authoritative context, or run ad-hoc knowledge lookups "
    "without leaving SQL. Because it speaks plain MediaWiki, it works against English Wikipedia by "
    "default, any of the 300+ Wikipedia language editions via a single `lang` argument, and any "
    "third-party MediaWiki wiki via `api_url`. It is a thin, polite **egress connector**: queries "
    "leave the engine for the wiki, results come back as Arrow rows, and the worker process never "
    "crashes on a missing page or a flaky network.\n\n"
    "Under the hood the extension calls the [MediaWiki Action API](https://www.mediawiki.org/wiki/API:Main_page) "
    "for ranked search and the [MediaWiki REST API](https://www.mediawiki.org/wiki/API:REST_API) "
    "page-summary endpoint for rich lead extracts and thumbnails — the same free, open interfaces "
    "that power [Wikipedia](https://en.wikipedia.org/) itself. The networked client sends a "
    "descriptive User-Agent, applies per-call timeouts, and retries with backoff on rate limits and "
    "server errors, so it stays well-behaved against the public Wikimedia endpoints. The software is "
    "open source and mirrors the upstream [MediaWiki source on GitHub](https://github.com/wikimedia/mediawiki); "
    "see the [MediaWiki search API documentation](https://www.mediawiki.org/wiki/API:Search) for the "
    "underlying query semantics.\n\n"
    "**SQL use cases and function surface.** Use `wiki_search(query, lang := 'en', count := 10, "
    "max_pages := 1, api_url := '')` — a table function — to run full-text search and get ranked "
    "rows of article title, plain-text snippet, page id, word count and canonical URL, ideal for "
    "retrieval-augmented generation (RAG) candidate selection. Use the `wiki_page(title)` / "
    "`wiki_page(title, lang)` scalar to fetch a single page's plain-text summary extract inline in a "
    "`SELECT` (returns NULL on a missing page rather than erroring). Use `wiki_page_summary(title, "
    "lang := 'en', api_url := '')` — a table function — for the rich single-row summary including "
    "the extract, canonical URL, thumbnail and page id. Pair it with vgi-embed and vgi-rerank to "
    "build knowledge-grounding pipelines entirely in SQL.\n\n"
    "Retrieved article **content is CC-BY-SA** — attribution and share-alike are the caller's "
    "responsibility; the `url` column is provided so you can link back and attribute."
)

_SCHEMA_DESCRIPTION_LLM = (
    "Wikipedia / MediaWiki search and page-retrieval functions: full-text search returning "
    "ranked titles, snippets, page ids, word counts and URLs (`wiki_search`); a scalar that "
    "returns a page's plain-text summary extract (`wiki_page`); and a table function returning a "
    "page's rich summary row (`wiki_page_summary`). All run over the free official MediaWiki "
    "Action API + REST summary endpoint (no key). Language-selectable via `lang` and usable "
    "against any MediaWiki wiki via `api_url`. Use it for RAG, knowledge grounding, and fact "
    "lookup. Retrieved text is CC-BY-SA -- attribution and share-alike are the caller's "
    "responsibility."
)

_SCHEMA_DESCRIPTION_MD = (
    "# wiki.main\n\n"
    "Wikipedia / MediaWiki full-text search and page-summary retrieval functions over the free "
    "MediaWiki API.\n\n"
    "- `wiki_search(query, lang :=, count :=, max_pages :=, api_url :=)` -- ranked full-text "
    "results (table function).\n"
    "- `wiki_page(title[, lang])` -- a page's plain-text summary extract (scalar).\n"
    "- `wiki_page_summary(title, lang :=, api_url :=)` -- a page's rich summary row "
    "(table function).\n\n"
    "Retrieved content is **CC-BY-SA**; attribution and share-alike are the caller's "
    "responsibility."
)

_SCHEMA_EXAMPLE_QUERIES = (
    "SELECT title, snippet, url FROM wiki.main.wiki_search('apache arrow', lang := 'en', count := 10);\n"
    "SELECT wiki.main.wiki_page('DuckDB');\n"
    "SELECT title, extract, url FROM wiki.main.wiki_page_summary('DuckDB', lang := 'en');"
)

_CATALOG_KEYWORDS = [
    "wikipedia",
    "mediawiki",
    "wiki",
    "search",
    "full-text search",
    "page",
    "summary",
    "extract",
    "article",
    "encyclopedia",
    "rag",
    "retrieval",
    "knowledge grounding",
    "fact lookup",
    "egress connector",
]

_SCHEMA_KEYWORDS = [
    "wikipedia",
    "mediawiki",
    "wiki_search",
    "wiki_page",
    "wiki_page_summary",
    "search",
    "page summary",
    "extract",
    "article",
    "encyclopedia",
    "rag",
    "retrieval",
    "knowledge grounding",
    "lang",
    "language",
]

_CATALOG_TAGS = {
    "vgi.title": "Wikipedia / MediaWiki Search & Page Retrieval",
    "vgi.keywords": keywords_json(_CATALOG_KEYWORDS),
    "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
    "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
    "vgi.author": "Query.Farm",
    "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
    "vgi.license": "MIT",
    "vgi.support_contact": "https://github.com/Query-farm/vgi-wikipedia/issues",
    "vgi.support_policy_url": "https://github.com/Query-farm/vgi-wikipedia/blob/main/README.md",
}

_WIKI_CATALOG = Catalog(
    name="wiki",
    default_schema="main",
    comment="Wikipedia / MediaWiki full-text search and page retrieval for SQL / RAG (free MediaWiki API, no key)",
    tags=_CATALOG_TAGS,
    source_url="https://github.com/Query-farm/vgi-wikipedia",
    schemas=[
        Schema(
            name="main",
            comment="Wikipedia / MediaWiki full-text search and page retrieval for SQL / RAG",
            tags={
                "vgi.title": "Wikipedia / MediaWiki Functions",
                "vgi.keywords": keywords_json(_SCHEMA_KEYWORDS),
                "vgi.doc_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.doc_md": _SCHEMA_DESCRIPTION_MD,
                # VGI139: source_url is kept on the catalog object only.
                "vgi.example_queries": _SCHEMA_EXAMPLE_QUERIES,
                # VGI123 classifying tags -- BARE keys (not vgi.-namespaced).
                "domain": "knowledge",
                "category": "search",
                "topic": "wikipedia-retrieval",
            },
            functions=[*SCALAR_FUNCTIONS, *TABLE_FUNCTIONS, *PAGE_TABLE_FUNCTIONS],
        ),
    ],
)


class WikipediaWorker(Worker):
    """Worker process hosting the ``wiki`` catalog."""

    catalog = _WIKI_CATALOG


def main() -> None:
    """Run the Wikipedia worker process (stdio or, via flags, HTTP)."""
    WikipediaWorker.main()


if __name__ == "__main__":
    main()
