"""A tiny canned-response MediaWiki server for deterministic E2E tests.

Serves the captured fixture JSON at the paths the worker's :class:`WikiClient`
hits, so a real HTTP round-trip through httpx runs with no network, no keys, and
no cost:

* ``GET /w/api.php?action=query&list=search&sroffset=N`` -> the search fixtures.
  ``sroffset=0`` returns ``search_page1`` (which carries ``continue.sroffset=3``);
  ``sroffset=3`` returns ``search_page2`` (no continue token -> exhausted). This
  is what exercises the scan-state batch-boundary round-trip end to end.
* ``GET /api/rest_v1/page/summary/{Title}`` -> ``summary`` (or the not-found
  fixture for an unknown title), so ``wiki_page`` / ``wiki_page_summary`` run the
  full REST path.
* ``/flaky`` returns 503 a configurable number of times before 200 (bounded
  retry/backoff path); ``/boom`` always 500s.

Used by the pytest mock-server E2E (:mod:`tests.test_mock_e2e`) and, run as
``python -m tests.mock_server``, by the haybarn SQL E2E launcher.
"""

from __future__ import annotations

import json
import sys
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

FIXTURES = Path(__file__).parent / "fixtures"


def _fixture(name: str) -> bytes:
    return (FIXTURES / f"{name}.json").read_bytes()


class _Handler(BaseHTTPRequestHandler):
    def log_message(self, *args: Any) -> None:  # silence default stderr spam
        pass

    def _send(self, status: int, body: bytes) -> None:
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:  # noqa: N802
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        qs = urllib.parse.parse_qs(parsed.query)

        if path == "/boom":
            self._send(500, b'{"error": "boom"}')
            return

        if path == "/flaky":
            server: Any = self.server
            with server.flaky_lock:
                if server.flaky_remaining > 0:
                    server.flaky_remaining -= 1
                    self._send(503, b'{"error": "try later"}')
                    return
            self._send(200, _fixture("search_page1"))
            return

        # Action API: api.php (the {lang} template resolves to /w/api.php here).
        if path.endswith("/api.php") or path.endswith("/w/api.php"):
            action = (qs.get("action") or [""])[0]
            if action == "query" and "search" in (qs.get("list") or []):
                sroffset = int((qs.get("sroffset") or ["0"])[0])
                name = "search_page2" if sroffset >= 3 else "search_page1"
                self._send(200, _fixture(name))
                return
            if action == "query" and "extracts" in ",".join(qs.get("prop") or []):
                # Action-API extract fallback (when REST is unreachable).
                titles = (qs.get("titles") or [""])[0]
                if titles.replace("_", " ").lower() == "duckdb":
                    self._send(200, _fixture("extract_action"))
                else:
                    self._send(
                        200,
                        b'{"batchcomplete": true, "query": {"pages": [{"title": "x", "missing": true}]}}',
                    )
                return
            self._send(200, b'{"batchcomplete": true, "query": {"search": []}}')
            return

        # REST summary: /api/rest_v1/page/summary/{Title}
        if "/page/summary/" in path:
            title = urllib.parse.unquote(path.rsplit("/", 1)[-1])
            if title.replace("_", " ").lower() in ("duckdb", "sparse page"):
                name = "summary_minimal" if "sparse" in title.lower() else "summary"
                self._send(200, _fixture(name))
                return
            self._send(404, _fixture("summary_notfound"))
            return

        self._send(404, b'{"error": "no route"}')


class MockServer:
    """A threaded canned-response MediaWiki server with a stable ``base`` URL."""

    def __init__(self, flaky: int = 0) -> None:
        self._httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
        self._httpd.flaky_lock = threading.Lock()  # type: ignore[attr-defined]
        self._httpd.flaky_remaining = flaky  # type: ignore[attr-defined]
        self._thread = threading.Thread(target=self._httpd.serve_forever, daemon=True)

    @property
    def base(self) -> str:
        host, port = self._httpd.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def api_url(self) -> str:
        """The api.php URL to hand the worker as VGI_WIKIPEDIA_API_URL."""
        return f"{self.base}/w/api.php"

    def __enter__(self) -> MockServer:
        self._thread.start()
        return self

    def __exit__(self, *exc: object) -> None:
        self._httpd.shutdown()
        self._httpd.server_close()
        self._thread.join(timeout=2)


def main() -> None:
    """Run the mock server in the foreground, printing ``PORT:<n>`` once bound.

    The haybarn launcher reads the chosen port from this line. Stays up until
    killed.
    """
    flaky = int(sys.argv[1]) if len(sys.argv) > 1 else 0
    srv = MockServer(flaky=flaky)
    srv._thread.start()
    port = srv._httpd.server_address[1]
    print(f"PORT:{port}", flush=True)
    try:
        srv._thread.join()
    except KeyboardInterrupt:
        srv._httpd.shutdown()


if __name__ == "__main__":
    main()


def fixture_json(name: str) -> dict[str, Any]:
    """Re-export for tests that just want the JSON shapes."""
    return json.loads(_fixture(name))
