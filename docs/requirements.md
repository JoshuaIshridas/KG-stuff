# Current requirements audit — version 0.6.0

The current integrated implementation and commands are described in
[production-v06.md](production-v06.md). The historical v0.5 checklist below remains
as a record of earlier live checks; the following changes supersede its commands
and integration limitations:

- Exact user-supplied FDA ontology preserved and schema-checked; immutable streaming
  index reads real FDA release partitions with resumable file checkpoints.
- `pipeline-update` automates ordered metadata acquisition, count/JSON/RDF/SHACL
  verification, FDA candidate generation, bounded PubTator enrichment and local
  transactional graph replacement/deletion. `pipeline-verify` audits artifacts.
- Persistent device candidates and independent reviewer decisions are revision- and
  catalog-bound; stale approvals are removed after replacement/deletion. Name/category
  matches cannot become verified identity. Regulatory records and devices stay distinct.
- Disk-backed local RDF materialization follows all metadata/annotation/link changes,
  with crash recovery and a query checkpoint guard. Remote writes remain unsupported.
- Twelve additional FDA-ontology queries are tested in the integrated synthetic demo;
  the actual full PubMed/FDA joined graph has not been tested live.
- Optional operator-supplied trial-device crosswalk requires provenance and explicitly
  typed FDA devices. No trial predicate was invented in the FDA ontology.
- Top-ups are supported in a separate snapshot state. Complete FDA instances and a VM
  with adequate storage are still operator inputs. No full-corpus or semantic recall
  percentage has been established. Full text remains disabled.

## Historical audit — version 0.5


Audited 2026-10-21 (America/New_York; late checks have UTC date 2026-10-22).
This checklist describes implemented behavior and distinguishes local tests from
live checks. “About 10% full text” is a supplied planning assumption, not a measured
coverage claim, download quota, or prerequisite for building this metadata graph.

| Requirement | Implementation and evidence |
|---|---|
| Annual baseline + daily updates | `corpus-plan`, `corpus-run`, `corpus-update`: all observed files, official MD5, ordered dependencies, no relevance filter, revisions/deletions, persistent ledger, crash recovery and lock. Full-corpus performance/execution remains untested. |
| E-utilities top-ups with optional key | `fetch` batches explicit PMIDs; `NCBI_API_KEY` and `NCBI_EMAIL` are supplied only through request parameters. Default conservative rate remains 3 requests/sec. EFetch verified live earlier without credentials; API-key forwarding/privacy tested with a mock key, not a live credential. Use a separate top-up state to protect annual corpus ordering. |
| PMID, title, journal, dates, publication type, MeSH, grants | Structured parser plus path/value/attribute preservation; partial and distinct date meanings retained; IDs and MeSH qualifiers/major flags verified against source fields. |
| DOI, PMCID and NCT | Normalized, typed identifiers, original values and evidence location. NCT links to local trial plus a ClinicalTrials.gov study resolver using `rdfs:seeAlso`; the resolver does not assert that the trial's existence or device associations were verified. |
| Chemicals, diseases and genes from PubTator3 | `enrich` for snapshot states; **`corpus-enrich`** for corpus states. Separate, bounded, resumable stage. Metadata ingestion is independent. Provider IDs/namespaces/types/scope/retrieval time retained; provider passage/mention text discarded. Live check: one article, 26 annotations, independent RDF verification. Synthetic tests cover all three entity families and malformed/unavailable/failed responses. |
| Device names | **`link-source`** scans titles/abstracts transiently against configured FDA catalog name predicates. Retains normalized catalog terms and locations, never snippets. Short/ambiguous single-word names are excluded by default; explicit single-token allowlist is available. Every name-only result is a review candidate, never identity or `owl:sameAs`. This is device-catalog lookup, not a replacement biomedical entity recognizer. |
| FDA product codes, 510(k), PMA | Explicit title/abstract identifier detections, including book citations. Product codes require an explicit label; a bare word such as CAT is not enough. Configured exact external identifier matching also supports valid structured identifiers. Category-code matches are explicitly classified as category candidates. All require review before promotion to verified identity. |
| NCT to ClinicalTrials.gov | Exact structured or detected NCT identifier gives a study resolver link. Actual trial → device data/predicate was not established in the inspected MDKG; the multi-hop example remains synthetic until real registry/device relationships are supplied. |
| No abstract/body publication | XML/source caches remain private; distributable JSON/RDF/logs contain metadata, IDs, catalog terms, annotations and links. Tests inject private abstract/passage/mention sentinels and check they do not escape into JSON/Turtle/N-Quads. Titles are retained as explicitly requested bibliographic metadata. |
| Full text only PMC OA, with article licenses | **Full-text ingestion is disabled and no full-text downloader is implemented.** There is no enabling flag that bypasses a license check. A PMCID is never treated as OA permission. Any future implementation must verify OA-subset membership, inspect each article's license/reuse conditions, use an authorized PMC access method, and retain the text privately rather than export it. Full text is unnecessary for this graph. |
| FDA Turtle and ontology for later work | `integration/fda/fda-sample.ttl` is a bounded real live extract, not synthetic or a full FDA dump. `fda-ontology.ttl` is a registry-derived RDF schema snapshot, not a claimed copy of the original release ontology. Original registry schema/usage notes, attribution, observed mappings, query results and native endpoint timeout are included. |

## Commands

Install and run the baseline/update path as described in [full-corpus.md](full-corpus.md),
using a **fresh version-0.5 state** because extraction/mapping contracts changed.
Then run optional enrichment explicitly; it never starts automatically at corpus scale:

```sh
pmkg --state ../pubmed-private/corpus-v05 corpus-enrich --limit 10
pmkg --state ../pubmed-private/corpus-v05 corpus-enrich --limit 10 --refresh
pmkg --state ../pubmed-private/corpus-v05 corpus-verify

# Targeted current snapshots remain separate from the annual reconstruction.
export NCBI_EMAIL='operator@example.org'
# Set NCBI_API_KEY privately in the environment if available; do not commit it.
pmkg --state ../pubmed-private/topups fetch 19894120 --order 1
pmkg --state ../pubmed-private/topups enrich --limit 1
pmkg --state ../pubmed-private/topups verify

# Candidate review artifact from a selected source and an FDA catalog extract.
# The supplied sample is small; it is not a complete device-name dictionary.
pmkg link-source /absolute/path/pubmed-source.xml.gz \
  --config integration/fda/config.observed.json \
  --external integration/fda/fda-sample.ttl --output candidates.ttl
```

`link-source` writes atomically and reruns deterministically, with source/catalog/config
hashes in its audit sidecar. It processes the supplied source rather than the current
corpus ledger. Historical candidate artifacts may refer to superseded/deleted records:
review consumers must check PMID/source revision against the current corpus and retract
stale assertions. Candidate artifacts are not silently loaded as verified relationships.
A production reviewed-link registry/loader remains integration work.

## Annotation updates and deletions

Corpus metadata has one stable named graph per PMID; annotations have a separate
`…/article-annotations/{PMID}` graph. Metadata replacement/deletion clears **both**
graphs using `clear_graphs` in the source replacement manifest. An annotation refresh
clears only the annotation graph, preserving metadata. `corpus_changes` in SQLite
orders metadata and annotation commits together; a loader follows this unified feed,
not separate date-sorted folders. For metadata entries use `corpus_batches`; for
annotation entries use `corpus_annotation_batches`. Hold the shared lock and require
the referenced manifest checksum. In one graph-store transaction clear the listed
graphs, load the named-graph N-Quads, then commit the consumed feed checkpoint.

`pending` survives interruption and is retried. `failed` and `unavailable` are distinct
from a successful document with no annotations. A failed corpus annotation request
records a failed artifact and produces a nonzero CLI exit. Metadata can still be used.
`corpus-verify` replays the unified feed and checks every annotation artifact, article
revision, current annotation head, and enrichment ledger row. A regression applies
metadata + annotation refresh + article revision/deletion and proves stale entities
are removed while current metadata remains.

## Test and live status

- **88 tests passed**; see `reports/requirements-v05-tests.txt`.
- Live PubTator corpus enrichment: `reports/requirements-v05-pubtator-live.json`.
- FDA-only category/recall join: 3 live rows through OKN federation; see
  `integration/fda/live-recall-join.json`. Shared category is not proof that a
  particular mentioned device was recalled.
- Native MDKG endpoint direct query: timed out; see `native-endpoint-test.json`.
- Full PubMed → deployed MDKG join: not performed; PubMed is not loaded there.
- No full PubMed backfill, massive enrichment job or full-text retrieval started.
- No exact overall medical-device recall percentage is established.

## Official documentation and rights

Accessed/rechecked 2026-10-21: [NLM PubMed downloads](https://pubmed.ncbi.nlm.nih.gov/download/),
[NCBI PubTator3](https://www.ncbi.nlm.nih.gov/research/pubtator3/api), and
[PMC Open Access subset](https://pmc.ncbi.nlm.nih.gov/tools/openftlist/).
PMC explicitly requires respecting each article's license, and does not authorize
reuse of every PMC article. See [sources.md](sources.md) for the earlier E-utilities,
DTD and concrete API response checks. The PubTator site itself serves an app shell;
implemented response fields were verified against actual BioC JSON, not inferred
from that shell.
