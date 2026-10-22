# What “100% coverage” can and cannot mean

Version 0.4 adds [full-corpus ingestion](full-corpus.md): an unfiltered, pinned official inventory, checksum-verified downloads, verified per-source RDF deltas, independent disk-backed history replay, and an automated update command. It avoids the prior full-snapshot-per-source scaling problem. The older snapshot commands described below remain for separate small states. File completeness and record retention are measured independently from device screening recall.

A count match alone cannot establish scientific recall. This version makes three distinct claims testable and reports an unmeasured claim explicitly.

1. **Record-event retention:** every article/book/deletion event in each supplied XML source appears in verified JSONL, in the same sequence, including duplicate PMIDs and revisions. Independent XML inventory and JSON inventories compare counts and an ordered content fingerprint. Expected article counts and targeted PMID sets can also be supplied. Final article count is deliberately different from event count because later versions replace earlier records and deletions remove records.
2. **Eligible structured-metadata retention:** every supported source XML metadata node, its path, text value and attributes is retained in JSON and RDF. Formatting is normalized; excluded text is counted and identified by policy. The count denominator is safe structured metadata, not every sentence or fact in a paper. Important research fields additionally have direct semantic properties for ordinary SPARQL.
3. **Published-file inventory coverage:** with `verify_complete_inventory: true`, each scheduled update reconciles the manifest and completed ledger with independently discovered official baseline and update listings. Missing, unexpected or unprocessed files fail the job. This applies to the published files observed for that run, not real-time changes awaiting an NLM update file.
4. **Device-screening recall:** unmeasured until an independently labeled representative benchmark has been supplied. All articles are ingested regardless of screening result; `unassessed` does not mean irrelevant. A partial lexical vocabulary, incomplete MeSH indexing, missing abstracts, or device mentions only in unavailable full text can produce missed signals. PubTator entity annotations do not provide a complete medical-device identity resolver.

There is **no honest current claim of near-100% identification of all medical-device facts across PubMed**. The software can enforce zero silent loss in its declared metadata scope. It cannot infer or verify facts absent from supplied metadata. Abstract/body publication remains prohibited, and full text is disabled.

## Mandatory workflow

Every `ingest`, `download`, `fetch`, and scheduled `update` now runs:

```text
independent XML inventory/count
  → JSONL (one source event per line)
  → JSON schema/identity/count/value verification
  → ordered record replacement/deletion in a transaction
  → partitioned Turtle
  → reparse actual serialized Turtle
  → SHACL + ontology-term + JSON/RDF value and count verification
  → completion checkpoint
```

Artifacts for a source SHA-256 are under `state/audits/{sha256}/`:

- `01-source-count.json`: independently counted source types, metadata elements/attributes and excluded elements.
- `records.jsonl`: schema-v2 metadata and deletion events; contains no abstracts or article bodies.
- `02-json-verification.json`: stage reconciliation, declared exclusions, scope and unmeasured claims.
- `03-rdf-verification.json`: completed workflow and applied/ignored event counts.

Each immutable snapshot includes partition checksums, `verification.json`, current articles, source manifest and deletion manifest. `CURRENT` changes only after serialized outputs verify. Existing pointer/ledger crash-recovery rules still apply. Historical JSON events may legitimately contain old metadata for deleted/revised records; they are audit events, not the current article set.

`pmkg verify` independently replays source JSON events into a scratch SQLite ledger, compares final records and tombstones to the persistent ledger, checks every serialized partition and checksum, verifies ontology/SHACL and JSON-to-RDF values, checks article identities across partitions and reconciles the deletion manifest. It writes `state/verification-report.json` and exits nonzero on failure. `update` invokes this automatically, then performs complete inventory verification if configured.

Unknown record/metadata elements fail ingestion instead of being silently omitted. `schema-contract.json` defines the reviewed DTD vocabulary and text exclusions. The source inventory uses an independent flat XML traversal; extraction uses a recursive traversal. They share the declared policy, so a deliberately wrong policy is not detected by agreement alone. Review policy changes and maintain independent fixtures. This is a meaningful integrity check, not a proof that software has no bugs.

## Device screening and validation

Screening combines explicit regulatory mentions, indexed equipment topics and candidate device terms. Terms are normalized screening reasons only, not identity links. Version 0.3 bundles all 345 official 2026 E07 descriptors and their 3,683 term strings. It screens transient abstracts as well as titles/keywords. This still does not establish semantic device recall; the measured live test and full-run blockers are in [readiness report](../reports/readiness/README.md). To include every current E07 descendant, obtain the official descriptor XML from NLM and run:

```sh
pmkg --state state/production mesh-index /absolute/path/official-descriptor-release.xml
```

Do this before starting a fresh source build. The command reads local XML only; it does not automatically download MeSH. Dictionary changes require a fresh rebuild or explicit reclassification/reingestion; a changed file on disk does not automatically refresh already stored screening results. Equipment indexing is broader than regulated medical devices; no signal proves FDA identity.

NLM sources checked 2026-10-21:
- Equipment and Supplies and its E07 tree: https://meshb.nlm.nih.gov/record/ui?ui=D004864
- Official MeSH data and annual descriptors: https://www.nlm.nih.gov/databases/download/mesh.html

An independently reviewed benchmark is JSON with `population`, `labeling_method`, `representative` (operator attestation), and `records: [{"pmid": "...", "is_device": true/false}]`. Labels must be independent of this screener and include difficult negatives, newly indexed/unindexed articles, specialty devices, multiple years/languages and missing-abstract cases. Evaluate a held-out sample, not training examples. The software cannot certify that a human-created sample is representative.

```sh
pmkg --state state/production benchmark human-reviewed-labels.json --min-recall 0.99
```

The command reports TP/FN/FP/TN, precision, recall and a 95% Wilson lower bound. Acceptance requires both recall and its lower bound at least 0.99, plus declared population/labeling/representativeness. A small perfect set does not pass. Synthetic example labels deliberately fail with exit code 2. Failure does not discard articles; it blocks the near-100% claim.

## Automation

Use a fresh schema-v2 state directory; pre-v2 states cannot substantiate this workflow and must be rebuilt from source. The manifest example enables discovery and complete inventory verification. The existing `update` command is the scheduler entry point; cron configuration remains separate from ingestion:

```cron
15 3 * * * cd /srv/pubmed-kg && /srv/pubmed-kg/.venv/bin/pmkg --state /srv/pubmed-state-v2 update --manifest /srv/pubmed-kg/manifest.json >> /srv/pubmed-state-v2/operations.log 2>&1
```

No full baseline or cron job is automatically installed on the workstation. Source/record validation is automatic whenever the workflow runs. Alerts should use process exit codes and `verification-report.json`; there is no hidden success checkpoint on a failed source. Full-state replay and graph export are deliberately thorough and IO intensive; benchmark before nationwide/full-corpus deployment. Local ad-hoc SPARQL loads the chosen snapshot in memory; use a graph database for corpus-scale queries.

## Automated offline acceptance and CI

```sh
pmkg acceptance --output acceptance-results
```

This runs the complete workflow on synthetic source counts (4 article events plus 1 deletion, ending with 2 current articles), verifies ledger replay and actual Turtle, executes all 12 queries against expected results, and confirms that a tiny synthetic benchmark cannot certify population recall. It writes the source JSONL sample, Turtle, query results and machine-readable verification/coverage reports. `.github/workflows/verify.yml` runs the test suite and this command on pushes, pull requests and manual dispatch once the project is placed at a repository root. CI configuration is supplied but has not been executed on a remote runner in this session.

The JSON contract is published as `record.schema.json` (Draft 2020-12), validated per event using jsonschema. Separate semantic checks compare normalized identifiers, PMID/version, title, dates, MeSH/qualifiers, grants and extended indexing fields to the preserved source fields. Thus matching record counts cannot mask changed identifiers or values.

Mapping, ontology, shapes and extraction contracts are content-fingerprinted. A changed mapping creates a distinct immutable snapshot generation. A changed extraction contract invalidates previous source completion evidence and requires a fresh state rebuild. Repeated exports of unchanged inputs keep the same generation and manifest creation time. This prevents an ontology/code change from silently overwriting a previously published generation.

The final ontology regression also runs SHACL with RDFS inference enabled, including shared detection evidence and a correction target whose full article is absent. This guards against domain/range statements accidentally turning link assertions or unresolved references into fully ingested articles.

Current-graph reads also check for incomplete source states and ledger/snapshot disagreement. The pointer-before-commit crash window is covered by a regression test: reads are blocked until the failed source is safely resumed.
