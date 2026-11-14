"""Recorded-source setup; analytical SQL and figures remain in each notebook."""

from datetime import UTC, datetime
import json
import os
from pathlib import Path

import certifi

from replay import DiscoveryReplay, Replay

_MANAGED = (
    "VGI_WIKIPEDIA_API_URL",
    "VGI_WIKIDATA_API_URL",
    "VGI_WIKIDATA_SPARQL_URL",
    "SSL_CERT_FILE",
)


def configure(story: str, parameter: object, mode: str):
    """Validate visible controls and select only the explicitly requested source."""
    if mode not in ("recorded", "live"):
        raise ValueError("DATA_MODE must be recorded or live.")
    if story == "population":
        if type(parameter) is not int or not 1800 <= parameter <= 2025:
            raise ValueError("MIN_YEAR must be an integer from 1800 through 2025.")
    elif story == "articles":
        if type(parameter) is not int or not 1 <= parameter <= 1000:
            raise ValueError("CONTEXT_CHARS must be an integer from 1 through 1000.")
    elif story == "labels":
        if parameter not in ("en", "fr"):
            raise ValueError("LABEL_LANGUAGE must be en or fr.")
    else:
        raise ValueError("Unknown example story")
    previous = {key: os.environ.get(key) for key in _MANAGED}
    for key in _MANAGED[:-1]:
        os.environ.pop(key, None)
    os.environ.setdefault("SSL_CERT_FILE", certifi.where())
    if mode == "recorded":
        replay = Replay() if story == "population" else DiscoveryReplay(story)
    else:
        replay = None
    article_api = "https://en.wikipedia.org/w/api.php"
    entity_api = "https://www.wikidata.org/w/api.php"
    sparql_api = "https://query.wikidata.org/sparql"
    if replay is not None:
        if story == "population":
            entity_api = replay.url
        else:
            article_api = entity_api = replay.base + "/w/api.php"
            sparql_api = replay.base + "/sparql"
    os.environ["VGI_WIKIPEDIA_API_URL"] = article_api
    os.environ["VGI_WIKIDATA_API_URL"] = entity_api
    os.environ["VGI_WIKIDATA_SPARQL_URL"] = sparql_api
    directory = Path(__file__).resolve().parent
    manifest_path = (
        directory
        / ("fixtures" if story == "population" else "discovery_recordings")
        / "manifest.json"
    )
    manifest = json.loads(manifest_path.read_text())
    return {
        "replay": replay,
        "previous": previous,
        "manifest": manifest,
        "article_api": article_api,
        "entity_api": entity_api,
        "sparql_api": sparql_api,
        "run_started": datetime.now(UTC).isoformat(),
        "query": (directory / "discovery_query.rq").read_text() if story == "labels" else None,
    }


def cleanup(state):
    """Close this server and restore only environment keys changed during setup."""
    if state["replay"] is not None:
        state["replay"].close()
    for key, value in state["previous"].items():
        if value is None:
            os.environ.pop(key, None)
        else:
            os.environ[key] = value
