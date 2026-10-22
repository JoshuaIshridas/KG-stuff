# FDA integration inputs and verification

**Canonical schema (v0.6): `ontology.ttl` is the exact user-supplied ontology, unchanged (753 triples). See `ontology-provenance.json` for its SHA-256. It is schema, not a full FDA instance dump. The production adapter and `queries/fda/` use this ontology. `fda-ontology.ttl` below is historical reference only.**

These files supply actual FDA graph context for PubMed linking without rebuilding
FDA scrapers or claiming that a sample is the entire FDA dataset.

- **`fda-sample.ttl`**: 28 real triples selected by bounded read-only queries through
  the connected OKN service. Contains actual regulatory records, FDA product-code
  relationships, a TPLC record and recall. It is not synthetic and is not a full dump.
  Strings without datatype/language fields in the connector response are preserved
  as plain lexical literals; this is not a byte-identical original RDF serialization.
- **`fda-ontology.ttl`**: 1,143 RDF schema triples reconstructed from the registry's
  class/property URI, label and description tables. These are not a downloaded
  original release ontology, and do not invent OWL domains/ranges from prose.
- **`registry-schema.json`**: full schema tables and registry usage notes, including
  its warning that some schema predicates are not deployed. A declared property
  must not be treated as a populated relationship without a live check.
- **`config.observed.json`**: exact identifier/name predicates confirmed through
  federation. The remote `endpoint` is deliberately unset because the native query
  timed out; it works for local catalog matching without a remote endpoint.
- **`live-verification.json`**, **`connected-sample.json`**, **`category-sample.json`**:
  bounded actual query results and graph/predicate identifiers.
- **`live-recall-join.json`**, **`category-recalls.rq`**: FDA-only category/recall join
  tested live, 3 rows returned with LIMIT 3. Expected columns are submission,
  number, productCode, tplc and recall. The small local sample also demonstrates
  the path through `hasProductCode`; it is not expected to reproduce all live rows.
- **`pubmed-category-recalls.rq`**: actual-predicate cross-graph example. The FDA
  portion is live-verified; the complete PubMed join has not been executed remotely.
- **`native-endpoint-test.json`**: direct native endpoint attempt and timeout,
  with UTC timestamp; this must not be reported as successful endpoint validation.

The connected registry describes the graph at
`https://purl.org/okn/frink/kg/medical-device-kg`, with catalog/native endpoint
`https://apps.okn.us/medical-device-kg/sparql` and homepage
`https://bmedesign.org/knowledgegraph`. The catalog advertises the newer namespace
`https://bmedesign.org/medical-device-kg/ns/`, while the federation actually returned
`http://medicaldevice.com/ontology/`. The observed configuration targets the latter.
Do not replace one with the other blindly. No deployment version/triple count was
independently measured by this work. The published registry identifies the graph's
license as [CC BY 4.0](https://creativecommons.org/licenses/by/4.0/); attribution:
Medical Device Knowledge Graph project and OKN registry, accessed 2026-10-21 local
date (late checks have UTC date 2026-10-22). Retain this attribution with the extracts.

FDA-side keys have source-quality variation: the observed `k510Number` predicate
also returned DEN-prefixed values, and some `pmaNumber` values began with N. Preserve
those source values; never rewrite them into fabricated K/P identifiers. A matching
FDA product code identifies a category association, not device identity or direct
recall applicability. No trial → device predicate was established, so it remains
unset. Device-name assertions are review candidates, even with an exact catalog match.

Rebuild the derived Turtle offline with:

```sh
python scripts/build_fda_bundle.py
```

For your complete existing FDA release, supply its actual Turtle partitions and
canonical ontology (or a stable read-only endpoint), release/checksum manifest,
namespaces, and exact identifier/device/trial/recall predicates. This small bundle
provides verified examples and schema context; it does not replace that release.
