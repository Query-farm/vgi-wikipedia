"""Wikipedia / MediaWiki full-text search and page retrieval as a VGI worker.

The implementation is split so each concern stays focused:

- ``client``       -- the polite MediaWiki HTTP client (User-Agent, per-call
  timeout, bounded retry/backoff on 429/5xx, configurable ``api_url`` so tests
  point it at a mock). Never crashes the worker -- failures raise
  :class:`~vgi_wikipedia.client.WikiError`, which the functions surface as a
  clean DuckDB error.
- ``parse``        -- pure JSON-to-row mapping for the Action API search list and
  the page summary/extract, including HTML-snippet stripping and
  missing-field -> NULL. Unit-tested against captured fixtures, no network.
- ``schema_utils`` -- Arrow column-comment helper + shared types.
- ``tables``       -- ``wiki_search`` table function with ``sroffset`` paging
  carried as externalized scan state.
- ``scalars``      -- ``wiki_page`` scalar (positional-only) returning a page
  summary/extract.

``wikipedia_worker.py`` at the repo root assembles these into the ``wiki``
catalog and runs the worker over stdio (a DuckDB subprocess).

Licensing note: the worker code is MIT. CONTENT retrieved from Wikipedia /
Wikimedia projects is licensed **CC-BY-SA** (text) -- attribution and
share-alike are the user's responsibility. See README.md.
"""

from __future__ import annotations

__version__ = "0.1.0"
