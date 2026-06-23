"""Pure JSON-to-row mapping for the Wikipedia worker (no network).

Two shapes, two row types:

* :func:`parse_search` maps an Action-API ``list=search`` payload to
  :class:`SearchRow` rows -- the ``wiki_search`` unified schema -- stripping the
  HTML in each ``snippet`` down to plain text and turning the page URL together.
  It also returns the ``sroffset`` continuation token (or ``None`` when the
  result set is exhausted) so the table function can page.
* :func:`parse_summary` maps a REST page-summary (or normalized Action-API
  extract) payload to a :class:`PageRow`.

Everything here is deterministic and side-effect-free, so it is unit-tested
directly against captured fixtures, covering HTML-snippet stripping and
missing-field -> NULL.
"""

from __future__ import annotations

import html
import re
import urllib.parse
from dataclasses import dataclass
from typing import Any

# MediaWiki search snippets are HTML: the matched terms are wrapped in
# ``<span class="searchmatch">...</span>`` and the text may contain entities.
# We strip tags and unescape entities to plain text (the SPEC requires plain
# snippets, no HTML).
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


def strip_html(value: str | None) -> str | None:
    """Strip HTML tags and unescape entities, collapsing whitespace.

    Returns ``None`` for ``None`` input so a missing snippet reads as SQL NULL.
    """
    if value is None:
        return None
    text = _TAG_RE.sub("", value)
    text = html.unescape(text)
    text = _WS_RE.sub(" ", text).strip()
    return text


def page_url(title: str | None, lang: str) -> str | None:
    """Canonical Wikipedia article URL for a title, or ``None`` if no title.

    Used for the search ``url`` column. Spaces become underscores and the title
    is percent-encoded, matching Wikipedia's article URL convention.
    """
    if not title:
        return None
    quoted = urllib.parse.quote(title.replace(" ", "_"), safe="")
    return f"https://{lang or 'en'}.wikipedia.org/wiki/{quoted}"


@dataclass(slots=True)
class SearchRow:
    """One ``wiki_search`` row (the unified search schema).

    ``title`` VARCHAR, ``snippet`` VARCHAR (HTML stripped to text),
    ``pageid`` BIGINT, ``wordcount`` INTEGER, ``url`` VARCHAR, ``lang`` VARCHAR,
    ``extra`` VARCHAR(JSON, populated by the table function).
    """

    title: str | None = None
    snippet: str | None = None
    pageid: int | None = None
    wordcount: int | None = None
    url: str | None = None
    lang: str | None = None
    timestamp: str | None = None
    size: int | None = None


@dataclass(slots=True)
class PageRow:
    """One ``wiki_page`` result (a page summary / extract)."""

    title: str | None = None
    extract: str | None = None
    url: str | None = None
    thumbnail_url: str | None = None
    pageid: int | None = None


def parse_search(payload: dict[str, Any], *, lang: str) -> tuple[list[SearchRow], int | None]:
    """Map an Action-API ``list=search`` payload to rows + the next ``sroffset``.

    Returns ``(rows, next_sroffset)`` where ``next_sroffset`` is the
    continuation offset to fetch the following page, or ``None`` when the result
    set is exhausted (no ``continue.sroffset``).
    """
    query = payload.get("query") or {}
    hits = query.get("search") or []
    rows: list[SearchRow] = []
    for item in hits:
        title = item.get("title")
        rows.append(
            SearchRow(
                title=title,
                snippet=strip_html(item.get("snippet")),
                pageid=item.get("pageid"),
                wordcount=item.get("wordcount"),
                url=page_url(title, lang),
                lang=lang,
                timestamp=item.get("timestamp"),
                size=item.get("size"),
            )
        )
    # formatversion=2 puts the continuation token under continue.sroffset.
    cont = payload.get("continue") or {}
    next_sroffset = cont.get("sroffset")
    if isinstance(next_sroffset, str) and next_sroffset.isdigit():
        next_sroffset = int(next_sroffset)
    if not isinstance(next_sroffset, int):
        next_sroffset = None
    return rows, next_sroffset


def parse_summary(payload: dict[str, Any] | None) -> PageRow | None:
    """Map a REST page-summary (or normalized extract) payload to a PageRow.

    Returns ``None`` for a missing page (``None`` payload). Missing individual
    fields become ``None`` (SQL NULL).
    """
    if payload is None:
        return None
    content_urls = payload.get("content_urls") or {}
    desktop = content_urls.get("desktop") or {}
    url = desktop.get("page") or payload.get("canonicalurl") or payload.get("fullurl")
    thumbnail = payload.get("thumbnail") or {}
    return PageRow(
        title=payload.get("title"),
        extract=payload.get("extract"),
        url=url,
        thumbnail_url=thumbnail.get("source"),
        pageid=payload.get("pageid"),
    )
