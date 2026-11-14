# Copyright 2026 Query Farm LLC - https://query.farm

"""Composed example queries that combine two or more of this worker's functions.

Each example is defined once here and referenced from the schema-level
``vgi.example_queries`` list and from the example list of the object that drives
the query (within its first five examples, which is what the agent simulator
shows). They hit the live public APIs, so they are not executable examples.
"""

from __future__ import annotations

from vgi.metadata import FunctionExample

SEARCH_THEN_SUMMARIZE = {
    "description": (
        "Search English Wikipedia for 'apache arrow' and fetch the lead-paragraph summary "
        "of each of the top 3 matching articles with the wiki_page scalar"
    ),
    "sql": (
        "SELECT h.title, wiki.main.wiki_page(h.title) AS summary "
        "FROM wiki.main.wiki_search('apache arrow', count := 3) AS h "
        "ORDER BY h.title"
    ),
}

ARTICLE_WITH_ENTITY = {
    "description": (
        "Ground an article for RAG: the English summary extract and URL of the DuckDB article "
        "joined by page ID to the Wikidata item that article is linked to"
    ),
    "sql": (
        "SELECT s.title, e.entity_id, s.url, s.extract "
        "FROM wiki.main.wiki_page_summary('DuckDB') AS s "
        "JOIN wiki.main.wiki_entity('DuckDB') AS e USING (pageid) "
        "ORDER BY s.title"
    ),
}

LABELLED_STATEMENTS = {
    "description": (
        "Douglas Adams's (Q42) best-ranked birth/death dates and places, each labelled with "
        "its property's human-readable name by joining statements to the property entities"
    ),
    "sql": (
        "SELECT p.label AS property, coalesce(s.value.time, s.value.entity_id) AS value "
        "FROM wiki.wikidata.statements(['Q42'], rank := 'best') AS s "
        "JOIN wiki.wikidata.entity(['P19', 'P20', 'P569', 'P570']) AS p "
        "ON s.property_id = p.entity_id "
        "ORDER BY s.property_id"
    ),
}

SPOUSE_QUALIFIERS = {
    "description": (
        "Each spouse (P26) of Douglas Adams (Q42) with that statement's qualifiers, such as "
        "the P580 start time and P582 end time of the marriage"
    ),
    "sql": (
        "SELECT s.value.entity_id AS spouse, q.property_id AS qualifier, "
        "coalesce(q.value.time, q.value.entity_id) AS qualifier_value "
        "FROM wiki.wikidata.statements(['Q42'], property_id := 'P26') AS s "
        "JOIN wiki.wikidata.qualifiers(['Q42'], property_id := 'P26') AS q "
        "USING (source_api, entity_revision, requested_id, statement_id) "
        "ORDER BY spouse, qualifier"
    ),
}

SOURCES_PER_STATEMENT = {
    "description": (
        "How many distinct reference groups (sources) back each of Douglas Adams's (Q42) "
        "instance-of (P31) statements; statements with no source count 0"
    ),
    "sql": (
        "SELECT s.statement_id, s.value.entity_id AS class, "
        "count(DISTINCT r.reference_hash) AS sources "
        "FROM wiki.wikidata.statements(['Q42'], property_id := 'P31') AS s "
        "LEFT JOIN wiki.wikidata.statement_references(['Q42'], property_id := 'P31') AS r "
        "USING (source_api, entity_revision, requested_id, statement_id) "
        "GROUP BY s.statement_id, class "
        "ORDER BY s.statement_id"
    ),
}

MAIN_SCHEMA_COMPOSED = [SEARCH_THEN_SUMMARIZE, ARTICLE_WITH_ENTITY]
WIKIDATA_SCHEMA_COMPOSED = [LABELLED_STATEMENTS, SPOUSE_QUALIFIERS, SOURCES_PER_STATEMENT]


def as_function_example(example: dict[str, str]) -> FunctionExample:
    """Convert a ``{description, sql}`` dict into a native ``FunctionExample``."""
    return FunctionExample(sql=example["sql"], description=example["description"])
