"""Join local ratings to Wikidata, inspect statement context, and traverse a graph.

Run ``uv run --extra dev python examples/wikidata_demo.py`` for local fixture
data, or add ``--live`` to query Wikipedia and Wikidata. The VGI extension must
be installed or downloadable from Query.Farm in either mode.
"""

from __future__ import annotations

import argparse
import shlex
import sys
from contextlib import nullcontext
from pathlib import Path

import duckdb

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from tests.mock_server import MockServer  # noqa: E402


def literal(value: str) -> str:
    """Quote one SQL string literal, including arbitrary query or endpoint text."""
    return "'" + value.replace("'", "''") + "'"


def main() -> None:
    """Execute the same SQL pipeline against fixtures or the public APIs."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--live", action="store_true", help="Query the public Wikipedia and Wikidata services")
    args = parser.parse_args()
    with nullcontext(None) if args.live else MockServer() as server, duckdb.connect() as db:
        db.execute("SET allow_extension_repositories='allowed'")
        db.execute(
            "CREATE EXTENSION REPOSITORY IF NOT EXISTS query_farm WITH PREFIX 'https://duckdb-extensions.query.farm'"
        )
        db.execute("INSTALL vgi FROM query_farm")
        db.execute("LOAD vgi FROM query_farm")
        command = shlex.join([sys.executable, str(ROOT / "wikipedia_worker.py")])
        db.execute(f"ATTACH 'wiki' (TYPE vgi, LOCATION {literal(command)})")
        api = ", api_url := " + literal(server.api_url) if server else ""
        endpoint = ", endpoint_url := " + literal(server.base + "/sparql") if server else ""

        entity_id = db.execute(f"SELECT entity_id FROM wiki.main.wiki_entity('Douglas Adams'{api})").fetchone()[0]
        ids = "[" + literal(entity_id) + "]"
        db.execute("CREATE TABLE reading_list(entity_id VARCHAR, rating INTEGER)")
        db.execute("INSERT INTO reading_list VALUES (?, 5)", [entity_id])
        queries = [
            (
                "Reading list enriched with labels",
                f"SELECT e.entity_id, e.label, e.label_language, r.rating "
                f"FROM reading_list r JOIN wiki.wikidata.entity({ids}{api}) e USING (entity_id)",
            ),
            (
                "Birth date and precision",
                f"SELECT value.time, value.time_precision, value.calendar_model "
                f"FROM wiki.wikidata.statements({ids}, property_id := 'P569'{api})",
            ),
            (
                "Spouse and qualifiers",
                f"SELECT s.value.entity_id AS spouse, q.property_id, q.value.time, q.value.entity_id "
                f"FROM wiki.wikidata.statements({ids}, property_id := 'P26'{api}) s "
                f"JOIN wiki.wikidata.qualifiers({ids}, property_id := 'P26'{api}) q "
                "USING (requested_id, statement_id, entity_revision, source_api) ORDER BY q.property_id",
            ),
            (
                "Reference sources",
                f"SELECT reference_hash, property_id, value.entity_id, value.text "
                f"FROM wiki.wikidata.statement_references({ids}, property_id := 'P26'{api}) "
                "ORDER BY reference_hash, property_id",
            ),
        ]
        graph = (
            "SELECT DISTINCT ?ancestor WHERE { <http://www.wikidata.org/entity/" + entity_id + "> "
            "<http://www.wikidata.org/prop/direct/P31>/<http://www.wikidata.org/prop/direct/P279>* ?ancestor "
            "} LIMIT 20"
        )
        queries.append(
            (
                "Instance-of / subclass-of graph traversal",
                f"SELECT ancestor.value FROM wiki.wikidata.sparql({literal(graph)}{endpoint}) ORDER BY ancestor.value",
            )
        )
        for title, query in queries:
            print(f"\n{title}\n{query}")
            rows = db.execute(query).fetchall()
            if not rows:
                raise RuntimeError(f"Demo expected data for {title}")
            for row in rows:
                print(row)
        db.execute("DETACH wiki")


if __name__ == "__main__":
    main()
