# Version 0.6.0 release verification

The canonical supplied FDA ontology was preserved unchanged and parsed as data:
SHA-256 `f9f455c15bd100e2c6f17fb197c01f6e6a49685e1e10daa4a36f3b6ada198ac2`.

- `tests.txt`: complete Python regression suite. Tests cover malformed XML/IDs,
  partial dates, MeSH qualifiers, retries, ordering, duplicate ingestion, deletions,
  crash recovery, RDF/SHACL, ontology contracts, privacy, review invalidation,
  graph loading, annotation queue fairness, and refusing queries against
  an incompletely matched FDA catalog. The final suite passed 108 tests.
- `install.txt`: successful editable package installation as pubmed-mdkg 0.6.0.
- `integrated/summary.json`: credential-free end-to-end demo using the actual supplied
  ontology and synthetic instances. Three articles become two after a revision and
  deletion. Old reviews and annotations are removed. All 12 FDA-aligned queries pass.
- `integrated/query-results.json`: full local bindings, expected columns and known counts.
- `integrated/sample.ttl` and `integrated/sample.nq`: synthetic current graph exports.
- `live-smoke.json`: bounded live EFetch and PubTator3 check for real PMID 19894120,
  with source/JSON/RDF verification and local materialized article count. The FDA
  context is the previously verified 28-triple real extract; this article produced
  no FDA candidates. It is not represented as a real verified device-link example.

Earlier live FDA-only checks, endpoint/graph identifiers, timestamps, three bounded
category/recall join rows and the native endpoint timeout are retained under
`integration/fda/`. The 12 complete PubMed/FDA queries were not run live against a
combined deployment. A declared property in an ontology does not prove that a
particular deployed release contains its instances.

Earlier full-source XML→JSON audits in `../whole-source-json-audit.json` covered
41,339 article events and 214 deletion events across two complete cached official
files. Eligible metadata retention in those audited files was 100%; whole-file RDF
and the entire PubMed inventory were not verified by that audit. It is historical
v0.5.1 evidence, not a full v0.6 corpus run.

The complete PubMed reconstruction remains **not executed**, because the local
storage preflight failed (roughly 70.09 GiB compressed sources before databases and
outputs). No exact full-PubMed percentage or medical-device discovery recall can be
claimed. `pipeline-status` and `pipeline-verify` compute measured inventory/retention
counts on the operator's actual run; semantic recall needs independently labeled data.

Use `../../docs/production-v06.md` for VM setup. Production needs the actual complete
FDA instance release paths, persistent VM data paths/storage and optional sourced
trial/device crosswalk. Full text is disabled. API credentials are optional environment
variables. No external graph writes were performed.
