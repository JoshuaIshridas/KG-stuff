# SPARQL queries and local validation

All 12 queries were executed with RDFLib on the synthetic fixture graph on 2026-10-21. Full rows are in `local-results.json`. All adapter predicates are local contracts, not verified MDKG vocabulary. No query was executed against a live MDKG endpoint. Counts do not establish research coverage.

## 01_verified_510k.rq

Which articles identify a verified 510(k)?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?article ?device`. Local result count: **1**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?device WHERE { ?article k:link ?l . ?l k:status "verified"; k:scheme "510k"; k:device ?device . }
LIMIT 100
```

Interpretation: Only reviewed identifier links count; detected mentions alone do not.

## 02_device_recalls.rq

Which linked devices have recalls?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?article ?device ?recall`. Local result count: **1**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?device ?recall WHERE { ?article k:link ?l . ?l k:status "verified"; k:device ?device . ?device k:recall ?recall . }
LIMIT 100
```

Interpretation: A device recall does not prove the article concerns that recall. DISTINCT removes duplicate paths.

## 03_trial_devices.rq

Which trials connect articles and devices?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?article ?trial ?device`. Local result count: **2**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?trial ?device WHERE { ?article k:trial ?trial . ?trial k:device ?device . }
LIMIT 100
```

Interpretation: An NCT accession links a publication to a trial; trial-device data is external.

## 04_entities_devices.rq

Which disease and chemical annotations occur in device publications?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?article ?device ?type ?namespace ?id`. Local result count: **2**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?device ?type ?namespace ?id WHERE { ?article k:link ?l; k:annotation ?a . ?l k:status "verified"; k:device ?device . ?a k:entityType ?type; k:namespace ?namespace; k:normalizedIdentifier ?id . FILTER(?type IN ("Disease","Chemical")) }
LIMIT 100
```

Interpretation: Co-occurrence is not causality; duplicates are collapsed, no entity frequency claim.

## 05_mesh_year_category.rq

Which MeSH topics appear by year and category?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?category ?year ?descriptor`. Local result count: **1**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?category ?year ?descriptor WHERE { ?article k:link ?l; k:mesh/k:descriptor ?descriptor; k:date ?date . ?l k:status "verified"; k:device/k:category ?category . ?date k:kind "journal"; k:Year ?year . }
LIMIT 100
```

Interpretation: Uses journal publication year only, avoiding history/revision year confusion. Textual dates without a Year are excluded.

## 06_funding.rq

Which grants support device publications?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?article ?agency ?grant`. Local result count: **1**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?agency ?grant WHERE { ?article k:link ?l; k:grant ?g . ?l k:status "verified" . ?g k:Agency ?agency; k:GrantID ?grant . }
LIMIT 100
```

Interpretation: Grant strings and agency labels may require later funder normalization.

## 07_pma.rq

Which articles have verified PMA links?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?article ?device`. Local result count: **1**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?device WHERE { ?article k:link ?l . ?l k:scheme "pma"; k:status "verified"; k:device ?device . }
LIMIT 100
```

Interpretation: A PMA mention alone is not a verified link.

## 08_coverage.rq

Which recalled categories have few linked publications?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?category (COUNT(DISTINCT ?article) AS ?publications)`. Local result count: **2**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?category (COUNT(DISTINCT ?article) AS ?publications) WHERE { ?device k:category ?category; k:recall ?recall . OPTIONAL { ?article k:link ?l . ?l k:status "verified"; k:device ?device . } } GROUP BY ?category HAVING(COUNT(DISTINCT ?article) < 3)
LIMIT 100
```

Interpretation: Threshold <3 is illustrative; sparse graph coverage does not imply no research.

## 09_review.rq

Which candidates await review?

Required data: PubMed metadata plus synthetic reviewed links and local device/trial/recall/category adapter data.

Expected columns: `?article ?device ?method`. Local result count: **1**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?device ?method WHERE { ?article k:link ?l . ?l k:status "candidate"; k:device ?device; k:method ?method . }
LIMIT 100
```

Interpretation: Candidates require review, particularly names and product categories.

## 10_quality.rq

Which articles lack DOI identifiers or have conflicting verified device links?

Required data: Local PubMed metadata/enrichment. Query 10 optionally detects conflicts if link assertions exist.

Expected columns: `?article ?issue`. Local result count: **2**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?issue WHERE { { ?article a k:Article . FILTER NOT EXISTS { ?article k:identifier ?i . ?i k:scheme "doi"; k:valid true } BIND("missing DOI" AS ?issue) } UNION { ?article k:link ?a, ?b . ?a k:status "verified"; k:device ?d1 . ?b k:status "verified"; k:device ?d2 . FILTER(?d1 != ?d2) BIND("multiple devices require review" AS ?issue) } }
LIMIT 100
```

Interpretation: Missing DOI is not necessarily an error; multiple verified devices can be legitimate, so inspect rather than automatically reject.

## 11_identifiers.rq

Which structured identifiers and transformations are present?

Required data: Local PubMed metadata/enrichment. Query 10 optionally detects conflicts if link assertions exist.

Expected columns: `?article ?scheme ?raw ?value ?valid`. Local result count: **2**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?scheme ?raw ?value ?valid WHERE { ?article k:identifier ?i . ?i k:scheme ?scheme; k:raw ?raw; k:value ?value; k:valid ?valid . }
LIMIT 100
```

Interpretation: Raw/normalized values are identifiers only, never abstract text.

## 12_annotations.rq

Which annotation states and entity identifiers were retrieved?

Required data: Local PubMed metadata/enrichment. Query 10 optionally detects conflicts if link assertions exist.

Expected columns: `?article ?status ?namespace ?id`. Local result count: **4**. Live status: NOT RUN against a deployed combined PubMed/MDKG graph. FDA-only version-0.5 live checks are recorded in integration/fda; these adapter queries retain their synthetic local contract.

```sparql
PREFIX k: <https://example.org/pubmed-kg/v1/>
SELECT DISTINCT ?article ?status ?namespace ?id WHERE { ?article k:enrichmentStatus ?status . OPTIONAL { ?article k:annotation ?a . ?a k:namespace ?namespace; k:normalizedIdentifier ?id . } }
LIMIT 100
```

Interpretation: Unavailable means no returned document; failed requests are not zero annotations.
