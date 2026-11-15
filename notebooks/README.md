# Wikipedia and Wikidata notebooks

| Notebook | Question | Controlled experiment |
| --- | --- | --- |
| [01 · Population statements need dates and statement keys](01_population_statements_and_join_grain.ipynb) | Keep dates and reference groups attached to the correct population statement; measure row multiplication from unaggregated context. | `MIN_YEAR`: 2000 → 2010 |
| [02 · A context budget changes text length, not article identity](02_budget_context_without_changing_identity.ipynb) | Discover Mercury articles, reconcile page/item IDs and scalar/table summary readers, then retain a bounded text prefix. | `CONTEXT_CHARS`: 400 → 800 |
| [03 · Labels are presentation; item IRIs are identity](03_labels_are_not_item_keys.ipynb) | Select two discovered Wikidata items, inspect typed/unbound SPARQL values and show why lowercased labels cannot identify them uniquely. | `LABEL_LANGUAGE`: `en` → `fr` |

Each book is independent, uses recorded official public responses by default, and
has visible analytical SQL plus two SVG figures. No synthetic population, article,
entity or graph result is substituted.

## Setup and execution

Install [uv](https://docs.astral.sh/uv/) and Git, then run from this directory:

```sh
uv sync --locked
uv run --locked vgi-notebooks --timeout 600
uv run --locked vgi-notebooks 01_population_statements_and_join_grain.ipynb \
  --parameters '{"MIN_YEAR":2010}' --output-dir artifacts/since-2010
uv run --locked vgi-notebooks 02_budget_context_without_changing_identity.ipynb \
  --parameters '{"CONTEXT_CHARS":800}' --output-dir artifacts/context-800
uv run --locked vgi-notebooks 03_labels_are_not_item_keys.ipynb \
  --parameters '{"LABEL_LANGUAGE":"fr"}' --output-dir artifacts/french
```

The shared [v0.3.38 notebook package](https://github.com/Query-farm/vgi-example-notebooks/tree/v0.3.38)
provides the runner, credential isolation, UTC parent connection and presentation.
Local dependencies are locked, including Haybarn **2.0.0rc11**. Open-in-Colab links
appear at the top of each notebook. Colab setup installs only the shared base
package; the `[runner]` extra remains local. Three fresh notebook-only Python 3.12
runs with plotting and Jupyter libraries already imported passed without replacing
their versions. This verifies local preloaded-kernel compatibility, not a manually
executed hosted Colab session.

The `wikipedia` registry verifies thirteen companion files from immutable commit
`d85e73b77f7a82954472cb24f409d9fa866c63c7`. A notebook opened alone downloads its
helpers, recordings and query without cloning the worker. Restart the kernel and
run all when changing source modes or switching books. The publication runner
preserves successful defaults and requires a separate output directory for parameter
overrides. Native SVG supports Jupyter/Colab; saved content-addressed image links
also support GitHub's notebook renderer.

Both recorded and live modes visibly attach the actual published release:

```text
uvx --python 3.13 --from git+https://github.com/Query-farm/vgi-wikipedia@v0.5.0 vgi-wikipedia-worker
```

The tag resolves to `4f70512a09ff71f5d946a77c0adf81699c139e16`. Its declared
requirements install in an isolated worker process without a transitive `--with`
stack or changes to notebook-kernel libraries. No worker checkout or build is needed.

## Source provenance and licenses

Notebook 01 retains the original unchanged public Wikidata entity response captured
**2026-11-14 10:39:37 UTC** for Q60, Q84 and Q90. The [manifest](fixtures/manifest.json)
records the exact request, entity revisions, raw/compressed hashes and timestamp.
The strict server verifies both hashes and accepts only the recorded request; all
five actual worker reads parse the same source bytes. Its seventy population
statements are not harmonized city geographies or a current population estimate.

Notebooks 02 and 03 use seven additional original responses described in
[the discovery recording guide](discovery_recordings/README.md) and
[manifest](discovery_recordings/manifest.json): article search; two summaries; two
page-to-item mappings; entity search; and a read-only SPARQL POST. The complete graph
query is visible in SQL and preserved in [discovery_query.rq](discovery_query.rq).
The server checks method, path, parameters and hashes, performs no external requests
and does no filtering, parsing or graph evaluation. The released worker does the
request construction and SQL conversion.

Wikidata structured data is [CC0](https://www.wikidata.org/wiki/Wikidata:Licensing).
Wikipedia snippet/summary text is by Wikipedia contributors under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/); canonical URLs and
summary revision links supply attribution in the recording guide. These licenses
are separate from the worker's MIT code license. No thumbnails, linked external
sources or full article bodies are downloaded. No endorsement is implied.

## Optional live retrieval

Each notebook accepts the same explicit source switch, for example:

```sh
uv run --locked vgi-notebooks 03_labels_are_not_item_keys.ipynb \
  --parameters '{"DATA_MODE":"live"}' --output-dir artifacts/live-labels --timeout 600
```

All three full live runs passed during this revision. They reset inherited API
endpoint overrides and call the official public HTTPS services, with no credentials
and no fallback to recordings. Structured requests disable the response cache and
use the worker's bounded request/result budgets; the article client uses its
published bounded retries/timeouts. A new source error remains an error.

The calls are independent API observations, not an atomic snapshot. Notebook 01
requires entity revisions to agree across relations. Notebook 02 checks title,
page ID, URL and complete summary equality before applying its budget. A future
search/content change may fail a slice-specific assertion; do not relabel that
failure as a successful replay or silently change the inputs.

Before any ATTACH, shared setup creates a fresh empty temporary SQL-secret directory
with persistent secrets disabled. Empty inventories are checked before use and at
cleanup. No SQL secret is created. Cleanup closes only this connection/server,
removes its temporary secret directory and restores the environment keys changed
by the source helper. Saved user SQL secrets are not loaded or altered.

## What the comparisons mean

Notebook 01 aggregates qualifiers and reference snaks at the complete statement key
before joining. Reference groups are distinct from snaks and do not score source
quality. Original time strings and precision remain visible; year extraction does
not invent a missing month/day. Preferred rank is not a latest-date function.
The default selects **20** dated statements; the 2010 cutoff selects **17**. The
fixed full-source audit remains **70 statements / 171 expanded rows / 70 aggregated
rows**, and its figure stays identical. Separate population axes support within-item
inspection; cross-city rankings would require harmonized scopes and methods.

Notebook 02 keeps six search candidates visible, then explicitly chooses the planet
and element. Search word count is article length, not relevance or quality. The
summary readers agree on **754** and **398** Unicode characters. A per-article
budget of 400 retains **798** characters total; 800 retains **1152**. Page IDs,
Q308/Q925, canonical URLs and full source lengths stay fixed. A character budget is
not a token/byte budget or a promise of complete sentences or sufficient evidence.

Notebook 03 selects the same two IDs from six Wikidata search candidates. Its graph
slice has four item-language rows. English or French labels lower to one display
string, while **two item IRIs** remain distinct. The element's `80` lexical value
retains its decimal datatype; the planet's optional atomic-number binding stays
SQL NULL. Changing display language changes labels and figures only, not raw terms,
identity, numeric binding or missingness. No NULL is replaced with zero.

## Coverage and validation

[coverage.json](coverage.json) maps visible SQL, returned results, interpretation and
successful raw-notebook execution receipts to **10/10 distinct public names** in
both catalogs. `wiki_page` overloads count once; both `main` and `wikidata` schemas
are included. Actual complete functions/tables/views exports reconcile the denominator.
The independently attached current source is
`9558938291f3920382345233ae504f89bf7ca02e`; its changes since the release are composed
examples/metadata, with no runtime implementation changes. Analytical receipts
execute v0.5.0, so exact released/current-runtime coverage is **10/0**; exact-current
analytical execution is not claimed.

All three recorded defaults, detached parameter experiments, preloaded-kernel runs
and separate live runs passed. The books contain **12**, **8** and **9** visible SQL
invariants, respectively, plus revision/identity checks. Recorded request audits
verify **5**, **7** and **2** accepted requests and no unexpected replay request.
Ruff, presentation/structure, coverage, parameter/default-preservation guards and
all changed figures were checked. Worker source reuses its
[green source CI](https://github.com/Query-farm/vgi-wikipedia/actions/runs/43587174831);
no worker rebuild, full source-suite rerun or container publication was needed.

Background: [MediaWiki search API](https://www.mediawiki.org/wiki/API:Search) and
[Wikidata SPARQL examples](https://www.wikidata.org/wiki/Wikidata:SPARQL_query_service/queries/examples).
Changing a release or recording pin requires renewed source, catalog, execution,
request-audit and visual verification.
