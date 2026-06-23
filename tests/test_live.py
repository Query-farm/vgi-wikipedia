"""Gated live smoke tests against the REAL Wikipedia API (free, no key).

These hit ``en.wikipedia.org`` over the network and are marked ``live`` so they
are excluded from the default CI gate (``-m "not live"``). Run them with
``make test-live`` or ``pytest -m live`` when you want to confirm the worker
talks to the real API. Assertions are loose (article content drifts over time):
we only check that *something* sensible comes back.
"""

from __future__ import annotations

import pytest

from vgi_wikipedia.client import WikiClient
from vgi_wikipedia.parse import parse_search, parse_summary

pytestmark = pytest.mark.live


def test_live_search() -> None:
    client = WikiClient()  # default: real Wikipedia
    payload = client.search("apache arrow", lang="en", count=5, sroffset=0)
    rows, _ = parse_search(payload, lang="en")
    assert rows, "expected at least one live search result"
    assert all(r.snippet is None or "<" not in r.snippet for r in rows)
    assert any(r.pageid for r in rows)


def test_live_summary() -> None:
    client = WikiClient()
    page = parse_summary(client.summary("DuckDB", lang="en"))
    assert page is not None
    assert page.extract
    assert page.url and "DuckDB" in page.url
