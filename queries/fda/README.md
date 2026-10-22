# Queries against the supplied FDA ontology

These 12 queries use the user's actual `http://medicaldevice.com/ontology/`
vocabulary. They supersede the original `queries/*.rq` adapter examples for the
version-0.6 integrated pipeline. `catalog.json` gives each question, result columns,
known synthetic row count and live status. `pmkg integrated-demo` executes every
query, asserts those counts, and writes full bindings to `query-results.json`.

Run against the local, disk-backed union-default dataset:

```sh
pmkg pipeline-query --config pipeline.json queries/fda/02_device_recalls.rq
```

Required data and interpretation:

| Query | Required inputs | Interpretation |
|---|---|---|
| 01 | PubMed link assertions, reviewed exact identifier, `md:K510Record` with `md:k510Number`; optional actual device/record edge | A reviewed match to a submission is distinct from a match to a medical-device resource. |
| 02 | Verified assertion to an actual device; `md:hasRecallRecord` | Uses a direct device/recall edge; does not imply that the publication discusses or explains the recall. |
| 03 | Explicit NCT in PubMed, supplied reviewed registry/device crosswalk and provenance | No trial predicate is invented in the FDA ontology. A local `k:ClinicalTrial` bridges registry metadata. |
| 04 | PubTator disease/chemical annotations and reviewed device links | Co-occurrence is not a biomedical mechanism, efficacy or causation claim. |
| 05 | MeSH, journal publication year, reviewed device links and FDA product categories | Uses journal date meaning explicitly. Year-only/textual dates are not fabricated into complete dates. |
| 06 | GrantID/Agency and reviewed FDA assertions | Agency strings are source metadata, not invented funder identifiers. |
| 07 | Reviewed exact PMA identifier and `md:PMARecord` | A PMA submission/supplement is not automatically a unique marketed device. |
| 08 | `md:ProductCode`, a TPLC record with `md:hasRecall`, device/category edges and reviewed links | Category-level recall association. “Few publications” is relative to loaded/reviewed data; not proof of an evidence gap. The example threshold is at most one. |
| 09 | Candidate assertions from an indexed FDA release | Names and category codes remain review candidates; no numerical confidence or `owl:sameAs`. |
| 10 | Citation identifiers and reviewed device links | Multiple devices may be legitimate comparison studies; the query flags review needs, not automatic errors. |
| 11 | Separate PubTator state and annotation graph | Pending/unavailable/failed are not evidence that an article contains no entities. |
| 12 | Approved identifier reviews with reviewer, timestamp and source order | Reviews are bound to article/catalog revisions and are removed on article replacement. |

Local fixture counts in filename order are **1, 1, 2, 2, 1, 1, 1, 2, 1, 2, 2, 2**.
All clinical records, FDA instance resources, identifiers and reviews in that
acceptance dataset are clearly **synthetic**, while the ontology is the user's
unmodified file. Query 02 demonstrates article → reviewed device → recall; query
03 demonstrates article → trial → device. The small real FDA extract under
`integration/fda` is separate from this synthetic acceptance data.

The complete queries have not been run against a live combined PubMed/MDKG
deployment: PubMed is not loaded there and the actual complete FDA instance
release has not been supplied here. Earlier bounded FDA-only live checks and the
native endpoint timeout are recorded in `integration/fda`. Declared ontology
properties do not establish that a particular deployed release populates them.
