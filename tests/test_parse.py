"""Fixture parser unit tests: MediaWiki JSON -> the unified schema (no network).

Covers the Action-API ``list=search`` mapping (including HTML-snippet stripping,
the ``sroffset`` continuation token, and missing-field -> NULL) and the REST
page-summary mapping. We call the pure :mod:`vgi_wikipedia.parse` helpers on the
canned payloads directly.
"""

from __future__ import annotations

import json
from pathlib import Path

from vgi_wikipedia.parse import (
    page_url,
    parse_search,
    parse_summary,
    strip_html,
)

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


class TestStripHtml:
    def test_strips_searchmatch_spans(self) -> None:
        assert strip_html('Apache <span class="searchmatch">Arrow</span> is') == "Apache Arrow is"

    def test_strips_bold_and_unescapes_entities(self) -> None:
        assert strip_html("Arrow <b>columnar</b> &amp; Spark") == "Arrow columnar & Spark"

    def test_collapses_whitespace(self) -> None:
        assert strip_html("a\n  b\t c") == "a b c"

    def test_none_passes_through(self) -> None:
        assert strip_html(None) is None

    def test_plain_text_unchanged(self) -> None:
        assert strip_html("no markup here") == "no markup here"


class TestPageUrl:
    def test_builds_canonical_url(self) -> None:
        assert page_url("Apache Arrow", "en") == "https://en.wikipedia.org/wiki/Apache_Arrow"

    def test_respects_lang(self) -> None:
        assert page_url("DuckDB", "de") == "https://de.wikipedia.org/wiki/DuckDB"

    def test_percent_encodes(self) -> None:
        assert page_url("C++", "en") == "https://en.wikipedia.org/wiki/C%2B%2B"

    def test_none_title(self) -> None:
        assert page_url(None, "en") is None


class TestParseSearch:
    def test_maps_rows_to_unified_schema(self) -> None:
        rows, next_off = parse_search(_load("search_page1"), lang="en")
        assert len(rows) == 3
        first = rows[0]
        assert first.title == "Apache Arrow"
        assert first.pageid == 49371158
        assert first.wordcount == 2456
        assert first.lang == "en"
        assert first.url == "https://en.wikipedia.org/wiki/Apache_Arrow"

    def test_snippet_is_plain_text(self) -> None:
        rows, _ = parse_search(_load("search_page1"), lang="en")
        for r in rows:
            assert r.snippet is None or "<" not in r.snippet
        assert rows[0].snippet == (
            "Apache Arrow is a language-agnostic software framework for developing "
            "data analytics applications that process columnar data."
        )
        # The HTML entity in row 2's snippet is unescaped.
        assert "&" in rows[1].snippet and "&amp;" not in rows[1].snippet

    def test_continuation_token(self) -> None:
        _, next_off = parse_search(_load("search_page1"), lang="en")
        assert next_off == 3

    def test_exhausted_when_no_continue(self) -> None:
        _, next_off = parse_search(_load("search_page2"), lang="en")
        assert next_off is None

    def test_missing_fields_become_null(self) -> None:
        rows, _ = parse_search(_load("search_page1"), lang="en")
        # The third hit omits size and timestamp.
        third = rows[2]
        assert third.title == "Columnar database"
        assert third.size is None
        assert third.timestamp is None
        assert third.wordcount == 543

    def test_empty_search_list(self) -> None:
        rows, next_off = parse_search({"query": {"search": []}}, lang="en")
        assert rows == []
        assert next_off is None


class TestParseSummary:
    def test_maps_full_summary(self) -> None:
        page = parse_summary(_load("summary"))
        assert page is not None
        assert page.title == "DuckDB"
        assert page.pageid == 66475209
        assert page.extract.startswith("DuckDB is an open-source")
        assert page.url == "https://en.wikipedia.org/wiki/DuckDB"
        assert page.thumbnail_url.endswith("320px-DuckDB_logo.svg.png")

    def test_missing_thumbnail_and_url_become_null(self) -> None:
        page = parse_summary(_load("summary_minimal"))
        assert page is not None
        assert page.title == "Sparse Page"
        assert page.extract.startswith("A page whose summary")
        assert page.thumbnail_url is None
        assert page.url is None

    def test_none_payload_returns_none(self) -> None:
        assert parse_summary(None) is None

    def test_action_extract_fallback_shape(self) -> None:
        # The normalized Action-API extract shape (content_urls.desktop.page).
        normalized = {
            "title": "DuckDB",
            "extract": "Some extract.",
            "pageid": 1,
            "content_urls": {"desktop": {"page": "https://en.wikipedia.org/wiki/DuckDB"}},
            "thumbnail": {"source": "https://example.com/t.png"},
        }
        page = parse_summary(normalized)
        assert page.url == "https://en.wikipedia.org/wiki/DuckDB"
        assert page.thumbnail_url == "https://example.com/t.png"
