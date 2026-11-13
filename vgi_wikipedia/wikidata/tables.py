"""SQL functions for article identity, Wikibase entities and remote graph queries."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Annotated, Any, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.catalog import Schema
from vgi.invocation import BindResponse, GlobalInitResponse
from vgi.metadata import FunctionExample
from vgi.table_function import BindParams, ProcessParams, TableFunctionGenerator
from vgi_rpc import ArrowSerializableDataclass
from vgi_rpc.rpc import OutputCollector

from vgi_wikipedia.client import WikiError, default_api_url
from vgi_wikipedia.wikidata.client import WikidataClient, endpoint
from vgi_wikipedia.wikidata.parse import (
    ENTITY_SCHEMA,
    PAGE_ENTITY_SCHEMA,
    QUALIFIER_SCHEMA,
    REFERENCE_SCHEMA,
    SEARCH_SCHEMA,
    STATEMENT_SCHEMA,
    entity_rows,
)
from vgi_wikipedia.wikidata.sparql import query_form, result

CHUNK_ROWS = 512


@dataclass(frozen=True, kw_only=True)
class Limits:
    """Common bounded request and result options."""

    max_rows: Annotated[
        int, Arg("max_rows", default=10000, ge=1, le=100000, doc="Result row ceiling; overflow fails explicitly.")
    ]
    max_bytes: Annotated[
        int,
        Arg(
            "max_bytes",
            default=16777216,
            ge=1024,
            le=67108864,
            doc="Combined response bytes and Arrow snapshot ceiling.",
        ),
    ]
    timeout_ms: Annotated[
        int,
        Arg(
            "timeout_ms",
            default=30000,
            ge=1,
            le=60000,
            doc="Operation request/retry budget in milliseconds; checked between reads.",
        ),
    ]
    cache_seconds: Annotated[
        int,
        Arg(
            "cache_seconds",
            default=300,
            ge=0,
            le=3600,
            doc="Maximum process-local cached response age; zero bypasses cache.",
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class EntityArgs(Limits):
    """A batch of Wikidata item/property IDs."""

    ids: Annotated[
        list[str], Arg(0, arrow_type=pa.list_(pa.string()), doc="One to 500 Q/P IDs; duplicates are fetched once.")
    ]
    lang: Annotated[
        str,
        Arg(
            "lang",
            default="en",
            doc="Requested language; labels/aliases may use shared mul values, with actual language returned.",
        ),
    ]
    api_url: Annotated[
        str,
        Arg("api_url", default="", doc="Wikibase Action API override; defaults to VGI_WIKIDATA_API_URL or Wikidata."),
    ]


@dataclass(frozen=True, kw_only=True)
class StatementArgs(EntityArgs):
    """Statement selection shared by statements, qualifiers and references."""

    property_id: Annotated[
        str, Arg("property_id", default="", doc="Main statement P ID to select; empty means all properties.")
    ]
    rank: Annotated[
        str,
        Arg(
            "rank",
            default="all",
            choices=["all", "nondeprecated", "best"],
            doc="all; nondeprecated excludes deprecated; best selects preferred or otherwise normal per property.",
        ),
    ]


@dataclass(frozen=True, kw_only=True)
class SearchArgs(Limits):
    """Bounded ranked entity search."""

    query: Annotated[
        str, Arg(0, doc="Label or alias text to search; this is candidate discovery, not automatic identity matching.")
    ]
    lang: Annotated[str, Arg("lang", default="en", doc="Search language.")]
    count: Annotated[
        int, Arg("count", default=10, ge=1, le=500, doc="Requested top-N candidates, following continuation as needed.")
    ]
    entity_type: Annotated[
        str, Arg("entity_type", default="item", choices=["item", "property"], doc="Search items or properties.")
    ]
    api_url: Annotated[str, Arg("api_url", default="", doc="Wikibase Action API override.")]


@dataclass(frozen=True, kw_only=True)
class PageArgs(Limits):
    """Wikipedia article identity lookup."""

    title: Annotated[str, Arg(0, doc="Wikipedia/MediaWiki article title; redirects are resolved.")]
    lang: Annotated[str, Arg("lang", default="en", doc="Wikipedia language edition.")]
    api_url: Annotated[
        str, Arg("api_url", default="", doc="MediaWiki Action API override; uses VGI_WIKIPEDIA_API_URL by default.")
    ]


@dataclass(frozen=True, kw_only=True)
class SparqlArgs(Limits):
    """Read-only graph query using the SPARQL JSON results protocol."""

    query: Annotated[
        str, Arg(0, doc="Complete SPARQL SELECT or ASK query; updates and graph-result forms are rejected.")
    ]
    endpoint_url: Annotated[
        str,
        Arg("endpoint_url", default="", doc="SPARQL query endpoint override; uses VGI_WIKIDATA_SPARQL_URL or WDQS."),
    ]


@dataclass
class ResultState(ArrowSerializableDataclass):
    """A bounded immutable Arrow snapshot and batch cursor, portable across workers."""

    data: bytes
    index: int = 0


def api(args: Any) -> str:
    """Resolve the structured-data endpoint independently of the Wikipedia endpoint."""
    return endpoint(args.api_url, "VGI_WIKIDATA_API_URL", "https://www.wikidata.org/w/api.php")


def client(args: Limits) -> WikidataClient:
    """Start one request budget shared by every HTTP batch in this invocation."""
    return WikidataClient(timeout_ms=args.timeout_ms, max_bytes=args.max_bytes, cache_seconds=args.cache_seconds)


def validate(args: Any) -> None:
    """Validate user inputs before making any network requests."""
    if hasattr(args, "lang") and re.fullmatch(r"[a-zA-Z][a-zA-Z0-9-]{0,34}", args.lang or "") is None:
        raise ValueError("lang must be a language code")
    if hasattr(args, "ids"):
        if (
            not args.ids
            or len(args.ids) > 500
            or any(not isinstance(i, str) or re.fullmatch(r"[QP][1-9][0-9]*", i) is None for i in args.ids)
        ):
            raise ValueError("ids must contain 1 to 500 Wikibase Q/P IDs")
        api(args)
    if hasattr(args, "property_id") and args.property_id and re.fullmatch(r"P[1-9][0-9]*", args.property_id) is None:
        raise ValueError("property_id must be a P ID")
    for name in ("title", "query"):
        if hasattr(args, name) and not getattr(args, name).strip():
            raise ValueError(f"{name} must not be empty")


def encode(table: pa.Table, args: Limits) -> bytes:
    """Freeze the complete bounded result before emitting any SQL rows."""
    if table.num_rows > args.max_rows:
        raise WikiError("Wikidata result exceeds max_rows")
    if table.nbytes > args.max_bytes:
        raise WikiError("Wikidata result exceeds max_bytes")
    output = pa.BufferOutputStream()
    with pa.ipc.new_file(output, table.schema) as writer:
        writer.write_table(table, max_chunksize=CHUNK_ROWS)
    data = output.getvalue().to_pybytes()
    if len(data) > args.max_bytes:
        raise WikiError("Wikidata Arrow snapshot exceeds max_bytes")
    return data


class SnapshotFunction(TableFunctionGenerator[EntityArgs, ResultState]):
    """Common fixed-schema query lifecycle with fully externalized result state."""

    FIXED_SCHEMA: ClassVar[pa.Schema]
    operation: ClassVar[str] = "entity"

    @classmethod
    def on_bind(cls, params: BindParams[Any]) -> BindResponse:
        """Bind the fixed schema after validating all constant arguments."""
        validate(params.args)
        return BindResponse(output_schema=cls.FIXED_SCHEMA)

    @classmethod
    def on_init(cls, params: object) -> GlobalInitResponse:
        """Ensure the bounded remote query executes only once per scan."""
        return GlobalInitResponse(max_workers=1)

    @classmethod
    def fetch(cls, args: Any) -> pa.Table:
        """Fetch entities in batches and select the requested statement representation."""
        url = api(args)
        entities = client(args).entities(list(dict.fromkeys(args.ids)), url)
        rows = entity_rows(
            entities, url, args.lang, cls.operation, getattr(args, "property_id", ""), getattr(args, "rank", "all")
        )
        return pa.Table.from_pylist(rows, schema=cls.FIXED_SCHEMA)

    @classmethod
    def initial_state(cls, params: ProcessParams[Any]) -> ResultState:
        """Materialize once; subsequent batches never refetch mutable remote data."""
        return ResultState(encode(cls.fetch(params.args), params.args))

    @classmethod
    def process(cls, params: ProcessParams[Any], state: ResultState, out: OutputCollector) -> None:
        """Emit one snapshot batch, preserving state across HTTP/process boundaries."""
        reader = pa.ipc.open_file(pa.BufferReader(state.data))
        if state.index >= reader.num_record_batches:
            out.finish()
            return
        out.emit(reader.get_batch(state.index))
        state.index += 1


class Entity(SnapshotFunction):
    """Entity labels, descriptions, revisions and sitelinks for a batch of IDs."""

    FunctionArguments = EntityArgs
    FIXED_SCHEMA = ENTITY_SCHEMA

    class Meta:
        """Discovery metadata."""

        name = "entity"
        tags: ClassVar[dict[str, str]] = {}


class Statements(SnapshotFunction):
    """Full statements with typed values, ranks and stable statement identities."""

    FunctionArguments = StatementArgs
    FIXED_SCHEMA = STATEMENT_SCHEMA
    operation = "statements"

    class Meta:
        """Discovery metadata."""

        name = "statements"


class Qualifiers(SnapshotFunction):
    """Statement qualifier rows, retaining repeated values and their identities."""

    FunctionArguments = StatementArgs
    FIXED_SCHEMA = QUALIFIER_SCHEMA
    operation = "qualifiers"

    class Meta:
        """Discovery metadata."""

        name = "qualifiers"


class References(SnapshotFunction):
    """Reference snaks grouped by statement and upstream reference hash."""

    FunctionArguments = StatementArgs
    FIXED_SCHEMA = REFERENCE_SCHEMA
    operation = "references"

    class Meta:
        """Discovery metadata."""

        name = "statement_references"


class Search(SnapshotFunction):
    """Search entity labels/aliases with bounded continuation and no implicit match choice."""

    FunctionArguments = SearchArgs
    FIXED_SCHEMA = SEARCH_SCHEMA

    class Meta:
        """Discovery metadata."""

        name = "search"

    @classmethod
    def fetch(cls, args: SearchArgs) -> pa.Table:
        """Collect the requested top candidates, detecting broken continuation cursors."""
        connection = client(args)
        url = api(args)
        cursor = 0
        seen: set[int] = set()
        rows: list[dict[str, Any]] = []
        while len(rows) < args.count:
            if cursor in seen:
                raise WikiError("Wikibase search continuation cycle")
            seen.add(cursor)
            payload = connection.request(
                url,
                {
                    "action": "wbsearchentities",
                    "search": args.query,
                    "language": args.lang,
                    "uselang": args.lang,
                    "type": args.entity_type,
                    "limit": min(50, args.count - len(rows)),
                    "continue": cursor,
                    "format": "json",
                    "maxlag": "5",
                },
            )
            hits = payload.get("search")
            if not isinstance(hits, list):
                raise WikiError("Wikibase response is missing search results")
            for hit in hits:
                rows.append(
                    {
                        "entity_id": hit["id"],
                        "label": hit.get("label"),
                        "description": hit.get("description"),
                        "url": hit.get("concepturi"),
                        "match_text": hit.get("match", {}).get("text"),
                        "match_language": hit.get("match", {}).get("language"),
                    }
                )
            if "search-continue" not in payload:
                break
            cursor = int(payload["search-continue"])
        return pa.Table.from_pylist(rows[: args.count], schema=cls.FIXED_SCHEMA)


class WikiEntity(SnapshotFunction):
    """Resolve an article to its associated Wikibase item without matching labels."""

    FunctionArguments = PageArgs
    FIXED_SCHEMA = PAGE_ENTITY_SCHEMA

    class Meta:
        """Discovery metadata."""

        name = "wiki_entity"

    @classmethod
    def fetch(cls, args: PageArgs) -> pa.Table:
        """Use pageprops and redirect resolution on the selected article's wiki."""
        url = (args.api_url or default_api_url()).replace("{lang}", args.lang)
        url = endpoint(url, "VGI_WIKIPEDIA_API_URL", url)
        payload = client(args).request(
            url,
            {
                "action": "query",
                "titles": args.title,
                "redirects": "1",
                "prop": "pageprops|info",
                "ppprop": "wikibase_item",
                "inprop": "url",
                "format": "json",
                "formatversion": "2",
            },
        )
        pages = payload.get("query", {}).get("pages")
        if not isinstance(pages, list):
            raise WikiError("MediaWiki response is missing article pages")
        rows = [
            {
                "title": p["title"],
                "pageid": p.get("pageid"),
                "entity_id": p.get("pageprops", {}).get("wikibase_item"),
                "url": p.get("fullurl"),
                "lang": args.lang,
            }
            for p in pages
            if "missing" not in p and "invalid" not in p
        ]
        return pa.Table.from_pylist(rows, schema=cls.FIXED_SCHEMA)


class Sparql(SnapshotFunction):
    """Bind dynamic SELECT columns by fetching and freezing one bounded SPARQL result."""

    FunctionArguments = SparqlArgs

    class Meta:
        """Discovery metadata."""

        name = "sparql"
        tags: ClassVar[dict[str, str]] = {}

    @classmethod
    def on_bind(cls, params: BindParams[Any]) -> BindResponse:
        """Execute once at bind, carrying the result to execution as opaque Arrow bytes."""
        a = params.args
        form = query_form(a.query)
        url = endpoint(a.endpoint_url, "VGI_WIKIDATA_SPARQL_URL", "https://query.wikidata.org/sparql")
        payload = client(a).request(url, {"query": a.query, "format": "json"}, sparql=True)
        table = result(payload, form, a.max_rows)
        return BindResponse(output_schema=table.schema, opaque_data=encode(table, a))

    @classmethod
    def initial_state(cls, params: ProcessParams[Any]) -> ResultState:
        """Reuse the exact bound result, avoiding duplicate network evaluation."""
        if params.init_call is None or params.init_call.bind_opaque_data is None:
            raise WikiError("SPARQL execution is missing its bound result snapshot")
        return ResultState(params.init_call.bind_opaque_data)


def sql_type(data_type: pa.DataType) -> str:
    """Render the Arrow types used here as SQL metadata types."""
    if pa.types.is_struct(data_type):
        return "STRUCT(" + ", ".join(f'"{f.name}" {sql_type(f.type)}' for f in data_type) + ")"
    if pa.types.is_list(data_type):
        return sql_type(data_type.value_type) + "[]"
    return {"string": "VARCHAR", "bool": "BOOLEAN", "int32": "INTEGER", "int64": "BIGINT", "double": "DOUBLE"}[
        str(data_type)
    ]


def describe(
    function: Any, description: str, documentation: str, example: str, *, schema_name: str = "wikidata"
) -> None:
    """Attach consistent function metadata, examples and result column documentation."""
    function.Meta.description = description
    function.Meta.examples = [FunctionExample(sql=example, description=description)]
    documentation += (
        "\n\nRequests use configurable HTTP endpoints, bounded response sizes, retries and timeouts. "
        "cache_seconds controls a bounded process-local cache; zero bypasses it. An invocation freezes its "
        "complete result before emitting rows; there is no cross-query or cross-entity transaction snapshot. "
        "Errors fail explicitly rather than silently returning partial results. Order results explicitly."
    )
    function.Meta.tags = {
        "vgi.title": "Article identity" if schema_name == "main" else function.Meta.name.capitalize(),
        "vgi.category": "page-retrieval" if schema_name == "main" else "wikidata",
        "vgi.keywords": json.dumps(["wikidata", "wikibase", "knowledge", "graph"]),
        "vgi.doc_llm": documentation,
        "vgi.doc_md": "## " + description + "\n\n" + documentation + "\n\n### SQL usage\n\n"
        "Batch entity IDs in one call, then join the returned rows to local SQL tables. "
        "Function arguments must be constants known when SQL binds.",
        "vgi.example_queries": json.dumps([{"sql": example, "description": description}]),
        "vgi.executable_examples": json.dumps([{"sql": example, "description": description}]),
    }
    if hasattr(function, "FIXED_SCHEMA"):
        function.Meta.tags["vgi.result_columns_schema"] = json.dumps(
            [
                {
                    "name": f.name,
                    "type": sql_type(f.type),
                    "description": (f.metadata or {}).get(b"comment", b"").decode(),
                }
                for f in function.FIXED_SCHEMA
            ]
        )


describe(
    Entity,
    "Retrieve Wikidata entities in batches",
    "Supply ids as a `VARCHAR[]` of Q/P IDs. "
    "Returns one row per distinct requested ID, including missing=true for absent IDs. Redirects preserve "
    "requested_id and return canonical entity_id. Descriptions use exactly lang. "
    "Labels/aliases fall back only to shared mul values; actual languages are returned. "
    "entity_revision and source_api identify the source; sitelinks_json retains all sitelinks.",
    "SELECT entity_id, label FROM wiki.wikidata.entity(['Q42']) ORDER BY entity_id",
)
describe(
    Statements,
    "Read full Wikidata statements with typed values",
    "Supply ids as `VARCHAR[]`. "
    "Each row contains statement_id, property_id, rank, snak_type and a typed value `STRUCT`. "
    "Use value.entity_id, value.text, value.time, value.amount or coordinate fields. Time precision/calendar "
    "and quantity units/bounds are preserved; amounts remain exact decimal strings. value.raw_json retains "
    "all upstream datavalue fields. somevalue and novalue have NULL value with distinct snak_type. "
    "property_id filters main statement properties; rank=best selects preferred, otherwise normal, per property.",
    "SELECT property_id, count(*) AS statements FROM wiki.wikidata.statements(['Q42']) "
    "GROUP BY property_id ORDER BY property_id",
)
describe(
    Qualifiers,
    "Read contextual qualifiers attached to Wikidata statements",
    "Returns one row per qualifier snak. "
    "Join to statements by source_api, entity_revision and statement_id; requested_id disambiguates redirected inputs. "
    "property_id argument selects the main statement property; result property_id is the qualifier property. "
    "Typed value and snak_type have the same semantics as statements. Repeated qualifiers retain index and hash.",
    "SELECT statement_property_id, count(*) AS qualifiers FROM wiki.wikidata.qualifiers(['Q42']) "
    "GROUP BY statement_property_id ORDER BY statement_property_id",
)
describe(
    References,
    "Read sources attached to Wikidata statements",
    "Returns one row per reference snak. "
    "Join to statements by source_api, entity_revision and statement_id; requested_id distinguishes redirected inputs. "
    "reference_index and reference_hash group snaks belonging to the same source. property_id argument filters "
    "main statement properties; result property_id identifies the reference property. "
    "Typed values preserve source details.",
    "SELECT statement_property_id, count(*) AS reference_snaks FROM wiki.wikidata.statement_references(['Q42']) "
    "GROUP BY statement_property_id ORDER BY statement_property_id",
)
describe(
    Search,
    "Search Wikidata labels and aliases",
    "Returns the requested top count candidates using bounded "
    "continuation. This is entity discovery, not guaranteed identity resolution. "
    "Choose item or property with entity_type. "
    "Results include the matched label/alias and language; missing optional fields are NULL.",
    "SELECT entity_id, label FROM wiki.wikidata.search('Douglas Adams', count := 3) ORDER BY entity_id",
)
describe(
    WikiEntity,
    "Resolve a Wikipedia article to its Wikidata identity",
    "Resolve a known article title, "
    "following MediaWiki redirects and reading wikibase_item from pageprops. Missing articles produce zero rows; "
    "articles without a Wikibase association have entity_id NULL. Use api_url for another MediaWiki site. "
    "The associated ID belongs to that site's Wikibase repository, which may differ from Wikidata.",
    "SELECT title, entity_id FROM wiki.main.wiki_entity('Douglas Adams') ORDER BY title",
    schema_name="main",
)
describe(
    Sparql,
    "Query Wikidata relationships with read-only SPARQL",
    "Accepts SELECT and ASK; complete query "
    "syntax is validated before sending it to the configured endpoint. SELECT columns are RDF term `STRUCT`s "
    "(kind, value, datatype, language), with SQL NULL for unbound variables. ASK returns answer `BOOLEAN`. "
    "Supports graph patterns, joins, aggregates and property paths implemented by the endpoint. "
    "The query runs at bind time, including DESCRIBE/EXPLAIN; execution reuses that result. "
    "No automatic pagination or LIMIT rewriting is performed. max_rows overflow fails instead of truncating. "
    "Use endpoint_url to select the desired WDQS dataset or another SPARQL query service.",
    "SELECT answer FROM wiki.wikidata.sparql('ASK { <http://www.wikidata.org/entity/Q42> ?p ?o }')",
)
Sparql.Meta.tags["vgi.result_dynamic_columns_md"] = """
SELECT returns one column per projected variable:

| Name | Type | Description |
| --- | --- | --- |
| variable | STRUCT(kind VARCHAR, value VARCHAR, datatype VARCHAR, language VARCHAR) | RDF term or NULL if unbound. |

ASK returns:

| Name | Type | Description |
| --- | --- | --- |
| answer | BOOLEAN | Whether the graph pattern matches. |
"""

WIKIDATA_FUNCTIONS = [Search, Entity, Statements, Qualifiers, References, Sparql]
WIKIDATA_SCHEMA = Schema(
    path=["wikidata"],
    comment="Structured Wikibase knowledge and remote graph queries",
    tags={
        "vgi.title": "Wikidata knowledge",
        "domain": "knowledge",
        "vgi.keywords": json.dumps(["wikidata", "wikibase", "sparql", "statements"]),
        "vgi.doc_llm": "Retrieve structured knowledge with explicit identity, statement context and source revisions. "
        "Batch known IDs for efficient enrichment; use graph patterns for relationship questions. "
        "This is a remote read-only connector with bounded cached responses, not a local database.",
        "vgi.doc_md": "## Structured knowledge\n\n"
        "Wikibase entities connect labels, typed values, qualifiers and reference sources. "
        "Combine them with article discovery and local SQL tables. Entity revisions help identify differences "
        "between observations; calls do not share a global transaction snapshot.\n\n"
        "Use [Wikidata](https://www.wikidata.org/) for structured public knowledge. "
        "Bound queries with explicit result and byte budgets, and retain source identity when joining observations.",
        "vgi.example_queries": Entity.Meta.tags["vgi.example_queries"],
        "vgi.categories": json.dumps(
            [{"name": "wikidata", "title": "Wikidata", "description": "Structured knowledge and graph queries."}]
        ),
    },
    functions=WIKIDATA_FUNCTIONS,
)
