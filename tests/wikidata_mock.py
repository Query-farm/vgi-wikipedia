"""Local Wikibase fixtures and an independent RDFLib SPARQL endpoint for SQL tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from rdflib import Graph

FIXTURE = Path(__file__).with_name("fixtures") / "wikidata_q42.json"
GRAPH = """
@prefix wd: <http://www.wikidata.org/entity/> .
@prefix wdt: <http://www.wikidata.org/prop/direct/> .
@prefix rdfs: <http://www.w3.org/2000/01/rdf-schema#> .
wd:Q42 wdt:P31 wd:Q5 ; rdfs:label "Douglas Adams"@en ; wdt:P26 wd:Q14623681 .
wd:Q5 wdt:P279 wd:Q215627 .
wd:Q14623681 rdfs:label "Jane Belson"@en .
"""


def action(parameters: dict[str, list[str]]) -> dict[str, Any] | None:
    name = parameters.get("action", [""])[0]
    if name == "wbgetentities":
        fixture = json.loads(FIXTURE.read_text())["entities"]["Q42"]
        entities = {}
        redirects = []
        for identifier in parameters.get("ids", [""])[0].split("|"):
            if identifier == "Q999":
                redirects.append({"from": "Q999", "to": "Q42"})
                entities["Q42"] = fixture
            elif identifier == "Q42":
                entities[identifier] = fixture
            elif identifier == "Q404":
                entities[identifier] = {"id": identifier, "missing": ""}
            else:
                entities[identifier] = {"id": identifier, "lastrevid": 1, "labels": {"en": {"value": identifier}}}
        return {"entities": entities, "redirects": redirects, "success": 1}
    if name == "wbsearchentities":
        start = int(parameters.get("continue", ["0"])[0])
        limit = int(parameters.get("limit", ["10"])[0])
        if parameters.get("search", [""])[0] == "absent":
            return {"search": [], "success": 1}
        hits = [
            {
                "id": "Q42",
                "label": "Douglas Adams",
                "description": "English writer",
                "concepturi": "http://www.wikidata.org/entity/Q42",
                "match": {"text": "Douglas Adams", "language": "en"},
            }
        ]
        hits.extend({"id": f"Q{1000 + i}", "label": f"Candidate {i}"} for i in range(119))
        result: dict[str, Any] = {"search": hits[start : start + limit], "success": 1}
        if start + limit < len(hits):
            result["search-continue"] = start + limit
        return result
    if name == "query" and "pageprops" in parameters.get("prop", [""])[0]:
        title = parameters.get("titles", [""])[0]
        if title in {"Douglas Adams", "Douglas_Adams", "DNA"}:
            page = {
                "title": "Douglas Adams",
                "pageid": 8091,
                "pageprops": {"wikibase_item": "Q42"},
                "fullurl": "https://en.wikipedia.org/wiki/Douglas_Adams",
            }
        elif title == "No item":
            page = {"title": title, "pageid": 12}
        else:
            page = {"title": title, "missing": True}
        return {"query": {"pages": [page]}}
    return None


def sparql(query: str) -> bytes:
    graph = Graph().parse(data=GRAPH, format="turtle")
    return graph.query(query).serialize(format="json")
