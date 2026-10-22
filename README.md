# PubMed → MDKG foundation

**Version 0.6.0 integrates your supplied FDA ontology, a streaming FDA index, revision-bound device-link review, and an incremental local RDF query store.** Follow the [VM operating guide](docs/production-v06.md) for the complete automated count → JSON → verify → Turtle → verify → load workflow. Configure the actual FDA instance files; an ontology alone is not the FDA dataset. Full PubMed execution and near-100% medical-device recall remain unmeasured.

Schema-v2 automated count → JSON → verify → Turtle → verify workflow. See [coverage and acceptance contract](docs/coverage.md). No claim of near-100% semantic device recall is made without a representative labeled benchmark.

Working Python metadata ingestion, SQLite ledger, deletion-aware replacement, RDF/SHACL, separately retryable PubTator enrichment, and 12 tested SPARQL examples. FDA scrapers are outside scope. The team's FDA versions/deployment details are supplied context, not independently verified.

## Run

From this project directory (Python 3.10+):

```sh
python3 -m venv .venv
. .venv/bin/activate
pip install -e '.[test]'
pytest -q
pmkg integrated-demo --output integrated-results
pmkg acceptance --output acceptance-results
pmkg --state state/demo demo
pmkg --state state/demo verify
pmkg --state state/demo validate
pmkg --state state/demo query queries/02_device_recalls.rq --adapter-fixture
```

All demonstration records, PMIDs, grants, trial/device/recall resources, and review assertions are **synthetic**. This is an incomplete baseline sample. The demo writes `state/demo/demo.ttl` containing metadata plus synthetic adapter relationships; ordinary snapshots contain metadata and annotations only. No credentials are needed.

## Requirements and FDA inputs

To test complete frozen-inventory coverage, use the [guarded full-coverage test](docs/full-coverage-test.md). The local attempt stopped at the storage guard: about 70 GiB compressed sources versus 4.65 GiB free, before RDF/database overhead. A full reconstruction needs an adequately provisioned VM.

See the [audited requirements checklist](docs/requirements.md). The [FDA bundle](integration/fda/README.md) includes a real, bounded Turtle extract, your exact supplied canonical ontology plus a historical registry-derived snapshot, observed identifier/name predicates and live query results. Full text is disabled; abstracts/bodies are never exported.

## Operations

For the integrated production workflow use [production-v06.md](docs/production-v06.md). The lower-level corpus commands remain available: **`corpus-plan`, `corpus-run`, `corpus-update`, `corpus-status`, `corpus-enrich`, and `corpus-verify`**, as documented in [full-corpus.md](docs/full-corpus.md). The commands below use the original complete-snapshot engine for small, separate states. Do not use it for a full historical backfill or mix the two state types.

```sh
# Discover only; never downloads baseline automatically
pmkg discover --kind baseline > baseline-inventory.json
pmkg discover --kind updatefiles > update-inventory.json
# Selected historical file, not a complete reconstruction
pmkg download https://ftp.ncbi.nlm.nih.gov/pubmed/baseline/SELECT_ACTUAL_FILENAME.xml.gz --order 1 --md5 ACTUAL_MD5
# Or selected locally downloaded file (XML or gzip)
pmkg ingest /absolute/path/selected.xml.gz --order 1
# Complete, reviewed baseline-plus-updates inventory required
pmkg update --manifest manifest.json
pmkg verify
# Explicit top-up: order must be newer than all relevant prior sources
export NCBI_EMAIL='operator@example.org'
# Optional NCBI_API_KEY comes from environment; never commit it
pmkg fetch 19894120 --order 1000000
pmkg enrich --limit 10
pmkg enrich --limit 10 --refresh
pmkg export
pmkg validate
pmkg query queries/11_identifiers.rq
# Schema inspection is read-only; configuration is empty by default
pmkg inspect-mdkg --config config.example.json
# Candidate matches against an operator-supplied RDF extract
pmkg match --config actual-config.json --external mdkg-extract.ttl --output candidates.ttl
```

Replace the example filename and checksum with values from the official inventory/sidecar. This CLI fetches explicit PMID lists in batches of 200, not arbitrary search result sets. It avoids PubMed ESearch's first-10,000 retrieval limitation; for exhaustive historical acquisition use bulk baseline/update files. EFetch `retstart`/`retmax` paginate an explicitly selected/history set, not a way to bypass the PubMed search cap. Network calls have timeouts, bounded retries/backoff, and a conservative 3 requests/sec ceiling even with a key. Coordinate rates across processes sharing an IP/key. No full-text retrieval is implemented.

See [runbook](docs/runbook.md), [mapping](docs/mapping.md), [official sources](docs/sources.md), [query guide](queries/README.md), and [competency questions](docs/competency.md).

## Limits

This is a tested foundation, not a validated full-scale PubMed mirror. Journal and book citations are supported. Unknown XML fields or malformed records fail a source atomically instead of silently losing records. There is no remote graph-store writer, full-text ingestion, scheduler daemon, or automatic assertion of verified device identity. The corpus engine emits only changed articles, with 50-article partitions by default and explicit graph replacement/deletion manifests. The original snapshot engine still republishes its current graph. Measure VM IO, runtime and storage before a full historical run. Version 0.5 discovered the MDKG through the connected OKN service and verified bounded FDA queries against its served predicates. The catalog/native endpoint query timed out, and the full PubMed cross-graph join was not run. See the FDA bundle for namespace differences and observed mappings.

A mention does not establish causation, recall relevance, efficacy, or comprehensive research coverage. Annotation absence, failure and unavailability are distinct. PubTator entity co-occurrence does not establish a biomedical relation.
