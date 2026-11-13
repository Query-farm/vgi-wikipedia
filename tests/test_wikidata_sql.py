"""Real stock DuckDB/VGI binding, typed values, joins and graph paths."""

from __future__ import annotations

import sys

import duckdb
import pytest

from tests.mock_server import MockServer


def literal(value: str) -> str:
    return "'" + value.replace("'", "''") + "'"


@pytest.fixture(scope="module")
def sql():
    with MockServer() as server, duckdb.connect() as connection:
        connection.execute("SET allow_extension_repositories='allowed'")
        connection.execute(
            "CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm WITH PREFIX 'https://duckdb-extensions.query.farm'"
        )
        connection.execute("INSTALL vgi FROM query_farm")
        connection.execute("LOAD vgi FROM query_farm")
        command = str(sys.executable) + " -m vgi_wikipedia.worker"
        connection.execute(f"ATTACH 'wiki' (TYPE vgi, LOCATION {literal(command)})")
        yield connection, server
        connection.execute("DETACH wiki")


def test_article_entity_and_local_sql_join(sql):
    c, s = sql
    assert c.execute(
        f"SELECT entity_id FROM wiki.main.wiki_entity('DNA', api_url := {literal(s.api_url)})"
    ).fetchall() == [("Q42",)]
    c.execute("CREATE TEMP TABLE reading_list(id VARCHAR, rating INTEGER)")
    c.execute("INSERT INTO reading_list VALUES ('Q42',5),('Q404',0)")
    assert c.execute(
        "SELECT e.label,r.rating FROM reading_list r JOIN wiki.wikidata.entity(['Q42','Q404'], "
        f"api_url := {literal(s.api_url)}) e ON r.id=e.requested_id WHERE NOT e.missing"
    ).fetchall() == [("Douglas Adams", 5)]


def test_statement_context_and_nested_types(sql):
    c, s = sql
    options = f"['Q42'], api_url := {literal(s.api_url)}, property_id := 'P26'"
    assert c.execute(f"SELECT value.entity_id,rank FROM wiki.wikidata.statements({options})").fetchall() == [
        ("Q14623681", "normal")
    ]
    assert c.execute(
        f"SELECT q.property_id FROM wiki.wikidata.statements({options}) s "
        f"JOIN wiki.wikidata.qualifiers({options}) q USING (statement_id,entity_revision,source_api) "
        "ORDER BY q.property_id"
    ).fetchall() == [("P1534",), ("P580",), ("P582",)]
    assert c.execute(
        f"SELECT count(DISTINCT reference_hash) FROM wiki.wikidata.statement_references({options})"
    ).fetchone() == (1,)
    assert c.execute(
        f"SELECT value.time,value.time_precision FROM wiki.wikidata.statements(['Q42'], "
        f"api_url := {literal(s.api_url)}, property_id := 'P569')"
    ).fetchone() == ("+1952-03-11T00:00:00Z", 11)


def test_sparql_dynamic_structs_ask_empty_and_path_queries(sql):
    c, s = sql
    endpoint = literal(s.base + "/sparql")
    query = (
        'SELECT ?v ?label ?missing WHERE { VALUES (?v ?label) { (42 "Adams"@en) } '
        "OPTIONAL { <urn:absent> <urn:p> ?missing } }"
    )
    assert c.execute(
        f"SELECT v.value,v.datatype,label.language,missing IS NULL "
        f"FROM wiki.wikidata.sparql({literal(query)}, endpoint_url := {endpoint})"
    ).fetchall() == [("42", "http://www.w3.org/2001/XMLSchema#integer", "en", True)]
    assert c.execute(f"SELECT answer FROM wiki.wikidata.sparql('ASK {{}}', endpoint_url := {endpoint})").fetchone() == (
        True,
    )
    assert c.execute(
        f"SELECT count(*) FROM wiki.wikidata.sparql('SELECT ?v WHERE {{<urn:absent> <urn:p> ?v}}', "
        f"endpoint_url := {endpoint})"
    ).fetchone() == (0,)
    path = (
        "SELECT ?ancestor WHERE { <http://www.wikidata.org/entity/Q42> "
        "<http://www.wikidata.org/prop/direct/P31>/<http://www.wikidata.org/prop/direct/P279>* ?ancestor }"
    )
    assert c.execute(
        f"SELECT count(*) FROM wiki.wikidata.sparql({literal(path)}, endpoint_url := {endpoint})"
    ).fetchone() == (2,)


def test_sparql_batches_limits_and_invalid_queries(sql):
    c, s = sql
    endpoint = literal(s.base + "/sparql")
    query = "SELECT ?n WHERE {VALUES ?n { " + " ".join(str(i) for i in range(1200)) + " }}"
    assert c.execute(
        f"SELECT count(*),sum(CAST(n.value AS BIGINT)) "
        f"FROM wiki.wikidata.sparql({literal(query)}, endpoint_url := {endpoint})"
    ).fetchone() == (1200, 719400)
    with pytest.raises(duckdb.Error, match="max_rows"):
        c.execute(f"SELECT * FROM wiki.wikidata.sparql({literal(query)}, endpoint_url := {endpoint}, max_rows := 10)")
    with pytest.raises(duckdb.Error, match="read-only"):
        c.execute(f"SELECT * FROM wiki.wikidata.sparql('DELETE WHERE {{?s ?p ?o}}', endpoint_url := {endpoint})")


def test_search_paging_and_missing_inputs(sql):
    c, s = sql
    assert c.execute(
        f"SELECT count(*) FROM wiki.wikidata.search('Douglas Adams', count := 75, api_url := {literal(s.api_url)})"
    ).fetchone() == (75,)
    assert c.execute(
        f"SELECT count(*) FROM wiki.wikidata.statements(['Q404'], api_url := {literal(s.api_url)})"
    ).fetchone() == (0,)
    with pytest.raises(duckdb.Error, match="Q/P IDs"):
        c.execute(f"SELECT * FROM wiki.wikidata.entity(['invalid'], api_url := {literal(s.api_url)})")
