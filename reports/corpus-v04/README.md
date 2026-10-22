# Version 0.4 corpus-ingestion verification

Test date: **2026-10-21**. This version changes the acquisition/export mechanism;
it does not claim that a medical-device recognizer has attained 100% recall.

## Implemented

- Complete, frozen official baseline-plus-update source inventory and automatic
  same-vintage update discovery. The import is unfiltered: all citations survive
  regardless of device screening status.
- Required official MD5 agreement on the network import path, streaming XML and
  JSONL, independent counts/content verification, explicit replacements/deletions.
- Per-source deltas and bounded RDF partitions instead of rebuilding the entire
  current graph after every file. SQLite indexes support source-local selection.
- Stable per-PMID named graphs, verified Turtle/N-Quads exports, source manifests
  and a clear/load deletion protocol tested locally. No remote graph writes.
- Atomic ledger completion after output validation, failure recovery, immutable
  source identities, exclusive lock, dictionary/mapping contract checks, and
  independent disk-backed history replay.
- Machine-readable completeness reports with separate file, record-retention,
  checksum, and unmeasured device-recall fields. An incomplete inventory does not
  receive a record-coverage percentage.

## Evidence

| Check | Result |
|---|---|
| Unit/regression suite | **79 passed**, including 13 corpus-engine tests; see `tests.txt` |
| Offline acceptance | **12/12** known-answer SPARQL examples; 784 synthetic triples; see `acceptance.txt` |
| New per-article graph loading | Synthetic baseline + revision/deletion applied by graph clear/load; recall and trial/device multi-hop queries checked |
| Actual official inventory discovery | **1,691 files** observed: 1,334 baseline + 357 update files; no XML downloaded by discovery |
| Real cached source replay through new engine | **100 article records + 214 deletion events**; final 100 articles and 214 tombstones; all source events and eligible metadata reconciled; see `real-source-test.json` |
| Real-sample processing + independent verification | 63 seconds on this local machine; not a corpus-scale or VM benchmark |
| Source acquisition protocol | Official download documentation rechecked live; existing official baseline/update downloads and MD5 verification are documented in the version 0.3 readiness report |
| Remote MDKG SPARQL | Not executed: no actual endpoint/schema supplied |
| Entire baseline reconstruction | **Not executed**; no claim that all PubMed has already been ingested or tested |
| Semantic device recall | **Not measured**; no representative independently labeled benchmark supplied |

The real test inputs are private cached samples from the previously downloaded
official baseline/update files, not redistributable fixtures. The baseline input
contains 50 sampled articles and the update input contains 50 sampled articles
plus all 214 deletion notices from that chosen update file. The two sample inputs
have their own SHA-256 values; they do **not** match the official full-file MD5s.
Consequently the real-sample report deliberately shows no full-corpus coverage
claim or official whole-source-checksum certification for those samples.

The source XML already cached on this workstation was reused; no complete baseline
or large annotation job was started. Live PubTator behavior was tested earlier on
one article, not across this corpus test. Broad 315-record EFetch tests are previous
version evidence, not an additional 315-record test of the new delta engine.

## How to operate

Use the exact commands and scheduling example in [full-corpus.md](../../docs/full-corpus.md).
Start with one complete source on your VM, inspect the report and storage/runtime,
then explicitly choose `--all` for the remaining frozen inventory. Metadata
ingestion can proceed without PubTator or an MDKG endpoint. Corpus-wide annotation
changefeeds, a production graph-store loader, and full-corpus performance validation
remain outside the completed test evidence. Parallel ingestion into one state is
not supported.

`official-inventory-plan.json` records the actual read-only discovery and its access
time. Generate a fresh plan on the VM rather than assuming this archived observation
is the latest directory state. `corpus-status` is a ledger summary; use
`corpus-verify` for independent replay/export integrity checking.
