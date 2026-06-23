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
from vgi_wikipedia.parse import parse_summary
from vgi_wikipedia.schema_utils import field

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
        name = "wiki_page_summary"
        description = "Page summary as a row: title, extract, url, thumbnail_url, pageid"
        categories = ["wikipedia", "mediawiki", "rag", "retrieval"]
        examples = [
            FunctionExample(
                sql="SELECT title, extract, url FROM wiki_page_summary('DuckDB', lang := 'en')",
                description="The English Wikipedia summary row for DuckDB",
            ),
        ]

    @classmethod
    def on_bind(cls, params: BindParams[WikiPageArgs]) -> BindResponse:
        if not (params.args.title or "").strip():
            raise ValueError("wiki_page_summary requires a non-empty title")
        return BindResponse(output_schema=WIKI_PAGE_SCHEMA)

    @classmethod
    def on_init(cls, params: object) -> GlobalInitResponse:
        # A single summary row must be produced exactly once.
        return GlobalInitResponse(max_workers=1)

    @classmethod
    def cardinality(cls, params: BindParams[WikiPageArgs]) -> TableCardinality:
        return TableCardinality(estimate=1, max=1)

    @classmethod
    def initial_state(cls, params: ProcessParams[WikiPageArgs]) -> None:
        return None

    @classmethod
    def process(
        cls,
        params: ProcessParams[WikiPageArgs],
        state: None,
        out: OutputCollector,
    ) -> None:
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
