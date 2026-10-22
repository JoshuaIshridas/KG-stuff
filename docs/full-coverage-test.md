# Running the full-coverage test

The complete test must reconstruct and independently verify **every file in a
frozen annual baseline-plus-update inventory**. Discovering filenames, counting
records, a small perfect sample, or passing unit tests is not a full-coverage result.
This tests retention of the supported metadata, not identification of every
medical-device fact or extraction of full text.

## Local preflight result

The live directory preflight observed **1,334 baseline + 357 update = 1,691 files**,
with approximately **70.09 GiB compressed** according to rounded directory sizes.
The workstation had **4.65 GiB free**. The full run stopped with exit code 2 and
`blocked_insufficient_storage`, without downloading a source file. See
`reports/full-coverage-run.json` and its frozen `.plan.json`.

70.09 GiB is not a full system requirement. It excludes JSON audit events, the
current SQLite database, all RDF change sets, independent verification scratch
space and backups. Full source-field RDF preservation is verbose. Budget from a
complete-file pilot on the target VM and monitor storage during the backfill;
there is no verified full-corpus sizing or runtime estimate.

## VM command

Install the source package as described in `full-corpus.md`. Choose a durable,
private state path on adequately provisioned storage. Generate a preflight first:

```sh
python scripts/full_coverage_test.py \
  --state /srv/pubmed-private/full-test \
  --report /srv/pubmed-private/full-test-report.json
```

After setting an explicit storage budget in GiB that includes all outputs and
scratch data, start the complete test:

```sh
# Set this to your measured/planned budget, not just compressed source size.
export PUBMED_TEST_REQUIRED_FREE_GIB=YOUR_MEASURED_BUDGET
python scripts/full_coverage_test.py \
  --state /srv/pubmed-private/full-test \
  --report /srv/pubmed-private/full-test-report.json \
  --required-free-gib "$PUBMED_TEST_REQUIRED_FREE_GIB" \
  --reserve-gib 10 --batch-size 50 --run
```

The placeholder must be replaced by a positive numeric value. The runner refuses
to start a full reconstruction without an operator storage budget. Its checks are
necessary guards, not a guarantee that a whole source's temporary output fits:
the source-level reserve is checked between files. Provision sufficient space and
use independent disk monitoring. A disk failure rolls back the current source;
uncommitted orphan outputs may need to be reclaimed after inspecting the ledger.

Run the same command to resume. The runner freezes the first observed inventory
beside its report and uses the same plan on restart. It refuses incompatible
inventory changes/annual rollover. New updates appearing during the test do not
move its denominator; apply them after completing the frozen test. Ingestion is
sequential under an exclusive lock. Do not start parallel writers on the same state.

## Acceptance gate

Each source must pass official MD5, independent XML counts, audited JSONL,
identifier/metadata reconciliation, serialized Turtle/N-Quads checks, SHACL and
ontology checks before its completion is committed. At the end, `corpus-verify`
independently replays the full audit history and checks all current records,
tombstones, named graph ownership and annotations.

The runner reports `full_coverage_verified: true` and
`frozen_inventory_record_retention_percent: 100.0` **only** when all files in the
plan are committed/checksummed and the independent verification passes. That
percentage describes the frozen file inventory and declared safe metadata scope;
`medical_device_recall_percent` remains unknown without an independently labeled
representative device benchmark. Exit codes: 0 = completed requested mode,
2 = capacity/budget guard or incomplete acceptance, 1 = ingestion/verification
failure. Preflight-only exit 0 does not certify coverage.

## Whole-file XML/JSON diagnostics

This workstation also runs a storage-bounded audit on two already cached complete
official files. It verifies every supplied event and supported metadata field
against an independently counted XML inventory, using temporary 100-event JSONL
batches rather than retaining large outputs:

```sh
python scripts/audit_full_sources.py \
  /private/path/pubmed26n0001.xml.gz /private/path/pubmed26n1691.xml.gz \
  --output whole-source-json-audit.json
```

`reports/whole-source-checksums.json` records freshly rechecked official MD5s.
Both whole-file XML/JSON audits passed: **41,339 article events, 214 deletion
events, 5,836,465 metadata nodes and 2,585,780 metadata attributes** reconciled
without loss. See `reports/whole-source-json-audit.json` and the combined
`reports/full-coverage-summary.json`. Four new runner tests passed, including
capacity refusal and synthetic end-to-end completion after independent replay.
The whole-file audit explicitly does **not** verify RDF or the missing 1,689 source
files and must not be reported as a complete PubMed test. Raw inputs remain private;
diagnostic reports contain checksums, counts and outcomes, not abstract/body text.
