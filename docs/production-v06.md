# Run the integrated PubMed–FDA pipeline (0.6.0)

The supplied FDA ontology is preserved byte for byte at `integration/fda/ontology.ttl`:
SHA-256 `f9f455c15bd100e2c6f17fb197c01f6e6a49685e1e10daa4a36f3b6ada198ac2`.
It has 753 triples, 53 classes and 264 properties. The project adds its own PubMed
extension in the root `ontology.ttl`; it does not rewrite the FDA ontology.
The ontology is schema, not the complete FDA instance dataset. Configure the actual
FDA release files before running the production profile. No FDA scrapers are rebuilt.

## Install and test on a Linux VM

Use Python 3.10+ on a local filesystem with working POSIX file locks. The package
includes native Oxigraph wheels; use a supported Python/platform combination.
From the extracted project directory:

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -r requirements-tested.txt
pip install --no-deps -e .
pytest -q
pmkg integrated-demo --output demo-results
```

The demo is offline, credential-free and small. Every FDA instance, PMID and review
in this demo is synthetic. It tests baseline ingestion, revisions, deletions,
annotation replacement, FDA candidate review, SHACL, local RDF loading and all 12
queries in `queries/fda/`. The demo is not a PubMed coverage measurement.

## Configure real inputs

```sh
cp pipeline.example.json pipeline.json
# Edit pipeline.json before continuing.
export NCBI_EMAIL='operator@example.org'
# Set NCBI_API_KEY privately if available; never commit the key.
pmkg pipeline-init --config pipeline.json
pmkg pipeline-update --config pipeline.json --max-files 1
pmkg pipeline-status --config pipeline.json
pmkg pipeline-verify --config pipeline.json
pmkg pipeline-query --config pipeline.json queries/fda/09_pending_review.rq
```

Set `fda_sources` to the actual FDA instance partitions, e.g. `/srv/fda-release/*.ttl.gz`.
Set `fda_format` to ttl, nt, nq or trig, or omit it for extension detection.
Use absolute IRIs or explicit RDF base declarations. FDA sources may be gzip.
Keep `fda_ontology` pointing to the supplied canonical ontology. Initialization streams
all configured FDA partitions into a disk-backed index, validates the schema, keeps
identifiers/device-record/category/recall/name relationships, and excludes narrative
FDA text. It rejects an ontology-only dataset. It does not claim to preserve every
FDA predicate; the original FDA files remain the authoritative full dataset.
The finished index is immutable. A changed FDA release requires a new `fda_index`
path, then `pipeline-init` and `pipeline-update` (which rematches every due source before
downloading the next one). Alternatively repeat bounded `pipeline-link` runs and
finish with `materialize` and `pipeline-verify`. A changed catalog
invalidates old review approvals instead of silently reusing them.

Set `state`, `rdf_store`, `fda_index` and `plan` on the VM's persistent data volume.
Retain a fresh v0.6 state; do not reuse older extraction-contract states.
`pipeline.pilot.json` uses only the bundled 28-triple real FDA extract and explicitly
labels it partial. It cannot establish comprehensive device coverage.

## Storage, complete backfill and scheduling

Run the one-file pilot first and measure storage/runtime. The observed PubMed
inventory had 1,691 files (1,334 baseline, 357 updates), about 70.09 GiB compressed.
Those are dated inventory observations, not a permanent corpus size. JSON field
provenance, RDF partitions, the FDA index, private downloads and the local query
store need substantially more space. Peak loading also needs staging space.
`reserve_gib` is a stop threshold between sources, not an estimate or disk quota;
a source can exceed it while processing. Monitor disk usage. The local full-run
attempt was blocked by insufficient storage, and no full-corpus runtime or final
storage size is claimed.

After the pilot, process the full discovered reconstruction:

```sh
pmkg pipeline-update --config pipeline.json --all
pmkg pipeline-verify --config pipeline.json
```

Discovery freezes an ordered baseline-plus-update plan. All required annual baseline
files must be processed before its corresponding update sequence, including deletion
notices. A selected file or partial baseline is incomplete. Subsequent discovery may
append updates; incompatible annual rollover fails rather than mixing inventories.
Keep an archived annual inventory and its sources for reproducible reconstructions.
`--frozen-plan` uses the saved inventory without refreshing it. File checksums,
statuses, record counts, completion times and retryable failures are in the ledger.
A failed source is retried, never skipped to advance the checkpoint.

Use the installed virtualenv executable and absolute paths in cron. For example:

```cron
15 2 * * * cd /srv/pubmed-kg && /srv/pubmed-kg/.venv/bin/pmkg pipeline-update --config /srv/pubmed-kg/pipeline.json --max-files 10 >> /srv/pubmed-private/update.log 2>&1
```

Choose a file limit that can catch up with incoming files. A large initial backfill
may instead use `--all`. Scheduling is separate from ingestion. Logs contain counts
and errors, not abstracts or copied annotation passages. Supply API credentials through
a protected environment file or the scheduler's secret configuration.

One writer per corpus, FDA index and local RDF store: **do not run parallel pipeline
writers against the same paths**. POSIX locks reject overlap. Independent isolated
states can run concurrently, but coordinate the total NCBI request rate across the
same IP/key. Source ordering and revision commits are sequential. Parallel corpus
partition merging is not implemented; manual merges can invalidate deletions.

## Count → JSON → verify → Turtle → verify → load

Each source has an independent streaming XML metadata inventory; retained records
are compared against it before export. JSONL schema and identifier/date/MeSH mapping
checks precede RDF publication. Bounded RDF partitions are parsed, compared with JSON,
validated with SHACL and the ontology contract, and hashed. The ledger records the
successful artifacts before a completion checkpoint. A crash resumes without advancing
past unverified work. Duplicate and older events cannot resurrect newer/deleted records.
Run `pipeline-verify` for the explicit full-ledger/artifact audit; this may be expensive
on the complete corpus. Cheap incremental loading does not reparse all prior outputs.

Every PMID has separate metadata, annotation and device-link named graphs. A metadata
replacement or tombstone clears all three. The unified ordered change feed carries
replacement/deletion manifests and enrichment/link revisions. Turtle alone does not
apply deletions. `materialize` applies the feed to a local disk-backed Oxigraph store:
stream to staging graphs, verify counts, then atomically DROP/MOVE the affected graphs.
A pending marker blocks queries after an interrupted load until recovery completes.
The loader checkpoint advances after the RDF transaction; replay is idempotent if
it crashes between that transaction and the checkpoint. Never load historical TTLs
by append-only concatenation. No remote graph-store writer is included.

```sh
pmkg materialize --config pipeline.json
pmkg pipeline-query --config pipeline.json queries/fda/01_verified_510k.rq
pmkg pipeline-export --config pipeline.json --output /srv/pubmed-export/current.nq
```

Queries support SELECT and ASK, using a union view of the local named graphs. Query
and export commands require a current checkpoint, matching FDA catalog and completed
linking for all current articles. Export
writes and reparses a temporary N-Quads file before atomic publication; a complete
export requires additional disk and time. Back up the entire corpus, index, inventory
and query-store directories while writers are stopped/locked. Do not copy an active
RocksDB store piecemeal or open it read-only while another process writes it.

## Review links and enrich independently

Explicit NCT, 510(k), PMA and labeled product-code detections retain method/location
without copied text. A catalog match is a candidate, not a verified identity. Names
are matched transiently against indexed FDA terms, with ambiguous short/single-word
names excluded. Product codes identify categories, not individual devices. The adapter
separates regulatory submissions from MedicalDevice resources and distinguishes a
direct `hasRecallRecord` relationship from category/TPLC `hasRecall` relationships.

```sh
pmkg link-candidates --config pipeline.json --output candidates.json --limit 100
# Independently inspect evidence, then prepare decisions.json (JSON array).
pmkg link-review --config pipeline.json --decisions decisions.json
pmkg pipeline-enrich --config pipeline.json --limit 100
pmkg pipeline-enrich --config pipeline.json --limit 100 --refresh
```

Decision fields are exactly `candidate_id`, `status` (`verified` or `rejected`),
`reviewer`, `reviewed_at` (ISO timestamp with timezone), `evidence_method`.
Verification requires an exact 510(k)/PMA candidate and
`evidence_method: "independent-identifier-confirmation"`. A reviewer must actually
confirm the evidence; the string alone is not a scientific validation. Name-only or
category-only candidates cannot be promoted. Decisions are bound to the article
revision, content hash and FDA catalog. Synthetic demo decisions are examples, never
production approvals. A new source revision invalidates the old decisions.

PubTator3 annotations have separate complete/pending/unavailable/failed states.
`annotations_per_run` bounds automatic work (default 10; set 0 to defer it). Failed
requests never mean zero entities and can return a failing exit status after metadata
has safely committed. Increase explicit enrichment batch counts over repeated runs
if broad annotation coverage is needed; metadata completeness does not imply full
annotation coverage. Unprocessed articles are selected before retrying unavailable/failed results, which
prevents unavailable low PMIDs from blocking later articles. Refresh is explicit,
bounded and rotates existing annotations by oldest retrieval time. PubTator's coverage
is independent of PubMed metadata coverage.

The supplied FDA ontology has no trial-to-device predicate. An optional
`trial_device_file` supplies a separately sourced ClinicalTrials.gov/device crosswalk.
Use local `.../trial/NCT########` subjects, explicit `k:ClinicalTrial` type,
`k:device` pointing to an explicitly typed FDA MedicalDevice, and IRI-valued
`prov:wasDerivedFrom`. Labels and `rdfs:seeAlso` are allowed; arbitrary text/predicates
are rejected. Supplying a crosswalk asserts responsibility for its accuracy. The demo
crosswalk is synthetic. An NCT mention alone never proves the trial uses a device.

## Top-ups, privacy and coverage interpretation

Targeted EFetch top-ups remain supported in a **separate snapshot state** so they
cannot overwrite the baseline/update ordering:

```sh
pmkg --state /srv/pubmed-private/topups fetch 19894120 --order 1
pmkg --state /srv/pubmed-private/topups enrich --limit 1
pmkg --state /srv/pubmed-private/topups verify
```

Do not merge these snapshots by appending TTL to the integrated store. Full inventory
updates are the authoritative reconstruction path. API keys are optional environment
variables, never embedded in config or committed files.

Abstracts may be read transiently for identifiers/catalog matching. Raw downloads
remain in `state/raw-private`, separate from distributable graph outputs. Retain them
privately for reproducibility and catalog rematching; apply institutional retention
and publisher/NLM redistribution restrictions. Deleting them requires redownloading
matching source bytes for name rematching. Graphs/fixtures/logs exclude abstracts,
article bodies and copied annotation snippets. Full-text retrieval is disabled. A
PMCID is not proof of open access; future full-text support must check PMC OA subset
membership and each article's license. The expected ~10% full-text availability is a
planning assumption, not a measured output or a requirement for this graph.

`pipeline-status` reports exact observed file/record/retention counts and independent
link/enrichment progress. Full corpus completion means all files in the frozen plan
were verified, not 100% device-information recall. Device discovery recall requires
a representative independently labeled benchmark; no exact percentage is supportable
from a synthetic or selected sample. Mentions and entity co-occurrence do not prove
causation, recall relevance, efficacy or comprehensive research coverage.
