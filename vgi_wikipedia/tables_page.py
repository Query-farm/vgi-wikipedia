"""``wiki_page_summary`` -- the rich, multi-column page summary (table function).

``wiki_page_summary(title, lang := 'en', api_url := '')`` returns a single-row
table with the full page summary::

    title VARCHAR, extract VARCHAR, url VARCHAR, thumbnail_url VARCHAR, pageid BIGINT

It is the multi-column companion to the :func:`wiki_page` scalar (which returns
just the extract text). Being a **table** function, it takes ``name := value``
named arguments. A missing page yields zero rows (not an error); a transport
failure raises a clean :class:`WikiError`.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Annotated, ClassVar

import pyarrow as pa
from vgi.arguments import Arg
from vgi.invocation import BindResponse, GlobalInitResponse
from vgi.metadata import FunctionExample
from vgi.table_function import (
    BindParams,
    ProcessParams,
    TableCardinality,
    TableFunctionGenerator,
)
from vgi_rpc.rpc import OutputCollector

from vgi_wikipedia.client import WikiClient, WikiError
from vgi_wikipedia.meta import object_tags
from vgi_wikipedia.parse import parse_summary
from vgi_wikipedia.schema_utils import field

_WIKI_PAGE_SUMMARY_DOC_LLM = (
    "Fetch the **rich, multi-column page summary** of a single Wikipedia (or any "
    "MediaWiki) article as a one-row table.\n\n"
    "Call it as a table function: "
    "`wiki.main.wiki_page_summary(title, lang := 'en', api_url := '')`. Being a table "
    "function, `lang` and `api_url` are `name :=` named arguments: `lang` picks the "
    "language wiki and `api_url` targets a non-Wikimedia wiki's api.php.\n\n"
    "Use it when you need more than just the extract text -- the canonical URL (for "
    "attribution / linking back), a thumbnail image, and the page id -- e.g. when "
    "enriching rows for retrieval-augmented generation (RAG), knowledge cards, or "
    "fact lookup. For just the extract text as a scalar over a whole column of titles, "
    "use the `wiki_page` scalar instead.\n\n"
    "**Returns** one row with columns `title` (resolved), `extract` (plain-text "
    "summary), `url`, `thumbnail_url` (may be NULL), and `pageid`.\n\n"
    "**Edge cases:** an empty title is rejected at bind time. A missing page yields "
    "**zero rows** (not an error). A transport failure surfaces as a clean DuckDB "
    "error. Retrieved text is **CC-BY-SA**: attribution and share-alike are the "
    "caller's responsibility."
)

_WIKI_PAGE_SUMMARY_DOC_MD = (
    "# wiki_page_summary\n\n"
    "Returns a single Wikipedia / MediaWiki page's **rich summary** as a one-row "
    "table -- the multi-column companion to the `wiki_page` scalar -- over the free "
    "MediaWiki REST summary endpoint.\n\n"
    "## Usage\n\n"
    "Call it as a table function in `FROM`: the page title is the one positional "
    "argument, and `lang` and `api_url` are `name :=` named options. See the runnable "
    "examples in this function's `example_queries` metadata.\n\n"
    "The page title is positional; the language wiki and a custom MediaWiki endpoint are "
    "named options. See the per-argument docs (`vgi_function_arguments`) for the exact "
    "names and defaults.\n\n"
    "## Notes\n\n"
    "- A missing page returns zero rows; it is not an error.\n"
    "- `thumbnail_url` may be NULL when the page has no lead image.\n"
    "- For just the extract over many titles, use the `wiki_page` scalar.\n"
    "- Retrieved text is **CC-BY-SA** -- attribution and share-alike are your "
    "responsibility."
)

_WIKI_PAGE_SUMMARY_KEYWORDS = [
    "wikipedia",
    "mediawiki",
    "wiki_page_summary",
    "page summary",
    "extract",
    "url",
    "thumbnail",
    "pageid",
    "article",
    "encyclopedia",
    "rag",
    "retrieval",
    "knowledge grounding",
    "fact lookup",
    "lang",
    "language",
]

# VGI307/VGI414: the static result schema as a JSON array of {name,type,description}
# (replaces the retired free-form `vgi.result_columns_md`).
_WIKI_PAGE_SUMMARY_RESULT_COLUMNS_SCHEMA = json.dumps(
    [
        {"name": "title", "type": "VARCHAR", "description": "Resolved page title."},
        {"name": "extract", "type": "VARCHAR", "description": "Plain-text summary extract."},
        {"name": "url", "type": "VARCHAR", "description": "Canonical page URL."},
        {
            "name": "thumbnail_url",
            "type": "VARCHAR",
            "description": "Thumbnail image URL, if any (else NULL).",
        },
        {"name": "pageid", "type": "BIGINT", "description": "MediaWiki page id."},
    ],
    ensure_ascii=False,
)

# VGI515: described example_queries (the native carrier drops descriptions); the
# `sql` matches Meta.examples byte-for-byte so the two carriers dedup to one row.
_WIKI_PAGE_SUMMARY_EXAMPLE_QUERIES = json.dumps(
    [
        {
            "description": "The English Wikipedia summary row for DuckDB",
            "sql": "SELECT title, extract, url FROM wiki.main.wiki_page_summary('DuckDB', lang := 'en')",
        },
    ],
    ensure_ascii=False,
)

# VGI509: guaranteed-runnable, catalog-qualified examples (expected_result omitted).
_WIKI_PAGE_SUMMARY_EXECUTABLE_EXAMPLES = (
    '[{"description": "Rich English summary row for the DuckDB article.", '
    '"sql": "SELECT title, url, pageid FROM wiki.main.wiki_page_summary(\'DuckDB\', '
    "lang := 'en')\"}, "
    '{"description": "A missing page returns zero rows.", '
    '"sql": "SELECT count(*) AS n FROM '
    "wiki.main.wiki_page_summary('ThisPageDoesNotExist_zzz')\"}]"
)

WIKI_PAGE_SCHEMA = pa.schema(
    [
        field("title", pa.string(), "Resolved page title."),
        field("extract", pa.string(), "Plain-text summary extract."),
        field("url", pa.string(), "Canonical page URL."),
        field("thumbnail_url", pa.string(), "Thumbnail image URL, if any."),
        field("pageid", pa.int64(), "MediaWiki page id."),
    ]
)


@dataclass(slots=True, frozen=True, kw_only=True)
class WikiPageArgs:
    """Arguments for ``wiki_page_summary`` (positional title + named options)."""

    title: Annotated[str, Arg(0, doc="The page title to fetch.")]
    lang: Annotated[str, Arg("lang", default="en", doc="Wiki language code (default 'en').")]
    api_url: Annotated[
        str,
        Arg("api_url", default="", doc="Override the MediaWiki api.php URL; default Wikipedia."),
    ]


class WikiPageSummary(TableFunctionGenerator[WikiPageArgs, None]):
    """Rich, multi-column page summary (see module docstring)."""

    FunctionArguments: ClassVar[type] = WikiPageArgs

    class Meta:
        """Function metadata."""

        name = "wiki_page_summary"
        description = "Page summary as a row: title, extract, url, thumbnail_url, pageid"
        categories = ["wikipedia", "mediawiki", "rag", "retrieval"]
        tags = {
            **object_tags(
                title="Wikipedia Page Summary Row",
                doc_llm=_WIKI_PAGE_SUMMARY_DOC_LLM,
                doc_md=_WIKI_PAGE_SUMMARY_DOC_MD,
                keywords=_WIKI_PAGE_SUMMARY_KEYWORDS,
                relative_path="vgi_wikipedia/tables_page.py",
            ),
            "vgi.category": "page-retrieval",
            "vgi.result_columns_schema": _WIKI_PAGE_SUMMARY_RESULT_COLUMNS_SCHEMA,
            "vgi.example_queries": _WIKI_PAGE_SUMMARY_EXAMPLE_QUERIES,
            "vgi.executable_examples": _WIKI_PAGE_SUMMARY_EXECUTABLE_EXAMPLES,
        }
        examples = [
            FunctionExample(
                sql="SELECT title, extract, url FROM wiki.main.wiki_page_summary('DuckDB', lang := 'en')",
                description="The English Wikipedia summary row for DuckDB",
            ),
        ]

    @classmethod
    def on_bind(cls, params: BindParams[WikiPageArgs]) -> BindResponse:
        """Validate the title and bind the fixed output schema."""
        if not (params.args.title or "").strip():
            raise ValueError("wiki_page_summary requires a non-empty title")
        return BindResponse(output_schema=WIKI_PAGE_SCHEMA)

    @classmethod
    def on_init(cls, params: object) -> GlobalInitResponse:
        """Pin a single scan worker so the one summary row is emitted exactly once."""
        # A single summary row must be produced exactly once.
        return GlobalInitResponse(max_workers=1)

    @classmethod
    def cardinality(cls, params: BindParams[WikiPageArgs]) -> TableCardinality:
        """Report the exact one-row cardinality."""
        return TableCardinality(estimate=1, max=1)

    @classmethod
    def initial_state(cls, params: ProcessParams[WikiPageArgs]) -> None:
        """Start with no scan state (a single row needs none)."""
        return None

    @classmethod
    def process(
        cls,
        params: ProcessParams[WikiPageArgs],
        state: None,
        out: OutputCollector,
    ) -> None:
        """Fetch the page summary and emit it as a single row."""
        a = params.args
        client = WikiClient(api_url=a.api_url or None)
        try:
            page = parse_summary(client.summary(a.title, lang=a.lang))
        except WikiError:
            raise
        except Exception as exc:  # noqa: BLE001
            raise WikiError(f"wiki_page_summary failed: {exc}") from exc

        if page is not None:
            out.emit(
                pa.RecordBatch.from_pydict(
                    {
                        "title": [page.title],
                        "extract": [page.extract],
                        "url": [page.url],
                        "thumbnail_url": [page.thumbnail_url],
                        "pageid": [page.pageid],
                    },
                    schema=params.output_schema,
                )
            )
        out.finish()


PAGE_TABLE_FUNCTIONS: list[type] = [WikiPageSummary]
