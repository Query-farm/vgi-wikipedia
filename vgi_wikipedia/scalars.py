"""``wiki_page`` -- fetch a page's summary extract as a scalar (positional-only).

``wiki_page(title)`` / ``wiki_page(title, lang)`` returns the plain-text summary
**extract** of a Wikipedia page as a single VARCHAR -- the lead-paragraph summary
from the REST ``/page/summary/{title}`` endpoint (with an Action-API
``prop=extracts`` fallback). It returns NULL for a missing page or NULL input;
it never crashes a scan.

Scalars are POSITIONAL-ONLY in VGI/DuckDB (``name := value`` is a table-function
feature), so ``lang`` is a positional :class:`ConstParam`, exposed as two arity
overloads::

    SELECT wiki_page('DuckDB');              -- English (default)
    SELECT wiki_page('DuckDB', 'de');        -- German Wikipedia

The richer multi-column shape (title, extract, url, thumbnail_url, pageid) is the
:class:`~vgi_wikipedia.tables_page.WikiPageSummary` **table** function.
"""

from __future__ import annotations

from typing import Annotated

import pyarrow as pa
from vgi.arguments import ConstParam, Param, Returns
from vgi.metadata import FunctionExample
from vgi.scalar_function import ScalarFunction

from vgi_wikipedia.client import WikiClient, WikiError
from vgi_wikipedia.meta import object_tags
from vgi_wikipedia.parse import parse_summary

_WIKI_PAGE_DOC_LLM = (
    "Fetch the plain-text **lead-paragraph summary extract** of a Wikipedia (or any "
    "MediaWiki) article as a single VARCHAR.\n\n"
    "Call it as `wiki.main.wiki_page(title)` for English, or "
    "`wiki.main.wiki_page(title, lang)` to target another language wiki (e.g. `'de'`, "
    "`'fr'`). Because VGI scalars are positional-only, `lang` is a positional argument, "
    "not a `lang :=` named argument.\n\n"
    "Use it to enrich an existing column of titles with encyclopedic context for "
    "retrieval-augmented generation (RAG), fact lookup, or knowledge grounding -- it is "
    "set-based, so it resolves a whole column of titles in one scan.\n\n"
    "**Input:** an article title column (VARCHAR). **Output:** the summary extract "
    "VARCHAR.\n\n"
    "**Edge cases:** a missing page, a blank title, or a NULL input yields NULL for that "
    "row -- a single bad title never aborts the scan. A transport-level failure surfaces "
    "as a clean DuckDB error rather than crashing the worker. For the richer multi-column "
    "shape (title, extract, url, thumbnail_url, pageid) use the `wiki_page_summary` table "
    "function instead. Retrieved article text is **CC-BY-SA**: attribution and "
    "share-alike are the caller's responsibility."
)

_WIKI_PAGE_DOC_MD = (
    "# wiki_page\n\n"
    "Returns the plain-text summary **extract** (lead paragraphs) of a Wikipedia / "
    "MediaWiki page as a scalar VARCHAR, over the free MediaWiki REST summary endpoint "
    "(with an Action-API `prop=extracts` fallback).\n\n"
    "## Usage\n\n"
    "```sql\n"
    "SELECT wiki.main.wiki_page('DuckDB');          -- English (default)\n"
    "SELECT wiki.main.wiki_page('DuckDB', 'de');    -- German Wikipedia\n"
    "```\n\n"
    "Scalars are positional-only, so the language is a positional argument (no "
    "`lang :=`). To resolve a column of titles, just reference the column:\n\n"
    "```sql\n"
    "SELECT t.name, wiki.main.wiki_page(t.name) AS summary FROM topics t;\n"
    "```\n\n"
    "## Notes\n\n"
    "- A missing page, blank title, or NULL input returns NULL (the scan is never "
    "aborted by one bad row).\n"
    "- Transport failures raise a clean DuckDB error; the worker never crashes.\n"
    "- Need the URL, thumbnail, or page id too? Use the `wiki_page_summary` table "
    "function.\n"
    "- Retrieved text is **CC-BY-SA** -- attribution and share-alike are your "
    "responsibility."
)

_WIKI_PAGE_KEYWORDS = [
    "wikipedia",
    "mediawiki",
    "wiki_page",
    "page summary",
    "extract",
    "lead paragraph",
    "article",
    "encyclopedia",
    "rag",
    "knowledge grounding",
    "fact lookup",
    "lookup",
    "scalar",
    "lang",
    "language",
]

_WIKI_PAGE_TAGS = object_tags(
    title="Wikipedia Page Summary Extract",
    doc_llm=_WIKI_PAGE_DOC_LLM,
    doc_md=_WIKI_PAGE_DOC_MD,
    keywords=_WIKI_PAGE_KEYWORDS,
    relative_path="vgi_wikipedia/scalars.py",
)

# VGI509: guaranteed-runnable, catalog-qualified examples. Each `sql` is
# self-contained and re-runnable against an attached `wiki` worker. We omit
# `expected_result` deliberately -- the linter only needs the query to execute,
# and live Wikipedia content drifts so pinning exact text would be brittle.
_WIKI_PAGE_EXECUTABLE_EXAMPLES = (
    '[{"description": "English Wikipedia summary extract of the DuckDB article.", '
    '"sql": "SELECT wiki.main.wiki_page(\'DuckDB\') AS summary"}, '
    '{"description": "German Wikipedia summary extract of the DuckDB article.", '
    "\"sql\": \"SELECT wiki.main.wiki_page('DuckDB', 'de') AS summary\"}, "
    '{"description": "A missing page yields NULL rather than an error.", '
    '"sql": "SELECT wiki.main.wiki_page(\'ThisPageDoesNotExist_zzz\') IS NULL AS is_null"}]'
)


def _extracts(titles: pa.StringArray, lang: str) -> pa.StringArray:
    """Fetch each non-null title's extract, returning a string column.

    A missing page or any per-title error yields NULL for that row -- the scan
    is never crashed by a single bad title.
    """
    client = WikiClient()
    out: list[str | None] = []
    for value in titles.to_pylist():
        if value is None or not str(value).strip():
            out.append(None)
            continue
        try:
            page = parse_summary(client.summary(str(value), lang=lang))
        except WikiError:
            out.append(None)
            continue
        except Exception:  # noqa: BLE001 - a scalar must never crash a scan
            out.append(None)
            continue
        out.append(page.extract if page is not None else None)
    return pa.array(out, type=pa.string())


class WikiPageDefault(ScalarFunction):
    """``wiki_page(title)`` -- English Wikipedia summary extract."""

    class Meta:
        """Function metadata."""

        name = "wiki_page"
        description = "Plain-text summary extract of an English Wikipedia page (NULL if missing)"
        categories = ["wikipedia", "mediawiki", "rag"]
        tags = _WIKI_PAGE_TAGS
        examples = [
            FunctionExample(
                sql="SELECT wiki.main.wiki_page('DuckDB')",
                description="The English Wikipedia summary of DuckDB",
            ),
        ]

    @classmethod
    def compute(
        cls,
        title: Annotated[pa.StringArray, Param(doc="Page title to fetch.")],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Fetch each title's English summary extract."""
        return _extracts(title, "en")


class WikiPage(ScalarFunction):
    """``wiki_page(title, lang)`` -- summary extract from a chosen-language wiki."""

    class Meta:
        """Function metadata."""

        name = "wiki_page"
        description = "Plain-text summary extract of a Wikipedia page in a given language (NULL if missing)"
        categories = ["wikipedia", "mediawiki", "rag"]
        tags = {
            **_WIKI_PAGE_TAGS,
            "vgi.executable_examples": _WIKI_PAGE_EXECUTABLE_EXAMPLES,
        }
        examples = [
            FunctionExample(
                sql="SELECT wiki.main.wiki_page('DuckDB', 'de')",
                description="The German Wikipedia summary of DuckDB",
            ),
        ]

    @classmethod
    def compute(
        cls,
        title: Annotated[pa.StringArray, Param(doc="Page title to fetch.")],
        lang: Annotated[str, ConstParam("Wiki language code, e.g. 'en' or 'de'.")],
    ) -> Annotated[pa.StringArray, Returns()]:
        """Fetch each title's summary extract from the chosen-language wiki."""
        return _extracts(title, lang or "en")


SCALAR_FUNCTIONS: list[type] = [WikiPageDefault, WikiPage]
