"""Composed examples are published on the schema and on their driving object."""

from __future__ import annotations

import json

from vgi_wikipedia import composed
from vgi_wikipedia.tables import WikiSearch
from vgi_wikipedia.wikidata.tables import WIKIDATA_SCHEMA, Qualifiers, References, Statements, WikiEntity
from vgi_wikipedia.worker import _SCHEMA_EXAMPLE_QUERIES


def _sqls(example_queries: str) -> list[str]:
    return [e["sql"] for e in json.loads(example_queries)]


def test_schema_lists_carry_composed_examples() -> None:
    main = _sqls(_SCHEMA_EXAMPLE_QUERIES)
    wikidata = _sqls(WIKIDATA_SCHEMA.tags["vgi.example_queries"])
    assert all(e["sql"] in main for e in composed.MAIN_SCHEMA_COMPOSED)
    assert all(e["sql"] in wikidata for e in composed.WIKIDATA_SCHEMA_COMPOSED)


def test_driving_object_shows_composed_example_in_first_five() -> None:
    for function, example in [
        (WikiSearch, composed.SEARCH_THEN_SUMMARIZE),
        (WikiEntity, composed.ARTICLE_WITH_ENTITY),
        (Statements, composed.LABELLED_STATEMENTS),
        (Qualifiers, composed.SPOUSE_QUALIFIERS),
        (References, composed.SOURCES_PER_STATEMENT),
    ]:
        assert example["sql"] in [e.sql for e in function.Meta.examples[:5]]
        assert example["sql"] in _sqls(function.Meta.tags["vgi.example_queries"])[:5]
        assert example["sql"] not in function.Meta.tags["vgi.executable_examples"]
