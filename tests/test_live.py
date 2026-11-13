"""Gated live smoke tests against the REAL Wikipedia API (free, no key).

These hit ``en.wikipedia.org`` over the network and are marked ``live`` so they
are excluded from the default CI gate (``-m "not live"``). Run them with
``make test-live`` or ``pytest -m live`` when you want to confirm the worker
talks to the real API. Assertions are loose (article content drifts over time):
we only check that *something* sensible comes back.
"""

from __future__ import annotations

import pytest

from tests.harness import run_table_function
from vgi_wikipedia.client import WikiClient
from vgi_wikipedia.parse import parse_search, parse_summary
from vgi_wikipedia.wikidata.tables import Entity, Sparql, Statements, WikiEntity

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


def test_live_wikidata_article_entity_and_statement() -> None:
    article = run_table_function(WikiEntity, positional=("Douglas Adams",)).to_pylist()[0]
    assert article["entity_id"] == "Q42"
    entity = run_table_function(Entity, positional=(["Q42"],)).to_pylist()[0]
    assert entity["label"] and entity["entity_revision"] > 0
    statements = run_table_function(Statements, positional=(["Q42"],), named={"property_id": "P31"}).to_pylist()
    assert any(row["value"]["entity_id"] == "Q5" for row in statements)


def test_live_wikidata_sparql() -> None:
    query = "ASK { <http://www.wikidata.org/entity/Q42> <http://www.wikidata.org/prop/direct/P31> ?class }"
    assert run_table_function(Sparql, positional=(query,)).to_pylist() == [{"answer": True}]
