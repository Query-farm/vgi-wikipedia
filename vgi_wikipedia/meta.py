"""Shared helpers for the per-object discovery/description metadata tags.

The ``vgi-lint`` strict profile expects these on **every** function and table.
Each function/table surfaces them in its ``Meta.tags``:

- ``vgi.title`` (VGI124)        -- human-friendly display name
- ``vgi.doc_llm`` (VGI112)      -- a Markdown narrative aimed at LLM/agents
- ``vgi.doc_md`` (VGI113)       -- a Markdown narrative aimed at human docs
- ``vgi.keywords`` (VGI126)     -- a JSON array of search terms/synonyms

``vgi.source_url`` is set ONLY on the catalog object (VGI139): per-object
``source_url`` tags are redundant and the linter flags them, so they are not
emitted here.

``keywords_json(...)`` serializes a list of terms to the JSON-array string the
``vgi.keywords`` tag requires (VGI138).
"""

from __future__ import annotations

import json


def keywords_json(keywords: list[str]) -> str:
    """Serialize search keywords to the ``vgi.keywords`` JSON-array string.

    ``vgi.keywords`` must be a JSON array of strings like ``["a","b"]`` (VGI138),
    not a comma-separated string.
    """
    return json.dumps(keywords, ensure_ascii=False)


def object_tags(
    *,
    title: str,
    doc_llm: str,
    doc_md: str,
    keywords: list[str],
    relative_path: str,
) -> dict[str, str]:
    """Build the four standard per-object discovery/description tags.

    ``keywords`` is a list of terms, serialized to the required JSON-array
    string. ``relative_path`` is accepted for call-site documentation of the
    implementing file but is no longer emitted as a per-object ``source_url``
    (VGI139 keeps ``source_url`` on the catalog object only).
    """
    return {
        "vgi.title": title,
        "vgi.doc_llm": doc_llm,
        "vgi.doc_md": doc_md,
        "vgi.keywords": keywords_json(keywords),
    }
