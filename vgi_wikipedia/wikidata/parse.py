"""Lossless Wikibase statement values and explicit Arrow schemas."""

from __future__ import annotations

import json
from typing import Any

import pyarrow as pa

from vgi_wikipedia.client import WikiError
from vgi_wikipedia.schema_utils import field

VALUE_TYPE = pa.struct(
    [
        field("entity_id", pa.string(), "Referenced Wikibase entity ID, when present."),
        field("text", pa.string(), "String or monolingual text value."),
        field("language", pa.string(), "Language of a monolingual text value."),
        field("time", pa.string(), "Original Wikibase time, including BCE and out-of-range years."),
        field("time_precision", pa.int32(), "Wikibase time precision code; no invented day/month precision."),
        field("timezone", pa.int32(), "Wikibase timezone offset in minutes."),
        field("before", pa.int64(), "Time uncertainty before the value."),
        field("after", pa.int64(), "Time uncertainty after the value."),
        field("calendar_model", pa.string(), "Calendar model IRI."),
        field("amount", pa.string(), "Exact decimal quantity as text; CAST explicitly when appropriate."),
        field("lower_bound", pa.string(), "Exact quantity lower bound."),
        field("upper_bound", pa.string(), "Exact quantity upper bound."),
        field("unit", pa.string(), "Quantity unit IRI or 1 for a dimensionless value."),
        field("latitude", pa.float64(), "Coordinate latitude."),
        field("longitude", pa.float64(), "Coordinate longitude."),
        field("coordinate_precision", pa.float64(), "Coordinate precision in degrees."),
        field("globe", pa.string(), "Coordinate globe IRI."),
        field("raw_json", pa.string(), "Complete datavalue JSON, including unrecognized future value types."),
    ]
)
TERM_TYPE = pa.struct(
    [
        field(n, pa.string(), d)
        for n, d in [
            ("kind", "iri, blank, or literal."),
            ("value", "Unmodified RDF lexical value."),
            ("datatype", "Literal datatype IRI, otherwise NULL."),
            ("language", "Literal language, otherwise NULL."),
        ]
    ]
)
COMMON = [
    field("requested_id", pa.string(), "Requested ID, before entity redirects."),
    field("entity_id", pa.string(), "Canonical Wikibase entity ID."),
    field("entity_revision", pa.int64(), "Entity revision returned by the API; use when joining snapshots."),
    field("source_api", pa.string(), "Wikibase API supplying this row."),
]
ENTITY_SCHEMA = pa.schema(
    [
        *COMMON,
        field("missing", pa.bool_(), "True when the requested entity does not exist."),
        field("label", pa.string(), "Label in the requested language, falling back only to the shared mul label."),
        field("label_language", pa.string(), "Actual label language, including mul for a shared label."),
        field("description", pa.string(), "Description in the requested language."),
        field(
            "aliases",
            pa.list_(pa.string()),
            "Aliases in the requested language, falling back only to shared mul aliases.",
        ),
        field("aliases_language", pa.string(), "Actual aliases language; NULL if no aliases are available."),
        field("modified", pa.string(), "Original entity modification timestamp."),
        field("sitelinks_json", pa.string(), "Complete sitelink map as JSON."),
    ]
)
SNAK_FIELDS = [
    field("property_id", pa.string(), "Wikibase property ID."),
    field("snak_type", pa.string(), "value, somevalue (unknown), or novalue (explicitly no value)."),
    field("datatype", pa.string(), "Wikibase property datatype."),
    field("value", VALUE_TYPE, "Typed value components and lossless raw JSON; NULL for somevalue/novalue."),
]
STATEMENT_SCHEMA = pa.schema(
    [
        *COMMON,
        field("statement_id", pa.string(), "Stable statement identifier for joining qualifiers and references."),
        field("rank", pa.string(), "preferred, normal, or deprecated."),
        *SNAK_FIELDS,
    ]
)
QUALIFIER_SCHEMA = pa.schema(
    [
        *COMMON,
        field("statement_id", pa.string(), "Statement that this qualifier qualifies."),
        field("statement_property_id", pa.string(), "Main property of the qualified statement."),
        field("qualifier_index", pa.int64(), "Zero-based position within this qualifier property."),
        field("qualifier_hash", pa.string(), "Upstream qualifier hash, if supplied."),
        *SNAK_FIELDS,
    ]
)
REFERENCE_SCHEMA = pa.schema(
    [
        *COMMON,
        field("statement_id", pa.string(), "Statement supported by this reference."),
        field("statement_property_id", pa.string(), "Main property of the referenced statement."),
        field("reference_index", pa.int64(), "Zero-based reference group position within the statement."),
        field("reference_hash", pa.string(), "Upstream reference group hash; shared by its constituent snaks."),
        field("snak_index", pa.int64(), "Zero-based snak position within this reference property."),
        *SNAK_FIELDS,
    ]
)
SEARCH_SCHEMA = pa.schema(
    [
        field("entity_id", pa.string(), "Matching Wikibase entity ID."),
        field("label", pa.string(), "Entity label returned by search."),
        field("description", pa.string(), "Entity description returned by search."),
        field("url", pa.string(), "Entity concept URI returned by search."),
        field("match_text", pa.string(), "Label or alias that matched."),
        field("match_language", pa.string(), "Language of the matching text."),
    ]
)
PAGE_ENTITY_SCHEMA = pa.schema(
    [
        field("title", pa.string(), "Resolved article title after normalization and redirects."),
        field("pageid", pa.int64(), "MediaWiki page ID."),
        field("entity_id", pa.string(), "Associated wikibase_item ID; NULL if the article has none."),
        field("url", pa.string(), "Canonical article URL."),
        field("lang", pa.string(), "Requested wiki language."),
    ]
)


def snak_row(snak: dict[str, Any]) -> dict[str, Any]:
    """Preserve the distinction between a known value, an unknown value and no value."""
    kind = snak.get("snaktype")
    if kind not in {"value", "somevalue", "novalue"}:
        raise WikiError("Invalid Wikibase snak type")
    result = {"property_id": snak["property"], "snak_type": kind, "datatype": snak.get("datatype"), "value": None}
    if kind != "value":
        return result
    data = snak.get("datavalue")
    if not isinstance(data, dict) or "value" not in data or "type" not in data:
        raise WikiError("Wikibase value snak is missing its datavalue")
    value = data["value"]
    typed: dict[str, Any] = {"raw_json": json.dumps(data, ensure_ascii=False, separators=(",", ":"))}
    if data["type"] == "string":
        typed["text"] = value
    elif isinstance(value, dict):
        mapping = {
            "wikibase-entityid": {"id": "entity_id"},
            "monolingualtext": {"text": "text", "language": "language"},
            "time": {
                "time": "time",
                "precision": "time_precision",
                "timezone": "timezone",
                "before": "before",
                "after": "after",
                "calendarmodel": "calendar_model",
            },
            "quantity": {"amount": "amount", "lowerBound": "lower_bound", "upperBound": "upper_bound", "unit": "unit"},
            "globecoordinate": {
                "latitude": "latitude",
                "longitude": "longitude",
                "precision": "coordinate_precision",
                "globe": "globe",
            },
        }.get(data["type"], {})
        typed.update({target: value.get(source) for source, target in mapping.items()})
        if data["type"] == "wikibase-entityid" and not typed.get("entity_id"):
            prefix = {"item": "Q", "property": "P"}.get(str(value.get("entity-type")))
            if prefix and "numeric-id" in value:
                typed["entity_id"] = prefix + str(value["numeric-id"])
    result["value"] = typed
    return result


def entity_rows(
    entities: list[tuple[str, dict[str, Any]]],
    api_url: str,
    language: str,
    operation: str,
    property_id: str = "",
    rank: str = "all",
) -> list[dict[str, Any]]:
    """Flatten entities or statement context with explicit revision and join keys."""
    rows: list[dict[str, Any]] = []
    for requested, entity in entities:
        common = {
            "requested_id": requested,
            "entity_id": entity.get("id", requested),
            "entity_revision": entity.get("lastrevid"),
            "source_api": api_url,
        }
        if operation == "entity":
            labels = entity.get("labels", {})
            label = labels.get(language, labels.get("mul", {}))
            aliases = entity.get("aliases", {})
            alias_language = language if aliases.get(language) else "mul"
            selected_aliases = aliases.get(alias_language, [])
            rows.append(
                {
                    **common,
                    "missing": "missing" in entity,
                    "label": label.get("value"),
                    "label_language": label.get("language"),
                    "description": entity.get("descriptions", {}).get(language, {}).get("value"),
                    "aliases": [a["value"] for a in selected_aliases],
                    "aliases_language": alias_language if selected_aliases else None,
                    "modified": entity.get("modified"),
                    "sitelinks_json": json.dumps(entity.get("sitelinks", {}), ensure_ascii=False),
                }
            )
            continue
        for prop, statements in entity.get("claims", {}).items():
            if property_id and prop != property_id:
                continue
            best = "preferred" if any(s.get("rank") == "preferred" for s in statements) else "normal"
            for statement in statements:
                actual_rank = statement.get("rank")
                if rank == "best" and actual_rank != best or rank == "nondeprecated" and actual_rank == "deprecated":
                    continue
                base = {**common, "statement_id": statement["id"]}
                if operation == "statements":
                    rows.append({**base, "rank": actual_rank, **snak_row(statement["mainsnak"])})
                elif operation == "qualifiers":
                    for snaks in statement.get("qualifiers", {}).values():
                        for index, snak in enumerate(snaks):
                            rows.append(
                                {
                                    **base,
                                    "statement_property_id": prop,
                                    "qualifier_index": index,
                                    "qualifier_hash": snak.get("hash"),
                                    **snak_row(snak),
                                }
                            )
                elif operation == "references":
                    for ref_index, reference in enumerate(statement.get("references", [])):
                        for snaks in reference.get("snaks", {}).values():
                            for index, snak in enumerate(snaks):
                                rows.append(
                                    {
                                        **base,
                                        "statement_property_id": prop,
                                        "reference_index": ref_index,
                                        "reference_hash": reference.get("hash"),
                                        "snak_index": index,
                                        **snak_row(snak),
                                    }
                                )
    return rows
