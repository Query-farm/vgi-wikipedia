"""``wiki_search`` -- full-text search over Wikipedia / any MediaWiki.

``wiki_search(query, lang := 'en', count := 10, max_pages := 1, api_url := '')``
runs the MediaWiki Action API (``action=query&list=search``) and streams the
unified schema::

    title VARCHAR, snippet VARCHAR (HTML stripped to text), pageid BIGINT,
    wordcount INTEGER, url VARCHAR, lang VARCHAR, extra VARCHAR(JSON)

It is a **table function**, so DuckDB's ``name := value`` named arguments apply
(``lang`` / ``count`` / ``max_pages`` / ``api_url``).

Pagination as scan state
------------------------
The MediaWiki API pages with ``sroffset``: each response carries a
``continue.sroffset`` token pointing at the next page. That token -- plus how far
into the current page we've emitted -- is the externalized, plain-serializable
scan state (:class:`ScanState`), round-tripped across every ``process`` tick (and
so across batch boundaries). ``sroffset`` is a plain int, deliberately the
*easy* (non-defensible) kind of scan state, documented as such.

``max_pages`` bounds how many ``sroffset`` pages we'll follow (default 1: a
single top-N call). The fetched page's rows live in a process-local cache keyed
by the execution id; only the cheap cursor is serialized.

Network-worker discipline: a client error (bad request, HTTP failure, timeout)
is caught and re-raised as a clean DuckDB error via :class:`WikiError`; it never
crashes the worker.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from typing import Annotated, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.invocation import BindResponse, GlobalInitResponse
from vgi.metadata import FunctionExample
from vgi.table_function import (
    BindParams,
    ProcessParams,
    TableFunctionGenerator,
)
from vgi_rpc import ArrowSerializableDataclass
from vgi_rpc.rpc import OutputCollector

from vgi_wikipedia.client import WikiClient, WikiError
from vgi_wikipedia.parse import SearchRow, parse_search
from vgi_wikipedia.schema_utils import field

# Rows emitted per process tick. Deliberately small so a single API page (up to
# 50 hits) spans several batches -- exercising the scan-state round-trip across
# batch boundaries.
CHUNK_ROWS = 5

# Process-local cache of FETCHED PAGES, keyed by (execution key, sroffset). The
# CURSOR (which sroffset page we're on, how far into it we've emitted, how many
# pages we've followed) lives in the serializable scan state; the heavy fetched
# rows live here. Keying by sroffset makes a re-fetch idempotent and keeps
# parallel scan instances (which share an execution id) from corrupting a shared
# growing buffer -- each page is fetched/cached exactly once per offset.
#
# Each cache value is ``(rows, next_sroffset)``: the page's rows plus the
# continuation offset (``None`` when the result set is exhausted).
_PAGE_CACHE: dict[tuple[str, int], tuple[list[SearchRow], int | None]] = {}

WIKI_SEARCH_SCHEMA = pa.schema(
    [
        field("title", pa.string(), "Article title."),
        field("snippet", pa.string(), "Search-result excerpt, HTML stripped to plain text."),
        field("pageid", pa.int64(), "MediaWiki page id."),
        field("wordcount", pa.int32(), "Word count of the article."),
        field("url", pa.string(), "Canonical article URL."),
        field("lang", pa.string(), "Wiki language code the result came from."),
        field("extra", pa.string(), "Extra fields (timestamp, size), JSON-encoded (else NULL)."),
    ]
)


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiSearchArgs:
    """Arguments for ``wiki_search`` (one positional query + named options).

    Field names MUST match the SQL named-argument keys (DuckDB routes
    ``name := value`` by field name), so ``lang`` / ``count`` / ``max_pages`` /
    ``api_url`` are spelled exactly as a caller types them.
    """

    query: Annotated[str, Arg(0, doc="The full-text search query.")]
    lang: Annotated[str, Arg("lang", default="en", doc="Wiki language code (default 'en').")]
    count: Annotated[
        int,
        Arg("count", default=10, ge=1, le=50, doc="Results per API page, 1-50 (default 10)."),
    ]
    max_pages: Annotated[
        int,
        Arg(
            "max_pages",
            default=1,
            ge=1,
            le=20,
            doc="Max sroffset pages to follow, 1-20 (default 1: a single top-N call).",
        ),
    ]
    api_url: Annotated[
        str,
        Arg(
            "api_url",
            default="",
            doc="Override the MediaWiki api.php URL (e.g. another wiki); default Wikipedia.",
        ),
    ]


@dataclass(kw_only=True)
class ScanState(ArrowSerializableDataclass):
    """Externalized pagination cursor, round-tripped across process ticks.

    Plain-serializable ints/bools only -- the fetched page rows live in the
    process-local cache. ``cur_sroffset`` is the MediaWiki offset of the page we
    are currently emitting; ``pos`` is how far into that page we've emitted;
    ``pages_done`` counts pages fully emitted (bounded by ``max_pages``);
    ``exhausted`` flips true once the result set has no further continuation.
    """

    cur_sroffset: int = 0
    pos: int = 0
    pages_done: int = 0
    exhausted: bool = False


def _build_client(api_url: str) -> WikiClient:
    """Construct a client, honoring an explicit ``api_url`` override.

    An empty ``api_url`` arg falls back to ``VGI_WIKIPEDIA_API_URL`` / the
    Wikipedia default (handled inside :class:`WikiClient`).
    """
    return WikiClient(api_url=api_url or None)


def _extra_json(row: SearchRow) -> str | None:
    """JSON-encode the provider-specific extras (timestamp/size), or NULL."""
    extra: dict[str, object] = {}
    if row.timestamp is not None:
        extra["timestamp"] = row.timestamp
    if row.size is not None:
        extra["size"] = row.size
    if not extra:
        return None
    return json.dumps(extra, ensure_ascii=False)


def _to_batch(rows: list[SearchRow], schema: pa.Schema) -> pa.RecordBatch:
    return pa.RecordBatch.from_pydict(
        {
            "title": [r.title for r in rows],
            "snippet": [r.snippet for r in rows],
            "pageid": [r.pageid for r in rows],
            "wordcount": [r.wordcount for r in rows],
            "url": [r.url for r in rows],
            "lang": [r.lang for r in rows],
            "extra": [_extra_json(r) for r in rows],
        },
        schema=schema,
    )


class WikiSearch(TableFunctionGenerator[WikiSearchArgs, ScanState]):
    """Full-text Wikipedia / MediaWiki search (see module docstring)."""

    FunctionArguments: ClassVar[type] = WikiSearchArgs

    class Meta:
        """Function metadata."""

        name = "wiki_search"
        description = "Full-text search over Wikipedia (or any MediaWiki); returns the unified schema"
        categories = ["search", "wikipedia", "mediawiki", "rag", "retrieval"]
        examples = [
            FunctionExample(
                sql="SELECT title, snippet, url FROM wiki_search('apache arrow', lang := 'en', count := 10)",
                description="Top-10 English Wikipedia results for 'apache arrow'",
            ),
            FunctionExample(
                sql="SELECT title FROM wiki_search('duckdb', lang := 'de', count := 5, max_pages := 3)",
                description="Follow up to 3 sroffset pages of German results",
            ),
        ]

    @classmethod
    def on_bind(cls, params: BindParams[WikiSearchArgs]) -> BindResponse:
        """Validate the query and bind the fixed output schema."""
        a = params.args
        if not (a.query or "").strip():
            raise ValueError("wiki_search requires a non-empty query")
        return BindResponse(output_schema=WIKI_SEARCH_SCHEMA)

    @classmethod
    def on_init(cls, params: object) -> GlobalInitResponse:
        """Pin a single scan worker so sequential sroffset paging emits the result once."""
        # Paging is inherently sequential (each page's sroffset comes from the
        # previous response), and the result set must be produced exactly once.
        # Pin to a single scan worker so parallel scan instances don't each
        # re-emit the whole result.
        return GlobalInitResponse(max_workers=1)

    @classmethod
    def initial_state(cls, params: ProcessParams[WikiSearchArgs]) -> ScanState:
        """Start at the first sroffset page with a fresh scan state."""
        return ScanState()

    @classmethod
    def _execution_key(cls, params: ProcessParams[WikiSearchArgs]) -> str:
        eid = getattr(params.init_response, "execution_id", None) or getattr(
            params.init_call, "global_execution_id", None
        )
        a = params.args
        return f"{eid}:{a.lang}:{a.query}:{a.count}:{a.api_url}"

    @classmethod
    def _fetch_page(cls, params: ProcessParams[WikiSearchArgs], sroffset: int) -> tuple[list[SearchRow], int | None]:
        """Fetch (and parse) one MediaWiki page; returns ``(rows, next_sroffset)``."""
        a = params.args
        client = _build_client(a.api_url)
        try:
            payload = client.search(a.query, lang=a.lang, count=a.count, sroffset=sroffset)
        except WikiError:
            raise
        except Exception as exc:  # noqa: BLE001 - never let an odd error crash the worker
            raise WikiError(f"wiki_search failed: {exc}") from exc
        return parse_search(payload, lang=a.lang)

    @classmethod
    def _page(cls, params: ProcessParams[WikiSearchArgs], sroffset: int) -> tuple[list[SearchRow], int | None]:
        """Return the cached page for ``sroffset``, fetching it once if absent.

        Keyed by ``(execution key, sroffset)`` so it is idempotent: parallel
        scan instances sharing an execution id, and re-fetches after a cache
        eviction, all converge on the same page rather than appending to a
        shared, growing buffer.
        """
        cache_key = (cls._execution_key(params), sroffset)
        cached = _PAGE_CACHE.get(cache_key)
        if cached is None:
            cached = cls._fetch_page(params, sroffset)
            _PAGE_CACHE[cache_key] = cached
        return cached

    @classmethod
    def process(
        cls,
        params: ProcessParams[WikiSearchArgs],
        state: ScanState,
        out: OutputCollector,
    ) -> None:
        """Emit the next chunk of search rows, advancing sroffset paging as needed."""
        # The framework requires every non-finishing tick to emit a batch, so we
        # loop internally across empty/exhausted pages until we either emit a
        # chunk or finish -- never returning empty-handed without finishing.
        a = params.args

        while not state.exhausted:
            rows, next_sroffset = cls._page(params, state.cur_sroffset)

            # Emit the next chunk of the CURRENT page.
            if state.pos < len(rows):
                chunk = rows[state.pos : state.pos + CHUNK_ROWS]
                out.emit(_to_batch(chunk, params.output_schema))
                state.pos += len(chunk)
                return

            # Current page fully emitted: advance to the next page if one remains
            # and we are still under the max_pages budget; otherwise stop.
            pages_done = state.pages_done + 1
            if next_sroffset is None or not rows or pages_done >= a.max_pages:
                state.exhausted = True
                break

            state.cur_sroffset = next_sroffset
            state.pos = 0
            state.pages_done = pages_done
            # Loop to emit the new page's first chunk in this same tick.

        out.finish()


# Re-export the cache so tests can simulate eviction between ticks.
def _clear_cache() -> None:  # pragma: no cover - test helper
    _PAGE_CACHE.clear()


def _api_url_env() -> str | None:  # pragma: no cover - trivial accessor
    return os.environ.get("VGI_WIKIPEDIA_API_URL")


TABLE_FUNCTIONS: list[type] = [WikiSearch]
