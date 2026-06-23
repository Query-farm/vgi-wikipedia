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
from vgi_wikipedia.parse import parse_summary


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
        name = "wiki_page"
        description = "Plain-text summary extract of an English Wikipedia page (NULL if missing)"
        categories = ["wikipedia", "mediawiki", "rag"]
        examples = [
            FunctionExample(
                sql="SELECT wiki_page('DuckDB')",
                description="The English Wikipedia summary of DuckDB",
            ),
        ]

    @classmethod
    def compute(
        cls,
        title: Annotated[pa.StringArray, Param(doc="Page title to fetch.")],
    ) -> Annotated[pa.StringArray, Returns()]:
        return _extracts(title, "en")


class WikiPage(ScalarFunction):
    """``wiki_page(title, lang)`` -- summary extract from a chosen-language wiki."""

    class Meta:
        name = "wiki_page"
        description = "Plain-text summary extract of a Wikipedia page in a given language (NULL if missing)"
        categories = ["wikipedia", "mediawiki", "rag"]
        examples = [
            FunctionExample(
                sql="SELECT wiki_page('DuckDB', 'de')",
                description="The German Wikipedia summary of DuckDB",
            ),
        ]

    @classmethod
    def compute(
        cls,
        title: Annotated[pa.StringArray, Param(doc="Page title to fetch.")],
        lang: Annotated[str, ConstParam("Wiki language code, e.g. 'en' or 'de'.")],
    ) -> Annotated[pa.StringArray, Returns()]:
        return _extracts(title, lang or "en")


SCALAR_FUNCTIONS: list[type] = [WikiPageDefault, WikiPage]
