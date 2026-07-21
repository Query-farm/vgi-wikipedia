# /// script
# requires-python = ">=3.13"
# dependencies = [
#     "vgi-python[http]>=0.16.0",
#     "httpx>=0.27",
# ]
# ///
"""Repo-root stdio entry for the Wikipedia worker (thin PEP 723 shim).

The catalog, the :class:`~vgi_wikipedia.worker.WikipediaWorker` class, and
``main()`` live in the wheel-importable :mod:`vgi_wikipedia.worker` module. This
file re-exports them and stays a self-contained PEP 723 script so DuckDB's vgi
extension can spawn it directly::

    uv run wikipedia_worker.py            # serve over stdio (DuckDB subprocess)
    python serve.py --port 8000           # serve over HTTP

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'wiki' (TYPE vgi, LOCATION 'uv run wikipedia_worker.py');

LICENSING: the worker code is MIT. CONTENT retrieved from Wikipedia / Wikimedia
projects is **CC-BY-SA** (text) -- attribution and share-alike are the user's
responsibility. See README.md.
"""

from __future__ import annotations

from vgi_wikipedia.worker import WikipediaWorker, main

__all__ = ["WikipediaWorker", "main"]


if __name__ == "__main__":
    main()
