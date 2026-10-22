# Official documentation and verification

Access date for all entries: **2026-10-21**. API/source HTTP outcomes are recorded in `live-verification.json`; no copyrighted response text is included.

| Topic | Official source | Outcome and implementation consequence |
|---|---|---|
| Baseline, updates, restrictions | https://pubmed.ncbi.nlm.nih.gov/download/ | Read via web. Annual complete baseline, then numerical updates; revisions/deletions replace records. |
| Actual bulk directories | https://ftp.ncbi.nlm.nih.gov/pubmed/baseline/ and https://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/ | Both HTTP 200; filename discovery implemented; no baseline file downloaded. |
| XML DTD | https://dtd.nlm.nih.gov/ncbi/pubmed/out/pubmed_250101.dtd | Downloaded HTTP 200 and inspected. Element mapping uses this PubMed XML structure. No claim to support future DTD changes. |
| DTD documentation | https://dtd.nlm.nih.gov/ncbi/pubmed/doc/out/250101/index.html | Reference for operators; direct documentation page not separately live tested. |
| E-utilities guidelines | https://www.ncbi.nlm.nih.gov/books/NBK25497/pdf/Bookshelf_NBK25497.pdf | Official text consulted via web: 3 requests/sec without key, default 10 with key; implementation stays at 3. Aggregate IP/key traffic matters. |
| E-utilities parameters | https://www.ncbi.nlm.nih.gov/books/NBK25499/ | Direct page presented browser challenge. Official indexed printable documentation https://www.ncbi.nlm.nih.gov/books/NBK25499/?report=printable consulted for pagination limit; no exhaustive ESearch integration implemented. |
| PubTator API | https://www.ncbi.nlm.nih.gov/research/pubtator3/api | HTTP 200 serves application shell, not a machine-readable API spec. |
| PubTator official publication | https://pmc.ncbi.nlm.nih.gov/articles/PMC11223843/ | Consulted official-hosted primary paper for entities, BioC access and bulk interface. Supported entity families include genes, variants, diseases, chemicals, species and cell lines. |
| PubTator bulk | https://ftp.ncbi.nlm.nih.gov/pub/lu/PubTator3/ | Documented by primary paper; bulk ingestion not implemented/tested. |
| PubTator concrete export | https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson?pmids=19894120 | HTTP 200 JSON. Observed root is `PubTator3` list; documents contain pmid/id, passages/annotations/infons. Verified parser handles this wrapper. Passage and annotation text discarded. No undocumented API rate entitlement claimed: 3/sec is a conservative client setting, not a verified PubTator limit. |
| Targeted EFetch | https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi?db=pubmed&id=19894120&retmode=xml | HTTP 200 XML for one PMID. No API key supplied. API-key path not live tested. |
| PMC OA subset and licensing | https://pmc.ncbi.nlm.nih.gov/tools/openftlist/ | Read current page (modified August 2026). Reuse varies per article. Current automated methods listed include cloud, OAI-PMH, E-utilities and BioC. No full-text implementation. |

The earlier version-0.3 audit did not execute live MDKG queries. Version 0.5 subsequently discovered the graph through the connected OKN service, retrieved its registry schema, and executed bounded read-only FDA predicate and category/recall queries. The direct native endpoint request timed out. See [the attributed FDA bundle](../integration/fda/README.md) and [requirements audit](requirements.md). FDA release/triple-count claims remain unverified project context.


## Local RDF engine verification (accessed 2026-10-22)

Official Oxigraph Python documentation: https://pyoxigraph.readthedocs.io/en/stable/io.html
and https://pyoxigraph.readthedocs.io/en/stable/store.html . Streaming RDF parse and
bulk insertion avoid loading complete FDA files in Python memory. SPARQL updates
are transactional; the loader additionally uses an exclusive process lock and
persistent pending/checkpoint markers. Tested version: pyoxigraph 0.5.12.
