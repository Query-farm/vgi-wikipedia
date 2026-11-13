"""Read-only SPARQL parsing and lossless SELECT/ASK result conversion."""

from __future__ import annotations

from typing import Any

import pyarrow as pa
from rdflib.plugins.sparql.parser import parseQuery

from vgi_wikipedia.client import WikiError
from vgi_wikipedia.schema_utils import field
from vgi_wikipedia.wikidata.parse import TERM_TYPE


def query_form(query: str) -> str:
    """Parse the complete query, allowing only SELECT and ASK on the query endpoint."""
    if not query.strip() or len(query.encode()) > 65536:
        raise ValueError("SPARQL query must contain 1 to 65536 UTF-8 bytes")
    try:
        form = str(parseQuery(query)[1].name)
    except Exception as error:
        raise ValueError("Invalid read-only SPARQL query") from error
    if form not in {"SelectQuery", "AskQuery"}:
        raise ValueError("Only SPARQL SELECT and ASK are supported")
    return form


def term(value: dict[str, Any]) -> dict[str, str | None]:
    """Keep RDF term kind, lexical value, datatype and language distinct."""
    if not isinstance(value, dict):
        raise WikiError("Invalid SPARQL result term")
    kind = value.get("type")
    lexical = value.get("value")
    if kind not in {"uri", "bnode", "literal", "typed-literal"} or not isinstance(lexical, str):
        raise WikiError("Invalid SPARQL result term")
    language = value.get("xml:lang")
    datatype = value.get("datatype")
    if (language is not None and not isinstance(language, str)) or (
        datatype is not None and not isinstance(datatype, str)
    ):
        raise WikiError("Invalid SPARQL literal metadata")
    if kind in {"uri", "bnode"} and (language is not None or datatype is not None):
        raise WikiError("Non-literal SPARQL term has literal metadata")
    if kind in {"literal", "typed-literal"} and datatype is None:
        datatype = (
            "http://www.w3.org/1999/02/22-rdf-syntax-ns#langString"
            if language
            else "http://www.w3.org/2001/XMLSchema#string"
        )
    return {
        "kind": {"uri": "iri", "bnode": "blank"}.get(kind, "literal"),
        "value": lexical,
        "datatype": datatype,
        "language": language,
    }


def result(payload: dict[str, Any], form: str, max_rows: int) -> pa.Table:
    """Validate a complete SPARQL JSON result and construct its dynamic Arrow schema."""
    if form == "AskQuery":
        if not isinstance(payload.get("boolean"), bool):
            raise WikiError("SPARQL endpoint did not return an ASK boolean")
        return pa.Table.from_pylist(
            [{"answer": payload["boolean"]}],
            schema=pa.schema([field("answer", pa.bool_(), "Whether the SPARQL pattern matches.")]),
        )
    head = payload.get("head")
    results = payload.get("results")
    if not isinstance(head, dict) or not isinstance(results, dict):
        raise WikiError("SPARQL response is missing its complete SELECT result")
    variables = head.get("vars")
    bindings = results.get("bindings")
    if not isinstance(variables, list) or not variables or not all(isinstance(v, str) and v for v in variables):
        raise WikiError("SPARQL response is missing SELECT variable names")
    if len({v.lower() for v in variables}) != len(variables):
        raise WikiError("SPARQL variables collide under SQL case folding")
    if not isinstance(bindings, list):
        raise WikiError("SPARQL response is missing its complete bindings array")
    if len(bindings) > max_rows:
        raise WikiError("SPARQL result exceeds max_rows; narrow the query")
    schema = pa.schema([field(v, TERM_TYPE, "RDF term; SQL NULL denotes an unbound variable.") for v in variables])
    rows = []
    for binding in bindings:
        if not isinstance(binding, dict) or set(binding) - set(variables):
            raise WikiError("Invalid SPARQL variable bindings")
        rows.append({v: term(binding[v]) if v in binding else None for v in variables})
    return pa.Table.from_pylist(rows, schema=schema)
