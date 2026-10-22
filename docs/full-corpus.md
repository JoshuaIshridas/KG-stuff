> **v0.6 integrated operation:** use [production-v06.md](production-v06.md) for FDA indexing, reviewed links and local graph loading. Commands below describe the lower-level/legacy engines; keep separate states. Replacement now clears metadata, annotation and device-link graphs.

# Full-corpus metadata ingestion (version 0.5)

The way to maximize device-publication coverage is to retain **every citation**,
including records with no device screening signal, no MeSH, no abstract, or no DOI.
No search query or relevance classification selects records for this import.
“Unassessed” remains queryable; it never excludes an article.

This targets complete retention of the supported, distributable metadata in a
frozen official file inventory. It does not establish 100% recognition of devices
or extraction of facts in abstracts/full text. Text redistribution remains disabled.
The large-corpus path has bounded partition memory and does not re-export previous
articles on each source. A complete annual baseline has **not** been executed or
performance-certified in this session. Start with one source on the target VM,
inspect results, elapsed time and disk use, then explicitly opt into a backfill.

## VM commands

Use a fresh state directory, local disk, Python 3.10+, and the supplied archive:

```sh
unzip pubmed-kg.zip
cd pubmed-kg
python3 -m venv .venv
. .venv/bin/activate
python -m pip install -r requirements-tested.txt
python -m pip install -e .
pytest -q
pmkg acceptance --output acceptance-results

# Capture the COMPLETE official baseline + update listings. No XML is downloaded.
pmkg corpus-plan --output corpus-plan.json

# First real source: all citations, not a sample or relevance-filtered subset.
pmkg --state ../pubmed-private/corpus-v05 corpus-run \
  --plan corpus-plan.json --max-files 1 --batch-size 50
pmkg --state ../pubmed-private/corpus-v05 corpus-status
pmkg --state ../pubmed-private/corpus-v05 corpus-verify

# After inspecting the pilot on your VM, explicitly process the remaining inventory.
# This can be a long, disk-intensive job; no corpus-scale sizing claim is made.
pmkg --state ../pubmed-private/corpus-v05 corpus-run \
  --plan corpus-plan.json --all --batch-size 50
pmkg --state ../pubmed-private/corpus-v05 corpus-verify

# Discover subsequent daily files and continue automatically in dependency order.
pmkg --state ../pubmed-private/corpus-v05 corpus-update \
  --plan corpus-plan.json --all --batch-size 50
```

`corpus-run` and `corpus-update` default to **one file**, unless `--max-files N`
or `--all` is supplied. The commands process sources sequentially. They do not
spawn parallel writers or silently start annotation jobs. Keep the state and raw
cache outside the deliverable/repository directory. Raw XML may include restricted
abstracts. No Linux VM/remote CI run has been performed here; the supplied Python
tests ran locally.

## Exactly what is checked

1. Independently discover official baseline and update directories, freeze all
   filenames/URLs and observation time, and hash the resulting plan.
2. Reject empty baselines, missing numerical dependencies, duplicate files,
   mixed annual vintages, changed baselines, and edited plan identities.
3. Verify each downloaded gzip against its official MD5 sidecar. Resume with a
   matching cached file; incomplete downloads restart at byte zero. Stop on failure.
4. Independently count XML record/deletion events and safe metadata nodes/attributes.
5. Stream every event to JSONL. Verify JSON schema, identifiers, ordered content
   fingerprints, semantic mappings, and counts against XML.
6. Apply the file's events in order to SQLite, retaining only the latest article
   version and retaining tombstones. Repeated events within a file are audited;
   their final effect controls the current record.
7. Write **only this source's final upserts** in bounded Turtle and N-Quads
   partitions. Write explicit replacement/deletion operations to `replace.jsonl`.
8. Reparse both serializations. Check exact per-article graph ownership, JSON/RDF
   values, counts, ontology declarations, and SHACL. Verify output checksums.
9. Commit article changes, current graph ownership, and the completed batch in
   **one SQLite transaction**, after validation. This is the authoritative atomic
   completion marker. A crash before commit leaves an unreferenced output folder;
   resume safely rewrites it. There is no pointer-before-database-commit window.

All snapshots in this mode are source deltas, **not** full copies of current state.
The older `ingest/update/export` commands remain available for small independent
states and demonstrations; they must not be mixed with corpus state directories.

## Coverage reports that do not overstate completeness

`corpus-status` reports completed files, planned files, official-checksum-verified
files, current article count, tombstones and the inventory observation time. A
50% file fraction is **not** a 50% record-coverage estimate. Record coverage remains
unknown until **all** planned sources have passed and all official checksums are
recorded. At that point the status is `complete_for_frozen_inventory` and record
retention for that frozen inventory is 1.0. This says nothing about publications
not in PubMed, changes not yet released in NLM update files, or semantic device
recall. It also depends on the declared metadata policy and observed source
listings; agreement between checks is not a proof of bug-free extraction.

`corpus-verify` is the independent, more expensive check: replay all source JSON
into a scratch disk-backed database, compare final records/deletions/graph heads,
check every committed export checksum, reconstruct expected article graphs, and
run RDF/ontology/SHACL validation again. Run it after backfill, on a maintenance
schedule, and after restoring backups. Each source already runs its own checks
before completion; a full history replay is not repeated after every source.

## Loading RDF and applying deletions

Each PMID owns a stable named graph:
`https://example.org/pubmed-kg/v1/article-graph/{PMID}`.
Article resource IRIs remain separate:
`https://example.org/pubmed-kg/v1/article/{PMID}` with an explicit `k:pmid`.
The ontology and semantic mappings are shared with the snapshot engine. Shared
journal/entity/source descriptions may occur in multiple article graphs so that
clearing one article's graph cannot erase a different article's assertions.

The unified committed changefeed is `corpus_changes`, ordered by `seq`. Metadata
entries reference `corpus_batches.ord`; annotation entries reference
`corpus_annotation_batches.id`. `folder` identifies each immutable delta directory and
`manifest_sha256` validates its manifest. Consumers must hold the same `run.lock`,
require `sources.status = 'exported'`, and read only these referenced batches.
Never discover completed work by globbing directories: an interrupted attempt
can leave an uncommitted orphan. Back up SQLite together with referenced audit
and delta files. SQLite commit and atomic file renames protect against process
crashes; they are not a filesystem-wide power-loss transaction or backup.

For each committed batch, a graph-store loader must **in one transaction**:

1. Check the manifest and all listed file SHA-256 checksums.
2. Clear every named graph in each `replace.jsonl` entry’s `clear_graphs` list,
   for both upserts and deletes. This includes stale annotation graphs.
3. Bulk load all `.nq` partitions, preserving their named graphs.
4. Commit the data and its consumed batch checkpoint together. Roll back on error.

This exact clear/load strategy is exercised against a local RDF Dataset by the
regression suite, including the article → verified device → recall query after
revision/deletion. A production remote graph-store loader is not configured or
implemented. No remote writes are made. Turtle partitions are valid, human-readable
views of each change set; **do not concatenate historical Turtle partitions into
one graph**, which would retain obsolete assertions. Use a store's union-default
graph setting, or explicit `GRAPH` patterns, for cross-article SPARQL. Do not mix
synthetic FDA adapter data into a production dataset.

## Automation and recovery

After the initial backfill, an operator may install this cron entry (adjust paths):

```cron
15 3 * * * cd /srv/pubmed-kg && /srv/pubmed-kg/.venv/bin/pmkg --state /srv/pubmed-private/corpus-v05 corpus-update --plan /srv/pubmed-kg/corpus-plan.json --all >> /srv/pubmed-private/corpus.log 2>&1
```

The command refreshes listings, permits only same-vintage update appends, then
resumes from the first unfinished file. It stops at a failure rather than skipping
to the next file. A changed baseline or annual rollover requires a fresh state.
An exclusive process lock rejects overlapping writers; separate VMs do not have a
supported shard merge protocol. Scheduling is not installed automatically.
Use exit codes and the JSON reports for monitoring; provider bodies and article
text are not printed. A failed retry does not mean an article is irrelevant.

The corpus engine also supports bounded, resumable PubTator enrichment through
`corpus-enrich --limit 10` and `--refresh`. Annotation updates have separate PMID
graphs and share a unified commit feed with metadata. Read [requirements.md](requirements.md)
for loading and stale-annotation clearing. Do not run snapshot `enrich` or `fetch`
against a corpus state: use `corpus-enrich` and a separate top-up state instead.
Production remote graph-store integration remains follow-up work.
Changes to extraction, ontology/mapping or the device dictionary require a fresh
corpus state, preventing a mixed-contract corpus from being certified.

## Official source verification

Rechecked **2026-10-21**: [NLM download documentation](https://pubmed.ncbi.nlm.nih.gov/download/)
describes the complete annual baseline, numerical daily update order, and replacement
of revised/deleted records. It links the official bulk directories and DTD. The old
`nlm.nih.gov/databases/download/pubmed_medline.html` page is retired. Other official
source checks remain documented in [sources.md](sources.md).
