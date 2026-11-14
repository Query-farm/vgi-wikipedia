# Copyright 2026 Query Farm LLC - https://query.farm

"""VGI worker exposing the ``wiki`` catalog (Wikipedia / MediaWiki) to DuckDB.

Assembles the functions in ``vgi_wikipedia`` into a single ``wiki`` catalog and
runs the worker over stdio (a DuckDB subprocess) or HTTP.

This is an **egress connector** over the FREE official MediaWiki Action API +
REST summary endpoint -- clean, no key, no scraping. Great for RAG / knowledge
grounding; pairs with vgi-embed / vgi-rerank.

DuckDB spawns this over stdio and ATTACHes it::

    INSTALL vgi FROM community; LOAD vgi;
    ATTACH 'wiki' (TYPE vgi, LOCATION 'vgi-wikipedia-worker');

    SELECT title, snippet, url
      FROM wiki.wiki_search('apache arrow', lang := 'en', count := 10);
    SELECT wiki.wiki_page('DuckDB');
    SELECT * FROM wiki.wiki_page_summary('DuckDB', lang := 'en');

Configuration (env, optional):
    VGI_WIKIPEDIA_API_URL      override the api.php base (e.g. a non-Wikimedia
                               wiki, or a mock server). May contain ``{lang}``.
    VGI_WIKIPEDIA_USER_AGENT   override the (required) descriptive User-Agent.

LICENSING: the worker code is MIT. CONTENT retrieved from Wikipedia / Wikimedia
projects is **CC-BY-SA** (text) -- attribution and share-alike are the user's
responsibility. Wikidata structured data is CC0. See README.md.
"""

from __future__ import annotations

import json

from vgi import Worker
from vgi.catalog import Catalog, Schema

from vgi_wikipedia.composed import MAIN_SCHEMA_COMPOSED
from vgi_wikipedia.meta import keywords_json
from vgi_wikipedia.scalars import SCALAR_FUNCTIONS
from vgi_wikipedia.tables import TABLE_FUNCTIONS
from vgi_wikipedia.tables_page import PAGE_TABLE_FUNCTIONS
from vgi_wikipedia.wikidata.tables import WIKIDATA_SCHEMA, WikiEntity

_CATALOG_DESCRIPTION_LLM = (
    "Search Wikipedia, read article summaries and enrich local SQL data with Wikidata knowledge. "
    "Article identity links encyclopedic text to structured entities; statements preserve typed values, "
    "ranks, qualifiers and sources. Graph queries express relationships using remote SPARQL. "
    "This read-only connector uses public MediaWiki and Wikibase APIs without an API key. "
    "Article text is CC-BY-SA; Wikidata structured data is CC0. Custom sites may have different terms."
)

_CATALOG_DESCRIPTION_MD = (
    "# Wikipedia and Wikidata in SQL\n\n"
    "Connect encyclopedic articles to structured knowledge and join the results to local SQL tables. "
    "Use it for retrieval-augmented generation, fact lookup and entity enrichment. "
    "Article identity provides a reliable bridge to Wikidata without guessing from similar labels.\n\n"
    "## Data and context\n\n"
    "Statements retain their rank, typed value, qualifiers and reference sources. Source revisions help "
    "identify observations taken at different times. Remote SPARQL supports graph patterns and property paths; "
    "RDF terms retain their lexical value, datatype and language. Results are bounded and cached locally.\n\n"
    "## Access\n\n"
    "The worker calls the free [MediaWiki API](https://www.mediawiki.org/wiki/API:Main_page) and "
    "[Wikibase API](https://www.mediawiki.org/wiki/Wikibase/API). No API key is required. "
    "Queries require outbound access; language and endpoint options select the upstream site. "
    "Public endpoint availability and dataset coverage affect results.\n\n"
    "Worker code is MIT. Wikipedia article text is **CC-BY-SA**; callers handle attribution and "
    "share-alike requirements. Wikidata structured data is **CC0**. Custom endpoints have their own terms."
)

_SCHEMA_DESCRIPTION_LLM = (
    "Discover Wikipedia articles, retrieve summaries and resolve article identity for knowledge enrichment. "
    "Language-selectable MediaWiki APIs provide article text, canonical source URLs and associated Wikibase IDs. "
    "Use article identity to connect text with structured knowledge. Missing pages have explicit null/empty "
    "semantics documented on each function. Retrieved Wikipedia article text is CC-BY-SA."
)

_SCHEMA_DESCRIPTION_MD = (
    "# Article discovery and identity\n\n"
    "Find encyclopedic context for local records or a retrieval pipeline. Results carry canonical URLs "
    "for attribution and language selection determines the wiki edition.\n\n"
    "## Connecting knowledge\n\n"
    "Known article titles resolve through redirects to their associated Wikibase item, providing a bridge "
    "from prose to structured knowledge. Labels alone are not reliable entity identifiers. Custom MediaWiki "
    "sites may point at their own Wikibase repository.\n\n"
    "Wikipedia article text is **CC-BY-SA**; attribution and share-alike are the caller's responsibility."
)

# VGI413/VGI408-412: the schema's ordered category registry (navigation / SEO).
# Each object below declares a `vgi.category` naming one of these; both categories
# have >=1 member so none is a dead section (VGI412).
_SCHEMA_CATEGORIES = json.dumps(
    [
        {
            "name": "search",
            "description": (
                "Full-text search over Wikipedia and any MediaWiki wiki, returning "
                "ranked article results (titles, snippets, page ids, URLs)."
            ),
        },
        {
            "name": "page-retrieval",
            "description": (
                "Fetch a single known page's plain-text summary extract or its rich "
                "multi-column summary row (extract, URL, thumbnail, page id)."
            ),
        },
    ],
    ensure_ascii=False,
)

# VGI515: schema-level example_queries must be a JSON list of {description, sql}.
_SCHEMA_EXAMPLE_QUERIES = json.dumps(
    [
        {
            "description": "Full-text search English Wikipedia for 'apache arrow' (top 10).",
            "sql": ("SELECT title, snippet, url FROM wiki.main.wiki_search('apache arrow', lang := 'en', count := 10)"),
        },
        {
            "description": "Fetch the English Wikipedia summary extract for the DuckDB article.",
            "sql": "SELECT wiki.main.wiki_page('DuckDB')",
        },
        {
            "description": "Fetch the rich English summary row (title, extract, url) for DuckDB.",
            "sql": "SELECT title, extract, url FROM wiki.main.wiki_page_summary('DuckDB', lang := 'en')",
        },
        *MAIN_SCHEMA_COMPOSED,
    ],
    ensure_ascii=False,
)

_CATALOG_KEYWORDS = [
    "wikipedia",
    "mediawiki",
    "wiki",
    "search",
    "full-text search",
    "page",
    "summary",
    "extract",
    "article",
    "encyclopedia",
    "rag",
    "retrieval",
    "knowledge grounding",
    "fact lookup",
    "egress connector",
]

_SCHEMA_KEYWORDS = [
    "wikipedia",
    "mediawiki",
    "wiki_search",
    "wiki_page",
    "wiki_page_summary",
    "search",
    "page summary",
    "extract",
    "article",
    "encyclopedia",
    "rag",
    "retrieval",
    "knowledge grounding",
    "lang",
    "language",
]

# VGI152: fixed analyst-task suite for `vgi-lint simulate`. Only each task's
# `name` and `prompt` are published here (VGI416); the grader's canonical
# `reference_sql` and grading options live in `vgi-agent-tests.yaml` at the repo
# root, merged by task name. References are chosen to be DETERMINISTIC against
# live Wikipedia (page existence and resolved titles are stable, unlike article
# text): each resolves to a boolean or a fixed title.
_AGENT_TEST_TASKS = json.dumps(
    [
        {
            "name": "page_summary_title",
            "prompt": (
                "Using the wiki catalog, fetch the Wikipedia page summary for the "
                "article titled 'DuckDB' and return just its resolved page title."
            ),
        },
        {
            "name": "missing_page_is_null",
            "prompt": (
                "Using the wiki catalog's scalar page-extract function, determine "
                "whether the English Wikipedia article 'ThisPageDoesNotExist_zzz' is "
                "missing. Return a single boolean that is true when the page has no "
                "summary."
            ),
        },
        {
            "name": "search_found",
            "prompt": (
                "Using the wiki catalog, run a full-text search of English Wikipedia "
                "for 'apache arrow' (limit to 3 results) and return a single boolean "
                "that is true when at least one matching article was found."
            ),
        },
        {
            "name": "page_has_extract",
            "prompt": (
                "Using the wiki catalog, return a single boolean that is true when the "
                "English Wikipedia article 'DuckDB' has a non-empty summary extract."
            ),
        },
    ],
    ensure_ascii=False,
)

_AGENT_TEST_TASKS = json.dumps(
    json.loads(_AGENT_TEST_TASKS)
    + [
        {
            "name": "article_wikidata_id",
            "prompt": "Return the Wikidata ID associated with the English Wikipedia article 'Douglas Adams'.",
        },
        {"name": "wikidata_label", "prompt": "Return the English label for Wikidata entity Q42."},
        {
            "name": "wikidata_birth",
            "prompt": "Return Q42's birth-date string from P569, preserving the upstream time representation.",
        },
        {
            "name": "wikidata_qualifiers",
            "prompt": "For Q42's spouse statements (P26), list the distinct qualifier property IDs in property order.",
        },
        {
            "name": "wikidata_sources",
            "prompt": "Return whether Q42's instance-of statements (P31) have any reference snaks.",
        },
        {
            "name": "wikidata_discovery",
            "prompt": "Search Wikidata for 'Douglas Adams' with three candidates; return whether any were found.",
        },
        {
            "name": "wikidata_graph",
            "prompt": "Use SPARQL ASK to check whether Wikidata Q42 has any outgoing RDF triples. Return one boolean.",
        },
        {
            "name": "article_birth_date",
            "prompt": (
                "Starting from the English Wikipedia article 'Ada Lovelace', find the Wikidata item it is "
                "linked to and return that item's date of birth (P569). Return one row with one VARCHAR "
                "column: the upstream Wikibase time string, unchanged."
            ),
        },
        {
            "name": "spouse_label_and_start",
            "prompt": (
                "For Douglas Adams (Wikidata Q42), return the English label of his spouse (P26) and the "
                "marriage start time from that statement's P580 qualifier. Return one row with two VARCHAR "
                "columns in this order: spouse label, then the upstream Wikibase time string unchanged."
            ),
        },
    ]
)

_CATALOG_TAGS = {
    "vgi.title": "Wikipedia and Wikidata",
    "vgi.subtitle": "Connect Wikipedia articles with Wikidata statements and graph queries from SQL.",
    "vgi.agent_test_tasks": _AGENT_TEST_TASKS,
    "vgi.keywords": keywords_json(_CATALOG_KEYWORDS),
    "vgi.doc_llm": _CATALOG_DESCRIPTION_LLM,
    "vgi.doc_md": _CATALOG_DESCRIPTION_MD,
    "vgi.author": "Query.Farm",
    "vgi.copyright": "Copyright 2026 Query Farm LLC - https://query.farm",
    "vgi.license": "MIT",
    "vgi.support_contact": "https://github.com/Query-farm/vgi-wikipedia/issues",
    "vgi.support_policy_url": "https://github.com/Query-farm/vgi-wikipedia/blob/main/README.md",
}

_WIKI_CATALOG = Catalog(
    name="wiki",
    default_schema="main",
    comment="Wikipedia articles, Wikidata statements and graph queries for SQL",
    tags=_CATALOG_TAGS,
    source_url="https://github.com/Query-farm/vgi-wikipedia",
    schemas=[
        Schema(
            path=["main"],
            comment="Wikipedia / MediaWiki full-text search and page retrieval for SQL / RAG",
            tags={
                "vgi.title": "Wikipedia functions",
                "vgi.keywords": keywords_json(_SCHEMA_KEYWORDS),
                "vgi.doc_llm": _SCHEMA_DESCRIPTION_LLM,
                "vgi.doc_md": _SCHEMA_DESCRIPTION_MD,
                # VGI413: ordered category registry; each function names one via vgi.category.
                "vgi.categories": _SCHEMA_CATEGORIES,
                # VGI139: source_url is kept on the catalog object only.
                "vgi.example_queries": _SCHEMA_EXAMPLE_QUERIES,
                # VGI123 classifying tags -- BARE keys (not vgi.-namespaced).
                "domain": "knowledge",
                "category": "search",
                "topic": "wikipedia-retrieval",
            },
            functions=[*SCALAR_FUNCTIONS, *TABLE_FUNCTIONS, *PAGE_TABLE_FUNCTIONS, WikiEntity],
        ),
        WIKIDATA_SCHEMA,
    ],
)


class WikipediaWorker(Worker):
    """Worker process hosting the ``wiki`` catalog."""

    catalog = _WIKI_CATALOG


def main() -> None:
    """Run the Wikipedia worker process (stdio or, via flags, HTTP)."""
    WikipediaWorker.main()


if __name__ == "__main__":
    main()
