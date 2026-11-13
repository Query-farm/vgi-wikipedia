"""Structured-data semantics, bounded HTTP behavior and portable result snapshots."""

from __future__ import annotations

import copy
import json
from datetime import UTC, datetime, timedelta
from email.utils import format_datetime
from pathlib import Path

import httpx
import pytest

from tests.harness import run_table_function
from tests.mock_server import MockServer
from vgi_wikipedia.client import WikiError
from vgi_wikipedia.wikidata import tables
from vgi_wikipedia.wikidata.client import WikidataClient, clear_cache, endpoint, retry_delay
from vgi_wikipedia.wikidata.parse import entity_rows, snak_row
from vgi_wikipedia.wikidata.sparql import query_form, result
from vgi_wikipedia.wikidata.tables import Entity, Qualifiers, References, Search, Sparql, Statements, WikiEntity


@pytest.fixture(autouse=True)
def clean_response_cache():
    clear_cache()
    yield
    clear_cache()


@pytest.fixture
def server():
    with MockServer() as instance:
        yield instance


def fetch(function, server, positional, **named):
    return run_table_function(
        function, positional=positional, named={"api_url": server.api_url, **named}, serialize_state=True
    )


def entity_fixture():
    return json.loads((Path(__file__).parent / "fixtures/wikidata_q42.json").read_text())["entities"]["Q42"]


def test_entity_batching_missing_redirects_and_exact_language(server):
    ids = ["Q42", "Q999", "Q404", "Q42", *[f"Q{2000 + i}" for i in range(99)]]
    rows = fetch(Entity, server, (ids,), lang="de").to_pylist()
    assert len(rows) == 102
    assert rows[0]["entity_id"] == rows[1]["entity_id"] == "Q42"
    assert rows[0]["label"] == "Douglas Adams" and rows[0]["label_language"] == "mul"
    assert rows[0]["aliases_language"] == "mul"
    assert rows[1]["requested_id"] == "Q999"
    assert rows[2]["missing"] and rows[2]["label"] is None
    assert rows[3]["label"] is None  # No implicit fallback to English.
    requests = [qs for _, qs in server.requests if qs.get("action") == ["wbgetentities"]]
    assert [len(qs["ids"][0].split("|")) for qs in requests] == [50, 50, 2]


def test_article_resolution_redirect_missing_and_no_association(server):
    assert fetch(WikiEntity, server, ("DNA",)).to_pylist()[0]["entity_id"] == "Q42"
    assert fetch(WikiEntity, server, ("absent",)).num_rows == 0
    assert fetch(WikiEntity, server, ("No item",)).to_pylist()[0]["entity_id"] is None


def test_search_continuation_top_n_and_empty(server):
    rows = fetch(Search, server, ("Douglas Adams",), count=75).to_pylist()
    assert len(rows) == 75 and rows[0]["entity_id"] == "Q42"
    assert len({r["entity_id"] for r in rows}) == 75
    assert [qs["continue"] for _, qs in server.requests] == [["0"], ["50"]]
    assert fetch(Search, server, ("absent",)).num_rows == 0


def test_statement_qualifier_reference_join_keys_and_cache(server):
    statements = fetch(Statements, server, (["Q42"],), property_id="P26").to_pylist()
    qualifiers = fetch(Qualifiers, server, (["Q42"],), property_id="P26").to_pylist()
    references = fetch(References, server, (["Q42"],), property_id="P26").to_pylist()
    assert len(statements) == 1 and len(qualifiers) == 3 and len(references) > 0
    statement = statements[0]
    assert statement["value"]["entity_id"] == "Q14623681"
    assert all(r["statement_id"] == statement["statement_id"] for r in qualifiers + references)
    assert all(r["entity_revision"] == statement["entity_revision"] for r in qualifiers + references)
    assert {r["property_id"] for r in qualifiers} == {"P580", "P1534", "P582"}
    assert len({r["reference_hash"] for r in references}) == 1
    assert len(server.requests) == 1  # Shared full-entity cache across representations.


@pytest.mark.parametrize("kind", ["somevalue", "novalue"])
def test_unknown_and_absent_values_are_distinct(kind):
    row = snak_row({"property": "P1", "snaktype": kind, "datatype": "string"})
    assert row["value"] is None and row["snak_type"] == kind


@pytest.mark.parametrize(
    ("datatype", "value", "expected"),
    [
        (
            "quantity",
            {"amount": "+12345678901234567890.123456789", "lowerBound": "+1", "upperBound": "+2", "unit": "urn:unit"},
            {"amount": "+12345678901234567890.123456789", "unit": "urn:unit", "lower_bound": "+1"},
        ),
        (
            "time",
            {
                "time": "-0000000044-00-00T00:00:00Z",
                "precision": 9,
                "before": 2,
                "after": 3,
                "timezone": 0,
                "calendarmodel": "urn:julian",
            },
            {"time": "-0000000044-00-00T00:00:00Z", "time_precision": 9, "calendar_model": "urn:julian"},
        ),
        ("monolingualtext", {"text": "Grüße", "language": "de"}, {"text": "Grüße", "language": "de"}),
        (
            "globecoordinate",
            {"latitude": 1.5, "longitude": 2.5, "precision": 0.01, "globe": "urn:mars"},
            {"latitude": 1.5, "coordinate_precision": 0.01, "globe": "urn:mars"},
        ),
        ("wikibase-entityid", {"entity-type": "item", "numeric-id": 42}, {"entity_id": "Q42"}),
        ("future-type", {"nested": [1, "x"]}, {}),
    ],
)
def test_typed_values_preserve_precision_context_and_raw_data(datatype, value, expected):
    data = {"type": datatype, "value": value}
    parsed = snak_row({"property": "P1", "snaktype": "value", "datatype": datatype, "datavalue": data})["value"]
    assert all(parsed[k] == v for k, v in expected.items())
    assert json.loads(parsed["raw_json"]) == data


def test_rank_selection_is_per_property_and_retains_deprecated_by_default():
    entity = entity_fixture()
    statement = entity["claims"]["P31"][0]
    entity["claims"]["P31"] = [
        {**copy.deepcopy(statement), "id": rank, "rank": rank} for rank in ["normal", "preferred", "deprecated"]
    ]

    def ranks(selection):
        return [r["rank"] for r in entity_rows([("Q42", entity)], "urn:source", "en", "statements", "P31", selection)]

    assert ranks("all") == ["normal", "preferred", "deprecated"]
    assert ranks("nondeprecated") == ["normal", "preferred"]
    assert ranks("best") == ["preferred"]
    entity["claims"]["P31"] = [s for s in entity["claims"]["P31"] if s["rank"] != "preferred"]
    assert ranks("best") == ["normal"]


def test_cache_bypass_and_result_limits(server):
    fetch(Entity, server, (["Q42"],))
    fetch(Entity, server, (["Q42"],))
    assert len(server.requests) == 1
    fetch(Entity, server, (["Q42"],), cache_seconds=0)
    assert len(server.requests) == 2
    with pytest.raises(WikiError, match="max_rows"):
        fetch(Statements, server, (["Q42"],), max_rows=1)
    with pytest.raises(WikiError, match="max_bytes"):
        fetch(Statements, server, (["Q42"],), max_bytes=1024)


@pytest.mark.parametrize("ids", [[], ["Q0"], ["Q1|Q2"], [None], ["L1"], ["Q1"] * 501])
def test_invalid_ids_fail_before_network(ids):
    with pytest.raises((ValueError, TypeError)):
        run_table_function(Entity, positional=(ids,))


@pytest.mark.parametrize(
    "query",
    [
        "INSERT DATA { <urn:s> <urn:p> 1 }",
        "DELETE WHERE {?s ?p ?o}",
        "LOAD <https://example.org>",
        "CONSTRUCT {?s ?p ?o} WHERE {?s ?p ?o}",
        "SELECT ?x WHERE {} ; DROP ALL",
        "nonsense",
        "",
    ],
)
def test_sparql_rejects_mutations_and_unsupported_forms(query):
    with pytest.raises(ValueError):
        query_form(query)


def test_sparql_forms_terms_paths_and_unbound(server):
    url = server.base + "/sparql"

    def query(text, **named):
        return run_table_function(
            Sparql, positional=(text,), named={"endpoint_url": url, **named}, serialize_state=True
        )

    assert query("ASK { <http://www.wikidata.org/entity/Q42> ?p ?o }").to_pylist() == [{"answer": True}]
    rows = query(
        'SELECT ?iri ?n ?label ?missing WHERE { VALUES (?iri ?n ?label) { (<urn:s> 42 "Grüße"@de) } '
        "OPTIONAL { <urn:absent> <urn:p> ?missing } }"
    ).to_pylist()
    assert rows[0]["iri"]["kind"] == "iri"
    assert rows[0]["n"]["datatype"].endswith("#integer")
    assert rows[0]["label"]["language"] == "de" and rows[0]["missing"] is None
    rows = query(
        "PREFIX wd: <http://www.wikidata.org/entity/> PREFIX wdt: <http://www.wikidata.org/prop/direct/> "
        "SELECT ?ancestor WHERE { wd:Q42 wdt:P31/wdt:P279* ?ancestor } ORDER BY ?ancestor"
    ).to_pylist()
    assert len(rows) == 2
    assert query("SELECT ?x WHERE { <urn:absent> <urn:p> ?x }").num_rows == 0
    with pytest.raises(WikiError, match="max_rows"):
        query("SELECT ?x WHERE {VALUES ?x {1 2}}", max_rows=1)


def test_sparql_result_survives_many_batches_without_refetch(server, monkeypatch):
    monkeypatch.setattr(tables, "CHUNK_ROWS", 17)
    query = "SELECT ?x WHERE { VALUES ?x { " + " ".join(str(i) for i in range(1200)) + " } }"
    table = run_table_function(
        Sparql, positional=(query,), named={"endpoint_url": server.base + "/sparql"}, serialize_state=True
    )
    assert table.num_rows == 1200
    assert {int(r["x"]["value"]) for r in table.to_pylist()} == set(range(1200))
    assert len(server.requests) == 1


@pytest.mark.parametrize(
    "payload",
    [
        {"head": {"vars": ["x", "X"]}, "results": {"bindings": []}},
        {"head": {"vars": ["x"]}},
        {"head": {"vars": ["x"]}, "results": {"bindings": [{"x": {"type": "unknown", "value": "x"}}]}},
    ],
)
def test_malformed_sparql_results_fail(payload):
    with pytest.raises(WikiError):
        result(payload, "SelectQuery", 100)


def mock_http(monkeypatch, handler):
    original = httpx.Client
    monkeypatch.setattr(httpx, "Client", lambda **kwargs: original(transport=httpx.MockTransport(handler), **kwargs))


def test_http_retries_honor_retry_after_and_api_maxlag(monkeypatch):
    sleeps = []
    monkeypatch.setattr("vgi_wikipedia.wikidata.client.time.sleep", sleeps.append)
    responses = [
        httpx.Response(429, headers={"Retry-After": "1"}),
        httpx.Response(200, json={"error": {"code": "maxlag"}}),
        httpx.Response(200, json={"ok": True}),
    ]
    mock_http(monkeypatch, lambda r: responses.pop(0))
    assert WikidataClient(cache_seconds=0).request("https://test.invalid/api", {}) == {"ok": True}
    assert sleeps == [1.0, 0.5]


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(206, json={"ok": True}),
        httpx.Response(200, headers={"X-SPARQL-MaxRows": "100"}, json={"ok": True}),
        httpx.Response(200, json={"warnings": {"main": "truncated"}}),
        httpx.Response(200, content=b'{"head":'),
        httpx.Response(200, json={"error": {"code": "badvalue"}}),
    ],
)
def test_http_rejects_partial_malformed_and_error_responses(monkeypatch, response):
    mock_http(monkeypatch, lambda r: response)
    with pytest.raises(WikiError):
        WikidataClient(cache_seconds=0).request("https://test.invalid/api", {})


def test_retry_after_beyond_budget_fails_without_sleep(monkeypatch):
    mock_http(monkeypatch, lambda r: httpx.Response(429, headers={"Retry-After": "3600"}))
    with pytest.raises(WikiError, match="retry budget"):
        WikidataClient(timeout_ms=10).request("https://test.invalid/api", {})
    assert 8 <= retry_delay(format_datetime(datetime.now(UTC) + timedelta(seconds=10)), 0) <= 10


@pytest.mark.parametrize(
    "url", ["file:///tmp/test", "https://name:password@example.org/", "https://example.org/#fragment"]
)
def test_invalid_endpoint(url):
    with pytest.raises(ValueError):
        endpoint(url, "UNUSED_TEST_VARIABLE", "")


def test_cache_expires_and_cache_hits_respect_operation_deadline(monkeypatch):
    now = [100.0]
    requests = []
    monkeypatch.setattr("vgi_wikipedia.wikidata.client.time.monotonic", lambda: now[0])

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json={"generation": len(requests)})

    mock_http(monkeypatch, respond)
    first = WikidataClient(cache_seconds=5).request("https://test.invalid/cache", {})
    now[0] += 4
    assert WikidataClient(cache_seconds=5).request("https://test.invalid/cache", {}) == first
    expired_operation = WikidataClient(timeout_ms=100, cache_seconds=5)
    now[0] += 0.2
    with pytest.raises(WikiError, match="timeout_ms"):
        expired_operation.request("https://test.invalid/cache", {})
    now[0] += 2
    assert WikidataClient(cache_seconds=5).request("https://test.invalid/cache", {}) == {"generation": 2}


def test_read_timeout_is_retried_within_budget(monkeypatch):
    attempts = []
    monkeypatch.setattr("vgi_wikipedia.wikidata.client.time.sleep", lambda delay: None)

    def respond(request):
        attempts.append(request)
        raise httpx.ReadTimeout("timed out", request=request)

    mock_http(monkeypatch, respond)
    with pytest.raises(WikiError, match="ReadTimeout.*retry budget"):
        WikidataClient(cache_seconds=0).request("https://test.invalid/timeout", {})
    assert len(attempts) == 3


def test_api_maxlag_retry_after_is_respected(monkeypatch):
    sleeps = []
    monkeypatch.setattr("vgi_wikipedia.wikidata.client.time.sleep", sleeps.append)
    responses = [
        httpx.Response(200, headers={"Retry-After": "2"}, json={"error": {"code": "maxlag"}}),
        httpx.Response(200, json={"ok": True}),
    ]
    mock_http(monkeypatch, lambda request: responses.pop(0))
    assert WikidataClient(cache_seconds=0).request("https://test.invalid/maxlag", {}) == {"ok": True}
    assert sleeps == [2.0]


def test_search_continuation_cycle_is_an_error(monkeypatch):
    monkeypatch.setattr(WikidataClient, "request", lambda *args, **kwargs: {"search": [], "search-continue": 0})
    with pytest.raises(WikiError, match="continuation cycle"):
        run_table_function(Search, positional=("anything",))


def test_entity_language_overrides_shared_defaults():
    entity = entity_fixture()
    entity["labels"]["en"] = {"language": "en", "value": "English name"}
    entity["aliases"]["en"] = [{"language": "en", "value": "English alias"}]
    row = entity_rows([("Q42", entity)], "https://test.invalid", "en", "entity")[0]
    assert row["label"] == "English name" and row["label_language"] == "en"
    assert row["aliases"] == ["English alias"] and row["aliases_language"] == "en"


def test_blank_node_identity_and_lexical_values_are_preserved():
    table = result(
        {
            "head": {"vars": ["node", "number"]},
            "results": {
                "bindings": [
                    {
                        "node": {"type": "bnode", "value": "local-id"},
                        "number": {
                            "type": "literal",
                            "value": "001.2300",
                            "datatype": "http://www.w3.org/2001/XMLSchema#decimal",
                        },
                    }
                ]
            },
        },
        "SelectQuery",
        10,
    )
    row = table.to_pylist()[0]
    assert row["node"]["kind"] == "blank" and row["node"]["value"] == "local-id"
    assert row["number"]["value"] == "001.2300"
