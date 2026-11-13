"""Run strict VGI metadata checks against local API fixtures, without live data."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.mock_server import MockServer  # noqa: E402


def main() -> int:
    """Keep the fixture server alive for metadata examples and private SQL graders."""
    with MockServer() as server:
        environment = {
            **os.environ,
            "VGI_WIKIPEDIA_API_URL": server.api_url,
            "VGI_WIKIDATA_API_URL": server.api_url,
            "VGI_WIKIDATA_SPARQL_URL": server.base + "/sparql",
        }
        return subprocess.call(
            [
                "uvx",
                "--prerelease=allow",
                "--from",
                "vgi-lint-check>=2,<3",
                "vgi-lint",
                "lint",
                str(ROOT / ".venv/bin/vgi-wikipedia-worker"),
                "--fail-on",
                "info",
            ],
            cwd=ROOT,
            env=environment,
        )


if __name__ == "__main__":
    raise SystemExit(main())
