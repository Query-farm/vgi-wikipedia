# Copyright 2026 Query Farm LLC - https://query.farm

"""Serve one unchanged public Wikidata response for its exact recorded request."""

from __future__ import annotations

import gzip
import hashlib
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


class Replay:
    """Strict loopback response replay with a request audit and no outbound access."""

    def __init__(self) -> None:
        directory = Path(__file__).resolve().parent / "fixtures"
        self.manifest = json.loads((directory / "manifest.json").read_text())
        compressed = (directory / "wikidata-cities.json.gz").read_bytes()
        assert hashlib.sha256(compressed).hexdigest() == self.manifest["gzip_sha256"]
        self.raw = gzip.decompress(compressed)
        assert hashlib.sha256(self.raw).hexdigest() == self.manifest["raw_sha256"]
        self.requests: list[dict] = []
        self.rejected: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                parts = urlsplit(self.path)
                expected = {k: [v] for k, v in owner.manifest["request_parameters"].items()}
                if (
                    parts.path != "/w/api.php"
                    or parse_qs(parts.query, keep_blank_values=True) != expected
                ):
                    owner.rejected.append(self.path)
                    self.send_error(400, "Request does not match this recorded response")
                    return
                owner.requests.append(
                    {
                        "path": parts.path,
                        "parameters": parse_qs(parts.query),
                        "sha256": owner.manifest["raw_sha256"],
                    }
                )
                self.send_response(200)
                self.send_header("Content-Type", "application/json; charset=utf-8")
                self.send_header("Content-Length", str(len(owner.raw)))
                self.end_headers()
                self.wfile.write(owner.raw)

            def do_POST(self):
                owner.rejected.append(self.path)
                self.send_error(405, "This recording supports only its exact GET")

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.url = f"http://127.0.0.1:{self.server.server_port}/w/api.php"

    def close(self) -> None:
        """Stop only this notebook's disposable server."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)


class DiscoveryReplay:
    """Replay exact recorded Wikimedia GET/POST requests without outbound access."""

    def __init__(self, group: str) -> None:
        directory = Path(__file__).resolve().parent / "discovery_recordings"
        manifest = json.loads((directory / "manifest.json").read_text())
        allowed = {
            "articles": {
                "article-search",
                "summary-planet",
                "summary-element",
                "identity-planet",
                "identity-element",
            },
            "labels": {"entity-search", "typed-labels"},
        }[group]
        entries = []
        for entry in manifest["requests"]:
            if entry["id"] not in allowed:
                continue
            packed = (directory / entry["file"]).read_bytes()
            assert hashlib.sha256(packed).hexdigest() == entry["gzip_sha256"]
            raw = gzip.decompress(packed)
            assert hashlib.sha256(raw).hexdigest() == entry["raw_sha256"]
            entries.append((entry, raw))
        assert len(entries) == len(allowed)
        self.requests: list[str] = []
        self.rejected: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def answer(self):
                parts = urlsplit(self.path)
                body = (
                    self.rfile.read(int(self.headers.get("Content-Length", "0")))
                    if self.command == "POST"
                    else b""
                )
                parameters = parse_qs(
                    body.decode() if body else parts.query, keep_blank_values=True
                )
                for entry, raw in entries:
                    expected = {key: [value] for key, value in entry["parameters"].items()}
                    if (
                        self.command == entry["method"]
                        and parts.path == entry["path"]
                        and parameters == expected
                    ):
                        owner.requests.append(entry["id"])
                        self.send_response(200)
                        self.send_header("Content-Type", "application/json; charset=utf-8")
                        self.send_header("Content-Length", str(len(raw)))
                        self.end_headers()
                        self.wfile.write(raw)
                        return
                owner.rejected.append(self.command + " " + parts.path)
                self.send_error(400, "Request does not match this recorded response")

            do_GET = answer
            do_POST = answer

            def log_message(self, format, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.server.daemon_threads = True
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"

    def close(self) -> None:
        """Stop this replay server and release its listening socket."""
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
