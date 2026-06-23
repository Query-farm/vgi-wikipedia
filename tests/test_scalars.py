"""Unit tests for the ``wiki_page`` scalar (network mocked at the client).

Drives the scalar ``compute`` directly with a string column, with
:class:`~vgi_wikipedia.client.WikiClient.summary` patched to return fixture
payloads -- so a missing page / NULL / blank input yields NULL, and a real page
yields its extract, without any HTTP.
"""

from __future__ import annotations

import json
from pathlib import Path

import pyarrow as pa

from vgi_wikipedia import scalars
from vgi_wikipedia.scalars import WikiPage, WikiPageDefault

FIXTURES = Path(__file__).parent / "fixtures"


def _load(name: str) -> dict:
    return json.loads((FIXTURES / f"{name}.json").read_text())


def _patch(monkeypatch, mapping: dict[str, str | None]) -> None:
    """Patch WikiClient.summary to return the named fixture per title."""

    def fake_summary(self, title, *, lang):
        name = mapping.get(title)
        return None if name is None else _load(name)

    monkeypatch.setattr(scalars.WikiClient, "summary", fake_summary)


def test_default_returns_extract(monkeypatch) -> None:
    _patch(monkeypatch, {"DuckDB": "summary"})
    out = WikiPageDefault.compute(pa.array(["DuckDB"]))
    assert out.to_pylist()[0].startswith("DuckDB is an open-source")


def test_missing_page_is_null(monkeypatch) -> None:
    _patch(monkeypatch, {"DuckDB": "summary", "Nonexistent": None})
    out = WikiPageDefault.compute(pa.array(["Nonexistent"]))
    assert out.to_pylist() == [None]


def test_null_and_blank_input_are_null(monkeypatch) -> None:
    _patch(monkeypatch, {})
    out = WikiPageDefault.compute(pa.array([None, "", "   "]))
    assert out.to_pylist() == [None, None, None]


def test_lang_overload(monkeypatch) -> None:
    captured = {}

    def fake_summary(self, title, *, lang):
        captured["lang"] = lang
        return _load("summary")

    monkeypatch.setattr(scalars.WikiClient, "summary", fake_summary)
    out = WikiPage.compute(pa.array(["DuckDB"]), "de")
    assert out.to_pylist()[0].startswith("DuckDB is an open-source")
    assert captured["lang"] == "de"


def test_per_title_error_is_null_not_crash(monkeypatch) -> None:
    def boom(self, title, *, lang):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(scalars.WikiClient, "summary", boom)
    out = WikiPageDefault.compute(pa.array(["DuckDB"]))
    assert out.to_pylist() == [None]
