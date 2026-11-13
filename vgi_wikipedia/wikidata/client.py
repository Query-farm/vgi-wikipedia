"""Bounded Wikibase HTTP access with batching, retries and a small TTL cache."""

from __future__ import annotations

import json
import os
import threading
import time
from collections import OrderedDict
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Any
from urllib.parse import urlsplit

import httpx

from vgi_wikipedia.client import RETRY_STATUS, WikiError, user_agent

_CACHE: OrderedDict[str, tuple[float, bytes]] = OrderedDict()
_LOCK = threading.Lock()
_CACHE_BYTES = 32 * 1024 * 1024


def clear_cache() -> None:
    """Clear the process-local response cache (also useful for deterministic tests)."""
    with _LOCK:
        _CACHE.clear()


def endpoint(value: str, variable: str, default: str) -> str:
    """Resolve an explicit or configured HTTP endpoint without embedded credentials."""
    value = value or os.environ.get(variable, default)
    parsed = urlsplit(value)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.fragment
    ):
        raise ValueError("Endpoint must be an HTTP(S) URL without credentials or a fragment")
    return value


def retry_delay(value: str | None, attempt: int) -> float:
    """Honor numeric or HTTP-date Retry-After; otherwise use exponential backoff."""
    if value:
        try:
            return max(0.0, float(value))
        except ValueError:
            try:
                return max(0.0, (parsedate_to_datetime(value) - datetime.now(UTC)).total_seconds())
            except (ValueError, TypeError, OverflowError):
                pass
    return float(0.25 * 2**attempt)


class WikidataClient:
    """One operation's shared request budget; cache hits never alter the stored response."""

    def __init__(self, *, timeout_ms: int = 30000, max_bytes: int = 16 * 1024 * 1024, cache_seconds: int = 300) -> None:
        """Configure the elapsed-time budget, response ceiling and optional cache freshness."""
        self.deadline = time.monotonic() + timeout_ms / 1000
        self.max_bytes = max_bytes
        self.cache_seconds = cache_seconds
        self.received = 0

    def request(self, url: str, parameters: dict[str, Any], *, sparql: bool = False) -> dict[str, Any]:
        """Read one complete JSON response, rejecting partial, oversized or failed requests."""
        key = json.dumps([url, parameters, sparql], sort_keys=True)
        if self.cache_seconds:
            with _LOCK:
                cached = _CACHE.get(key)
                if cached and time.monotonic() - cached[0] <= self.cache_seconds:
                    _CACHE.move_to_end(key)
                    self._count(len(cached[1]))
                    return dict(json.loads(cached[1]))
        headers = {
            "User-Agent": user_agent(),
            "Accept": "application/sparql-results+json" if sparql else "application/json",
        }
        body: bytes | None = None
        last_error = "request failed"
        with httpx.Client(follow_redirects=False) as client:
            for attempt in range(3):
                remaining = self.deadline - time.monotonic()
                if remaining <= 0:
                    raise WikiError("Wikidata request exceeded timeout_ms")
                delay = 0.25 * 2**attempt
                try:
                    with client.stream(
                        "POST" if sparql else "GET",
                        url,
                        params=None if sparql else parameters,
                        data=parameters if sparql else None,
                        headers=headers,
                        timeout=remaining,
                    ) as response:
                        delay = retry_delay(response.headers.get("Retry-After"), attempt)
                        if response.status_code in RETRY_STATUS:
                            last_error = f"Wikidata endpoint returned HTTP {response.status_code}"
                        elif response.status_code != 200:
                            raise WikiError(
                                f"Wikidata endpoint returned HTTP {response.status_code}; no partial results accepted"
                            )
                        elif "X-SPARQL-MaxRows" in response.headers or "Content-Range" in response.headers:
                            raise WikiError("Endpoint reported an incomplete SPARQL result")
                        else:
                            content = bytearray()
                            for chunk in response.iter_bytes():
                                self._count(len(chunk))
                                content.extend(chunk)
                                if time.monotonic() > self.deadline:
                                    raise WikiError("Wikidata request exceeded timeout_ms")
                            body = bytes(content)
                    if body is not None:
                        try:
                            payload = json.loads(body)
                        except (ValueError, UnicodeError) as error:
                            raise WikiError("Wikidata endpoint returned invalid JSON") from error
                        if not isinstance(payload, dict):
                            raise WikiError("Wikidata endpoint returned a non-object response")
                        if payload.get("error"):
                            api_error = payload["error"]
                            code = api_error.get("code", "unknown") if isinstance(api_error, dict) else "unknown"
                            if code not in {"maxlag", "ratelimited", "readonly"}:
                                raise WikiError(f"Wikidata API error: {code}")
                            last_error = f"Wikidata API error: {code}"
                            body = None
                        elif payload.get("warnings"):
                            raise WikiError("Wikidata API returned warnings; refusing potentially incomplete results")
                        else:
                            if self.cache_seconds and len(body) <= _CACHE_BYTES:
                                with _LOCK:
                                    _CACHE[key] = (time.monotonic(), body)
                                    _CACHE.move_to_end(key)
                                    while len(_CACHE) > 128 or sum(len(v[1]) for v in _CACHE.values()) > _CACHE_BYTES:
                                        _CACHE.popitem(last=False)
                            return payload
                except httpx.HTTPError as error:
                    last_error = f"Wikidata transport failure: {type(error).__name__}"
                if attempt == 2 or delay >= self.deadline - time.monotonic():
                    raise WikiError(last_error + "; retry budget exhausted")
                time.sleep(delay)
        raise WikiError(last_error)

    def _count(self, size: int) -> None:
        if time.monotonic() > self.deadline:
            raise WikiError("Wikidata request exceeded timeout_ms")
        self.received += size
        if self.received > self.max_bytes:
            raise WikiError("Wikidata response exceeds max_bytes")

    def entities(self, ids: list[str], api_url: str) -> list[tuple[str, dict[str, Any]]]:
        """Fetch up to fifty distinct item/property IDs per request, preserving input order."""
        output = []
        for start in range(0, len(ids), 50):
            batch = ids[start : start + 50]
            payload = self.request(
                api_url,
                {
                    "action": "wbgetentities",
                    "ids": "|".join(batch),
                    "props": "labels|descriptions|aliases|sitelinks|claims|info",
                    "redirects": "yes",
                    "format": "json",
                    "maxlag": "5",
                },
            )
            entities = payload.get("entities")
            if not isinstance(entities, dict):
                raise WikiError("Wikibase response is missing entities")
            redirects = {r["from"]: r["to"] for r in payload.get("redirects", [])}
            for requested in batch:
                target = requested
                seen = set()
                while target in redirects:
                    if target in seen:
                        raise WikiError("Wikibase entity redirect cycle")
                    seen.add(target)
                    target = redirects[target]
                entity = entities.get(target, entities.get(requested))
                if not isinstance(entity, dict):
                    raise WikiError(f"Wikibase response omitted requested entity {requested}")
                output.append((requested, entity))
        return output
