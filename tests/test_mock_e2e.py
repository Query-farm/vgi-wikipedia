"""Mock-server E2E: real HTTP round-trip against canned MediaWiki responses.

The :class:`~vgi_wikipedia.client.WikiClient`'s ``api_url`` is pointed at a local
:class:`MockServer` serving fixture JSON, so the full httpx path (request
building, status handling, retry, JSON parsing) runs deterministically -- no
keys, no cost, no real network. Also covers the bounded-retry and clean-error
paths, and the REST-summary + Action-extract fallback.
"""

from __future__ import annotations

import pytest

from tests.mock_server import MockServer
from vgi_wikipedia.client import WikiClient, WikiError
from vgi_wikipedia.parse import parse_search, parse_summary


@pytest.fixture()
def server():
    with MockServer() as srv:
        yield srv


def _client(server) -> WikiClient:
    return WikiClient(api_url=server.api_url)


def test_search_page1_e2e(server) -> None:
    payload = _client(server).search("apache arrow", lang="en", count=10, sroffset=0)
    rows, next_off = parse_search(payload, lang="en")
    assert [r.title for r in rows] == ["Apache Arrow", "Apache Parquet", "Columnar database"]
    assert next_off == 3
    # Snippets are plain text after the round-trip.
    assert all(r.snippet is None or "<" not in r.snippet for r in rows)


def test_search_page2_exhausts(server) -> None:
    payload = _client(server).search("apache arrow", lang="en", count=10, sroffset=3)
    rows, next_off = parse_search(payload, lang="en")
    assert [r.title for r in rows] == ["Data lake", "DuckDB"]
    assert next_off is None


def test_summary_e2e(server) -> None:
    page = parse_summary(_client(server).summary("DuckDB", lang="en"))
    assert page is not None
    assert page.title == "DuckDB"
    assert page.url == "https://en.wikipedia.org/wiki/DuckDB"
    assert page.thumbnail_url.endswith("320px-DuckDB_logo.svg.png")


def test_summary_missing_returns_none(server) -> None:
    assert _client(server).summary("Nonexistent Page Zzzz", lang="en") is None


def test_summary_minimal_nulls(server) -> None:
    page = parse_summary(_client(server).summary("Sparse Page", lang="en"))
    assert page is not None
    assert page.thumbnail_url is None


def test_clean_error_on_5xx(server) -> None:
    client = _client(server)
    with pytest.raises(WikiError, match="HTTP 500"):
        client._request("GET", f"{server.base}/boom")


def test_bounded_retry_then_success() -> None:
    # 2 x 503 then 200: the bounded retry (3 attempts) recovers.
    with MockServer(flaky=2) as srv:
        client = WikiClient(api_url=srv.api_url)
        resp = client._request("GET", f"{srv.base}/flaky")
        assert resp.status_code == 200


def test_retry_exhausted_raises() -> None:
    # 5 x 503 exhausts the 3 attempts -> clean WikiError.
    with MockServer(flaky=5) as srv:
        client = WikiClient(api_url=srv.api_url)
        with pytest.raises(WikiError):
            client._request("GET", f"{srv.base}/flaky")
