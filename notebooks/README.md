# Wikipedia worker: Wikidata statement notebook

[01 · Population statements need dates and statement keys](01_population_statements_and_join_grain.ipynb)
uses the worker's Wikidata functions to retrieve population statements, date
qualifiers, reference groups and preferred ranks. Visible SQL preserves statement
keys and measures row multiplication from one-to-many joins. Two charts show dated
claims and the difference between statement counts and expanded join counts.

## Run from this directory

Install [uv](https://docs.astral.sh/uv/) and Git, then:

```sh
uv sync --locked
uv run --locked python execute.py
```

Only this complete `notebooks/` directory is needed. The runner uses a temporary
kernel specification and this directory as cwd. It replaces saved outputs only
after success; no global Jupyter kernel is registered. For VS Code, select
`notebooks/.venv/bin/python`. Connection and analytical SQL are visible; support
and plotting code are folded.

Run the proposed experiment into a separate copy:

```sh
uv run --locked python execute.py --parameters '{"MIN_YEAR":2010}' --output-dir validation/since-2010
```

For a separate explicit live retrieval:

```sh
uv run --locked python execute.py --parameters '{"DATA_MODE":"live"}' --output-dir validation/live
```

Overrides require a different output directory; unknown parameters and invalid
values fail explicitly. Experiment copies include assets, fixtures and environment
files. Restart the kernel and run all after editing. Reset `MIN_YEAR = 2000` and
`DATA_MODE = 'recorded'` for the saved example. Validation artifacts are ignored.

## Portable worker and data provenance

The literal ATTACH uses `uvx` with immutable public
[Git revision a0f139f7f6d7ce7a1f1a01e3ad48ab45a4cb8811](https://github.com/Query-farm/vgi-wikipedia/tree/a0f139f7f6d7ce7a1f1a01e3ad48ab45a4cb8811)
and the `vgi-wikipedia-worker` entry point. Python 3.13, VGI Python 0.51.1,
VGI RPC 0.53.1, PyArrow 26.0.1 and RDFLib 7.6.0 are explicitly pinned. Release
v0.5.0 supplies Python archives, not native github-auto assets. No local build or
parent checkout is required; initial Git/uv downloads need network access.
The notebook has its own lockfile and Haybarn 2.0.0rc11. Changing pins requires
rerunning defaults, the parameter experiment and the supported live path.

`fixtures/wikidata-cities.json.gz` contains the **unmodified** JSON bytes returned
by the public Wikidata Action API, compressed reproducibly. The manifest records
request parameters, complete URL, capture timestamp, byte count, SHA-256 hashes
and entity revisions. The response contains [New York City (Q60)](https://www.wikidata.org/wiki/Q60),
[London (Q84)](https://www.wikidata.org/wiki/Q84) and
[Paris (Q90)](https://www.wikidata.org/wiki/Q90). Structured Wikidata data is
[CC0](https://www.wikidata.org/wiki/Wikidata:Licensing); no article text or image
content is reproduced. External documents referenced by Wikidata have their own
terms and are not downloaded here.

The recorded response was captured at 2026-11-14 10:39:37 UTC. `replay.py` serves it
on an ephemeral loopback port only for the exact recorded GET request. Unknown
paths/parameters and POSTs fail. It performs no outbound access. Both compressed
and raw hashes are checked before serving. The notebook verifies five accepted
requests and no rejected requests, with identical response hashes. SQL uses the
real published worker to parse each response; no result table is substituted.

Live mode calls the public API five times with cache disabled and bounded worker
response/time limits. It is not an atomic source snapshot. Entity revisions must
agree across all retrieved relations before the joins proceed; drift fails clearly.
The run-start timestamp is separate from each entity's modification timestamp.
Errors stop execution without falling back to the recording. Saved outputs are
recorded mode by default. No API credential is needed. An empty temporary SQL secret
directory with persistent secrets disabled keeps saved credentials out of this run.

## Interpretation and checks

The capture contains 70 population statements. Twenty meet the default display
criteria (years since 2000); moving the inclusive lower year to 2010 retains 17.
The full-source audit expands 70 statements to 171 rows when every qualifier is
joined to every reference snak; aggregating context first retains 70 rows. The
second chart is unchanged by the year parameter.

Population quantities remain exact decimal strings in the raw result and are
explicitly cast to DECIMAL for selection. The plot converts to millions. It retains
unconnected statement points, marks preferred versus normal ranks, and shows
supplied paired quantity bounds where available. These are reported bounds, not
confidence intervals; missing bounds are not zero uncertainty. Independent vertical
scales are disclosed and the panels share a year axis.

The original time string, precision and calendar model are retained. Year-only
values with zero month/day are never coerced to a fabricated daily date. All plotted
values are positioned by year even when their source has finer precision. Other
qualifiers remain in the raw relation and may affect scope or method. The three
items' administrative boundaries and statistical definitions are **not harmonized**;
the notebook makes no current-population, trend-rate or cross-city ranking claim.

A full join key is `(source_api, entity_revision, requested_id, statement_id)`.
Reference groups are counted by their statement-local reference index; their snaks
are counted separately. Neither count establishes source independence or quality.
`rank='best'` means preferred when present, otherwise normal for that property; it
is not a latest-date query. Twelve SQL relationship checks, revision agreement and
request/hash audits validate the mechanics without asserting a desired population
ranking.

The unchanged official Query.Farm Strata Sun and VGI branding is bundled in
`assets/`, with the linked wordmark at the top and bottom of the notebook.
