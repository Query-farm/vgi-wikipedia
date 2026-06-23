"""In-process table-function tests for ``wiki_search`` and ``wiki_page_summary``.

These exercise the full bind -> init -> process lifecycle through
:mod:`tests.harness`, with the network mocked at the
:class:`~vgi_wikipedia.client.WikiClient` boundary (no HTTP). The key assertion
is the **scan-state round-trip**: running with ``serialize_state=True`` forces
the ``sroffset`` cursor through its Arrow serialization between every tick, and
the streamed rows must be identical to a single-shot run -- proving the cursor
survives batch boundaries and page boundaries.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

import vgi_wikipedia.tables as tables
from tests.harness import run_table_function
from vgi_wikipedia.client import WikiError
from vgi_wikipedia.parse import parse_search
from vgi_wikipedia.tables import WikiSearch
from vgi_wikipedia.tables_page import WikiPageSummary

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


@pytest.fixture(autouse=True)
def _clear_cache():
    tables._clear_cache()
    yield
    tables._clear_cache()


def _patch_pages(monkeypatch, *page_names: str) -> None:
    """Make WikiSearch fetch the given fixtures in sroffset order, no HTTP."""
    pages = {0: page_names[0]}
    if len(page_names) > 1:
        pages[3] = page_names[1]  # search_page1 carries continue.sroffset=3

    def fake_fetch(cls, params, sroffset):
        name = pages.get(sroffset)
        if name is None:
            return [], None
        return parse_search(_load(name), lang=params.args.lang)

    monkeypatch.setattr(WikiSearch, "_fetch_page", classmethod(fake_fetch))


class TestWikiSearchSchema:
    def test_output_columns(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, "search_page1")
        table = run_table_function(WikiSearch, positional=("apache arrow",))
        assert table.column_names == [
            "title",
            "snippet",
            "pageid",
            "wordcount",
            "url",
            "lang",
            "extra",
        ]


class TestWikiSearchRows:
    def test_single_page_rows(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, "search_page1")
        table = run_table_function(WikiSearch, positional=("apache arrow",), named={"lang": "en"})
        assert table.column("title").to_pylist() == [
            "Apache Arrow",
            "Apache Parquet",
            "Columnar database",
        ]
        assert table.column("pageid").to_pylist() == [49371158, 44432600, 13877946]

    def test_snippets_are_plain_text(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, "search_page1")
        table = run_table_function(WikiSearch, positional=("apache arrow",))
        for snip in table.column("snippet").to_pylist():
            assert snip is None or "<" not in snip

    def test_extra_json_carries_timestamp_and_size(self, monkeypatch) -> None:
        _patch_pages(monkeypatch, "search_page1")
        table = run_table_function(WikiSearch, positional=("apache arrow",))
        extras = [None if e is None else json.loads(e) for e in table.column("extra").to_pylist()]
        assert extras[0]["timestamp"] == "2024-11-02T10:00:00Z"
        assert extras[0]["size"] == 24680
        # The third hit has neither timestamp nor size -> extra is NULL.
        assert extras[2] is None


class TestScanStateRoundTrip:
    """The headline test: the sroffset cursor survives serialization across ticks."""

    def test_multi_page_paging_matches(self, monkeypatch) -> None:
        # Follow 2 pages: page1 (3 rows) + page2 (2 rows) = 5 rows. With
        # CHUNK_ROWS=5 emit chunks, this spans page AND batch boundaries.
        _patch_pages(monkeypatch, "search_page1", "search_page2")
        plain = run_table_function(WikiSearch, positional=("apache arrow",), named={"max_pages": 2})

        tables._clear_cache()
        _patch_pages(monkeypatch, "search_page1", "search_page2")
        roundtripped = run_table_function(
            WikiSearch,
            positional=("apache arrow",),
            named={"max_pages": 2},
            serialize_state=True,
        )

        # The serialized-state run yields exactly the same rows in the same order.
        assert plain.column("title").to_pylist() == roundtripped.column("title").to_pylist()
        assert roundtripped.column("title").to_pylist() == [
            "Apache Arrow",
            "Apache Parquet",
            "Columnar database",
            "Data lake",
            "DuckDB",
        ]
        # Both pages were emitted (5 rows total across the sroffset boundary).
        assert roundtripped.num_rows == 5

    def test_small_chunk_spans_batches(self, monkeypatch) -> None:
        # Force a tiny chunk so a single 3-row page spans multiple emit batches,
        # and confirm the cursor round-trips across each batch boundary.
        monkeypatch.setattr(tables, "CHUNK_ROWS", 2)
        _patch_pages(monkeypatch, "search_page1")
        rt = run_table_function(WikiSearch, positional=("apache arrow",), serialize_state=True)
        assert rt.num_rows == 3
        assert rt.column("title").to_pylist() == [
            "Apache Arrow",
            "Apache Parquet",
            "Columnar database",
        ]

    def test_max_pages_bounds_fetch(self, monkeypatch) -> None:
        # max_pages=1 must stop after the first page even though a continuation
        # token is present.
        _patch_pages(monkeypatch, "search_page1", "search_page2")
        table = run_table_function(WikiSearch, positional=("apache arrow",), named={"max_pages": 1})
        assert table.num_rows == 3


class TestWikiSearchErrors:
    def test_empty_query_fails_at_bind(self) -> None:
        with pytest.raises(ValueError, match="non-empty query"):
            run_table_function(WikiSearch, positional=("   ",))

    def test_client_error_surfaces_clean(self, monkeypatch) -> None:
        def boom(cls, params, sroffset):
            raise WikiError("MediaWiki API error: simulated")

        monkeypatch.setattr(WikiSearch, "_fetch_page", classmethod(boom))
        with pytest.raises(WikiError, match="simulated"):
            run_table_function(WikiSearch, positional=("apache arrow",))


class TestWikiPageSummary:
    def test_returns_row(self, monkeypatch) -> None:
        from vgi_wikipedia import tables_page

        def fake_summary(self, title, *, lang):
            return _load("summary")

        monkeypatch.setattr(tables_page.WikiClient, "summary", fake_summary)
        table = run_table_function(WikiPageSummary, positional=("DuckDB",))
        assert table.column_names == ["title", "extract", "url", "thumbnail_url", "pageid"]
        assert table.num_rows == 1
        assert table.column("title").to_pylist() == ["DuckDB"]
        assert table.column("pageid").to_pylist() == [66475209]

    def test_missing_page_zero_rows(self, monkeypatch) -> None:
        from vgi_wikipedia import tables_page

        monkeypatch.setattr(tables_page.WikiClient, "summary", lambda self, title, *, lang: None)
        table = run_table_function(WikiPageSummary, positional=("Nonexistent",))
        assert table.num_rows == 0

    def test_empty_title_fails_at_bind(self) -> None:
        with pytest.raises(ValueError, match="non-empty title"):
            run_table_function(WikiPageSummary, positional=("",))
