# Recorded public Wikipedia and Wikidata discovery responses

These seven gzip files preserve unmodified JSON response bytes retrieved from the
official public APIs. The manifest records exact method, URL, parameters, UTC capture
time and both raw/compressed SHA-256 hashes. Compression uses a zero mtime for
reproducibility. The SPARQL response was captured using the same POST query as the
released worker; its complete SELECT query is also in `../discovery_query.rq`.

The article search returns six Mercury-related articles. Summary and page-identity
responses concern **Mercury (planet)** and **Mercury (element)**. The separate
Wikidata search returns six candidate items; the SPARQL query deliberately selects
Q308 and Q925, retrieving their English/French labels and optional atomic number.
The record is a set of independent API observations, not an atomic source snapshot.

Wikipedia snippet and summary text is by Wikipedia contributors under
[CC BY-SA 4.0](https://creativecommons.org/licenses/by-sa/4.0/). The unmodified
recordings retain canonical article URLs and revision identifiers for attribution:

- [Mercury (planet), revision 1384573767](https://en.wikipedia.org/w/index.php?title=Mercury_(planet)&oldid=1384573767)
- [Mercury (element), revision 1382286341](https://en.wikipedia.org/w/index.php?title=Mercury_(element)&oldid=1382286341)
- Search snippets link their six source articles in returned rows; consult article
  histories for contributor attribution. Search snippets are a current index
  observation and have no revision identifier in this API response.

Structured Wikidata content is [CC0](https://www.wikidata.org/wiki/Wikidata:Licensing).
These data licenses are separate from the worker's MIT code license. No thumbnails
or linked external documents are fetched or republished. No endorsement is implied.

`DiscoveryReplay` verifies every gzip/raw hash and serves only the exact recorded
GET or POST request for the chosen story. It does not query external APIs, alter
responses, filter rows, compute graph results or perform analysis. Unknown requests
are rejected and audited. The actual released worker constructs requests and parses
all returned SQL values. A local POST replays a read-only SPARQL SELECT; it makes no
external mutation.
