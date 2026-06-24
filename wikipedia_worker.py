# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.8.3",
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

from vgi_wikipedia.scalars import SCALAR_FUNCTIONS
from vgi_wikipedia.tables import TABLE_FUNCTIONS
from vgi_wikipedia.tables_page import PAGE_TABLE_FUNCTIONS

_WIKI_CATALOG = Catalog(
    name="wiki",
    default_schema="main",
    schemas=[
        Schema(
            name="main",
            comment="Wikipedia / MediaWiki full-text search and page retrieval for SQL / RAG",
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
