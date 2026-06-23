"""Polite MediaWiki HTTP client: search + page summary/extract.

This is the only module that touches the network. It applies the
network-worker discipline uniformly:

* **Descriptive User-Agent.** The MediaWiki API *requires* a real, identifying
  User-Agent (a generic library default risks being blocked). It is overridable
  via ``VGI_WIKIPEDIA_USER_AGENT``.
* **Per-call timeout** (:data:`DEFAULT_TIMEOUT`).
* **Bounded retry with backoff** on 429 / 5xx (:meth:`_request`).
* **Never crash the worker.** Any failure raises :class:`WikiError`, which the
  functions turn into a clean DuckDB error -- the process stays up.

The API base URL is configurable per instance (and via the
``VGI_WIKIPEDIA_API_URL`` env var) so tests can point it at a local mock HTTP
server, and so the same code can target *any* MediaWiki wiki, not just
Wikipedia.

Two endpoints are used:

* **Action API** (``api.php``) ``action=query&list=search`` for full-text
  search, and ``prop=extracts`` as the page-extract fallback. Free, no key.
* **REST summary** (``/api/rest_v1/page/summary/{title}``) for the rich page
  summary (extract + thumbnail + canonical URL). Falls back to the Action API
  extract when REST is unavailable (e.g. a non-Wikimedia wiki).
"""

from __future__ import annotations

import os
import time
import urllib.parse
from typing import Any

import httpx

from vgi_wikipedia import __version__

DEFAULT_TIMEOUT = 15.0
"""Per-call HTTP timeout in seconds (connect + read)."""

MAX_RETRIES = 3
"""Total attempts for a single request (1 try + up to 2 retries)."""

RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
"""HTTP statuses that warrant a bounded retry with backoff."""

BACKOFF_BASE = 0.25
"""Base seconds for exponential backoff (0.25, 0.5, 1.0, ...)."""

# A real, identifying User-Agent: MediaWiki's API policy requires one.
DEFAULT_USER_AGENT = f"vgi-wikipedia/{__version__} (VGI DuckDB worker; +https://query.farm) httpx"


def default_api_url() -> str:
    """The default Action-API base, overridable by ``VGI_WIKIPEDIA_API_URL``.

    The value is the ``api.php`` endpoint (the search path). The REST summary
    base is derived from it (see :meth:`WikiClient.summary`).
    """
    return os.environ.get("VGI_WIKIPEDIA_API_URL") or "https://{lang}.wikipedia.org/w/api.php"


def user_agent() -> str:
    """The User-Agent header, overridable by ``VGI_WIKIPEDIA_USER_AGENT``."""
    return os.environ.get("VGI_WIKIPEDIA_USER_AGENT") or DEFAULT_USER_AGENT


class WikiError(RuntimeError):
    """A MediaWiki request failed in a way the worker surfaces as a clean error.

    The functions catch this and raise a tidy DuckDB error rather than letting
    an arbitrary exception escape and crash the worker process.
    """


class WikiClient:
    """A configured client for one wiki (one ``api_url`` template).

    ``api_url`` may contain a ``{lang}`` placeholder (the Wikipedia default,
    ``https://{lang}.wikipedia.org/w/api.php``); :meth:`_api` substitutes the
    requested language. A fixed URL with no placeholder targets a single wiki
    (any MediaWiki), in which case ``lang`` only labels the output rows.
    """

    def __init__(
        self,
        *,
        api_url: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        """Configure the API URL template, per-call timeout, and optional httpx client."""
        self.api_url_template = api_url or default_api_url()
        self.timeout = timeout
        self._client = client

    # -- URL building -------------------------------------------------------

    def _api(self, lang: str) -> str:
        """Resolve the Action-API URL for ``lang`` (substituting ``{lang}``)."""
        if "{lang}" in self.api_url_template:
            return self.api_url_template.format(lang=lang or "en")
        return self.api_url_template

    def _rest_summary_url(self, lang: str, title: str) -> str:
        """Derive the REST summary URL from the Action-API base.

        ``.../w/api.php`` -> ``.../api/rest_v1/page/summary/{title}``. For a
        non-Wikimedia base this best-effort derivation may 404; callers fall
        back to the Action-API extract.
        """
        api = self._api(lang)
        # Strip a trailing ``/w/api.php`` (the Wikimedia convention) to get the
        # site root, then append the REST path.
        root = api
        for suffix in ("/w/api.php", "/api.php"):
            if root.endswith(suffix):
                root = root[: -len(suffix)]
                break
        else:
            # No recognizable api.php suffix: drop the last path segment.
            root = api.rsplit("/", 1)[0]
        quoted = urllib.parse.quote(title.replace(" ", "_"), safe="")
        return f"{root}/api/rest_v1/page/summary/{quoted}"

    # -- HTTP ---------------------------------------------------------------

    def _http(self) -> httpx.Client:
        if self._client is not None:
            return self._client
        return httpx.Client(timeout=self.timeout)

    def _request(
        self,
        method: str,
        url: str,
        *,
        params: dict[str, Any] | None = None,
    ) -> httpx.Response:
        """Issue a request, retrying 429/5xx with exponential backoff.

        Raises :class:`WikiError` on a non-retryable HTTP error, exhausted
        retries, or a transport/timeout failure -- so callers only ever see a
        single, tidy exception type.
        """
        owns_client = self._client is None
        client = self._http()
        headers = {"User-Agent": user_agent(), "Accept": "application/json"}
        last_exc: Exception | None = None
        try:
            for attempt in range(MAX_RETRIES):
                try:
                    resp = client.request(method, url, params=params, headers=headers)
                except httpx.HTTPError as exc:  # timeout, connect error, ...
                    last_exc = exc
                else:
                    if resp.status_code in RETRY_STATUS and attempt < MAX_RETRIES - 1:
                        last_exc = WikiError(f"HTTP {resp.status_code}")
                    elif resp.is_error:
                        raise WikiError(f"HTTP {resp.status_code} {resp.text[:200]!r}")
                    else:
                        return resp
                if attempt < MAX_RETRIES - 1:
                    time.sleep(BACKOFF_BASE * (2**attempt))
            raise WikiError(f"request failed after {MAX_RETRIES} attempts") from last_exc
        finally:
            if owns_client:
                client.close()

    # -- API calls ----------------------------------------------------------

    def search(
        self,
        query: str,
        *,
        lang: str,
        count: int,
        sroffset: int,
    ) -> dict[str, Any]:
        """Call ``action=query&list=search`` and return the parsed JSON payload.

        Returns the raw JSON dict (parsing into rows happens in
        :mod:`vgi_wikipedia.parse`). Raises :class:`WikiError` on failure.
        """
        params = {
            "action": "query",
            "list": "search",
            "srsearch": query,
            "srlimit": max(1, min(count, 50)),
            "sroffset": max(0, sroffset),
            "srprop": "snippet|wordcount|timestamp",
            "format": "json",
            "formatversion": "2",
        }
        resp = self._request("GET", self._api(lang), params=params)
        try:
            payload: dict[str, Any] = resp.json()
        except Exception as exc:  # noqa: BLE001 - any decode error -> clean error
            raise WikiError(f"could not decode search response: {exc}") from exc
        if "error" in payload:
            info = payload["error"].get("info", "unknown error")
            raise WikiError(f"MediaWiki API error: {info}")
        return payload

    def summary(self, title: str, *, lang: str) -> dict[str, Any] | None:
        """Return the REST page-summary JSON, or ``None`` if the page is missing.

        Falls back to the Action-API ``prop=extracts`` shape (normalized to the
        REST summary keys this worker consumes) when REST is unavailable -- e.g.
        a non-Wikimedia wiki, or a 404 from the REST endpoint. Raises
        :class:`WikiError` only on a genuine transport/server failure.
        """
        url = self._rest_summary_url(lang, title)
        try:
            resp = self._request("GET", url)
        except WikiError:
            return self._summary_via_action(title, lang=lang)
        try:
            payload: dict[str, Any] = resp.json()
        except Exception:  # noqa: BLE001
            return self._summary_via_action(title, lang=lang)
        # REST returns type "https://mediawiki.org/wiki/HyperSwitch/errors/not_found"
        # for a missing page.
        if payload.get("type", "").endswith("not_found") or payload.get("title") == "Not found.":
            return None
        return payload

    def _summary_via_action(self, title: str, *, lang: str) -> dict[str, Any] | None:
        """Action-API extract fallback, normalized to the REST summary keys."""
        params = {
            "action": "query",
            "prop": "extracts|info|pageimages",
            "exintro": "1",
            "explaintext": "1",
            "inprop": "url",
            "piprop": "thumbnail",
            "pithumbsize": "320",
            "titles": title,
            "redirects": "1",
            "format": "json",
            "formatversion": "2",
        }
        resp = self._request("GET", self._api(lang), params=params)
        try:
            payload: dict[str, Any] = resp.json()
        except Exception as exc:  # noqa: BLE001
            raise WikiError(f"could not decode extract response: {exc}") from exc
        if "error" in payload:
            info = payload["error"].get("info", "unknown error")
            raise WikiError(f"MediaWiki API error: {info}")
        pages = (payload.get("query") or {}).get("pages") or []
        if not pages:
            return None
        page = pages[0]
        if page.get("missing"):
            return None
        thumb = page.get("thumbnail") or {}
        # Normalize to the subset of REST-summary keys the parser reads.
        return {
            "title": page.get("title"),
            "extract": page.get("extract"),
            "pageid": page.get("pageid"),
            "content_urls": {"desktop": {"page": page.get("fullurl") or page.get("canonicalurl")}},
            "thumbnail": {"source": thumb.get("source")} if thumb.get("source") else None,
        }
