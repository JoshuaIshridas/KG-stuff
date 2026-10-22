# Version 0.6 FDA integration mapping

The supplied `integration/fda/ontology.ttl` is canonical and unchanged. `k:Device`
is a subclass of `md:MedicalDevice`. A K510Record/PMARecord is a regulatory submission,
not a device. Link assertions use `k:matchedResource` and optional `k:fdaRecord`,
`k:device` or `k:categoryMatch`. The adapter follows `md:has510kRecord`, `md:hasPMARecord`
and other declared device-record relationships; generic `hasFDARecord` alone does
not establish a device. Exact identifier matching does not automatically approve links.
`k:DeviceLinkAssertion` adds article revision/hash, catalog identity, evidence location,
review status/reviewer/time/method and source provenance. Shapes enforce these fields.
No tentative link uses owl:sameAs or numerical confidence. Category/TPLC recalls and
direct device recalls have different query paths. Trial-device associations use the
PubMed extension and a separately validated provenance-bearing crosswalk.

Stable local article IRIs remain `https://example.org/pubmed-kg/v1/article/PMID`,
with explicit `k:pmid`. This is a reserved demonstration namespace used consistently
throughout the release; choose and migrate an institutional namespace deliberately
before publishing an institutional production KG. Changing it requires rebuilding
mapping contracts and updating queries/shapes; it is not a runtime string replacement.
One article has metadata, annotation and device-link graphs; replacement clears all
three via the change feed. See [production-v06.md](production-v06.md) and
[the FDA query catalog](../queries/fda/README.md).

## Earlier metadata mapping (still applicable)

# RDF mapping and IRI policy

The namespace `https://example.org/pubmed-kg/v1/` is an explicit project-local placeholder controlled by configuration/code, not an existing MDKG namespace. Adopt an organization-controlled persistent base before publishing production IRIs. All local identifiers are percent-encoded UTF-8, with a kind path and deterministic key. Article IRIs are `…/article/{PMID}` with explicit `k:pmid`; PMID alone establishes article identity. No `owl:sameAs` assertions are generated.

| Source/meaning | Graph representation |
|---|---|
| PubmedArticle | `k:Article`, title, PMID, sourceVersion and ordered revision source |
| Journal | `k:Journal`, preferred key NlmUniqueID; ISSN/ISSNLinking preserved. If no NLM identifier, article-scoped unidentified journal avoids false merging |
| ArticleId/ELocationID/DataBank | Article-owned identifier node, source scheme, raw value, normalized value, validity and location |
| PubDate / ArticleDate / DateRevised / DateCompleted / history | Article-owned date node with kind and meaning; exact source components preserved, precision `year/month/day/text`. No invented date literals. `MedlineDate` and seasons remain textual |
| PublicationType UI | Article-owned type node, UI and label; missing UI omitted |
| MeSH | Assignment node, descriptor URI when supplied, label, major boolean; each qualifier has its own UI/label/major flag |
| Grants | Article-owned grant with GrantID/Agency/Country when present; no invented funder identity |
| Explicit K/P/NCT mentions | Article-owned detection with identifier scheme/value, regex method, title/abstract location; no copied evidence text |
| ClinicalTrials.gov NCT | Article-to-local trial resource; requires exact NCT structure or explicit structured accession. External trial-device relationships require adapter data |
| PubTator | Annotation node with entity type, namespace, normalized/raw identifier, passage scope, provider and retrieval date; unmapped identifiers explicitly typed |
| Provenance | Article `prov:wasDerivedFrom` filename resource with SHA-256; `prov:wasGeneratedBy` export run, SQLite source ordering and generation manifest UTC time |
| Device link | Separate LinkAssertion with device resource, candidate/verified/rejected status, method, qualitative confidence and evidence metadata. Synthetic review fixtures demonstrate verification only |

`rdf:type`, RDFS labels, PROV activities and NLM MeSH IRIs are reused. Local properties deliberately avoid claiming broader bibliographic equivalence. See ontology.ttl and shapes.ttl. Node keys based on article and source order within arrays are stable for identical input; replacement removes obsolete nodes by whole snapshot replacement. The export activity is content-addressed from stored records, source checksums and enrichment retrieval metadata, making repeated exports of unchanged state byte-identical. Manifest creation timestamps may differ. Sorted N-Triples is valid Turtle; no random blank nodes occur.

Repeated identifiers are retained as separate source occurrences for auditing. DOI URL/prefix removal and case normalization preserve raw values. Unknown identifier schemes retain the supplied type without invented mappings. PubTator explicit `database` metadata is used when a prefix is absent; otherwise the namespace is `unmapped:{entityType}`. The pipeline does not remap genes to human IDs or infer species. A missing document yields `unavailable`; a returned document with zero annotations can be `complete`; transport/parser failure yields `failed`.

SHACL enforces required article PMID/title/source order, annotation provenance/namespace, and link method/status/device. This compact constraint set does not certify biomedical truth or validate every PubMed field. XML DTD validation is not run (external entity/network resolution is disabled); unsupported/malformed records abort rather than skip a source.


## Schema-v2 ontology and preservation layer

Important contextual resources now have OWL classes, typed object/datatype properties, domain/range statements where unambiguous, selected disjoint classes, labels and definitions. Identifier occurrences, identifier detections, MeSH/qualifier assignments, grants, publication-type assignments, keywords, chemical and supplementary indexing, correction links, reference identifiers and source fields remain distinguishable. SourceField nodes preserve a safe XML element's indexed path, value and attributes without pretending it is an independently inferred biomedical fact. They make unmapped metadata queryable and loss-auditable.

BookArticle is a subclass of Article. ClinicalTrial resources are explicitly typed. `k:qualifierUI` and `k:identifierValue` carry source identifier strings; `k:identifier` is exclusively the article-to-Identifier object property. `k:correction/k:relatedArticle` supports retraction and correction queries; the relation type stays a source string. Citation text, free-text notes and conflict statements are explicitly excluded by policy. Missing correction PMIDs remain missing, never fabricated.

DeviceRelevanceSignal resources record a screening method, normalized identifier/term, location and qualitative classification. Candidate topic signals do not create verified device links. SHACL enforces article identifiers/provenance, record field types, date meaning/precision, MeSH qualifier context, source-field cardinalities, prohibited text-node exclusions, relevance status and link review state. An ontology-term gate rejects emitted project predicates or classes absent from ontology.ttl. These constraints do not prove clinical truth or scientific completeness.


Annotation resources now link to distinct, typed BiomedicalEntity resources keyed by namespace, entity type and normalized identifier. Missing identifiers do not produce fabricated entity IRIs. Shared detection evidence can be referenced by both articles and LinkAssertions, so `k:detection` deliberately has no single Article domain. `k:relatedArticle` permits unresolved referenced publication IRIs without requiring a locally complete record. RDFS-inference SHACL regression tests cover these distinctions.

For unknown identifier schemes, `valid` indicates a nonempty accepted source value, not external registry validation. Known DOI/PMID/PMCID/NCT/510(k)/PMA schemes have lexical checks; registry existence and verified identity remain separate.
# Corpus graph ownership (version 0.4)

The corpus importer uses the same article classes, identifier mappings, contextual
assertions, ontology and SHACL shapes as the snapshot importer. It places each
article and its contextual triples in a distinct named graph with stable IRI
`https://example.org/pubmed-kg/v1/article-graph/{PMID}`. This graph IRI is distinct
from the article resource. Shared journal/source descriptions may be repeated
across article graphs. Replacement/deletion clears the entire article graph;
other articles retain their own descriptions. See [full-corpus.md](full-corpus.md)
for the tested clear/load protocol and automated completeness checks.

