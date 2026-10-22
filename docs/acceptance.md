> Historical schema-v2 acceptance report. For current version 0.3 and the 66-test/live-source results, use [live readiness report](../reports/readiness/README.md).

# Acceptance report — schema v2, 2026-10-21

- **59 tests passed**, including independent XML/JSON counts, same-count content corruption, identifier/date/MeSH normalization faults, schema drift, book citations, revisions/deletions, failed export recovery, RDF tampering, annotation corruption, ontology declarations and SHACL under RDFS inference. Exact output: `test-results-v2-final.txt`.
- Complete offline automated acceptance succeeded: 4 source article events, 1 deletion, 2 current articles, 12 SPARQL queries with expected results, 784 synthetic triples.
- Observed fixture record-event and eligible structured-metadata retention are both 100%. These denominators explicitly exclude article/abstract text, narrative notes and formatting structure; they do not measure biomedical fact recall.
- A tiny synthetic benchmark with perfect observed recall was correctly rejected as evidence of population recall.
- Directory inventory checks detect missing/unprocessed published files. No complete PubMed baseline was downloaded, so full-corpus coverage is **not established**.
- Fresh live EFetch → count/JSON verification → PubTator enrichment → Turtle verification succeeded for one PMID: 218 metadata nodes, 82 source attributes, 1966 serialized triples. Exact report: `live-v2-verification.json`. This is a smoke test, not a population benchmark. Actual MDKG endpoint/schema remain unavailable; no live MDKG queries or writes were performed.
- GitHub Actions and cron examples are supplied. Remote CI and a production schedule have not been installed or run here.

Run `pmkg acceptance --output acceptance-results` to reproduce the offline count → JSON → verify → Turtle → verify workflow. See `verification-example/` for machine-readable sample reports, metadata JSONL, Turtle and query rows. See `coverage.md` for the claims and limitations.
