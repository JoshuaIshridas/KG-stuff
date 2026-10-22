import json
from pathlib import Path

import pytest
from rdflib import Dataset, Graph, Literal, RDF, RDFS, URIRef

from pubmed_kg.corpus import CorpusStore, verify_corpus
from pubmed_kg.enrichment import enrich_corpus
from pubmed_kg.graph import K, ROOT, iri
from pubmed_kg.linking import link_source, name_index
from pubmed_kg.network import annotations
from pubmed_kg.parser import records


def test_catalog_names_identifiers_provenance_and_text_exclusion(tmp_path):
    source = tmp_path / 'source.xml'
    source.write_text((ROOT / 'fixtures/baseline.xml').read_text().replace(
        '</ArticleTitle>', '</ArticleTitle><Abstract><AbstractText>PRIVATE_LINK_SENTINEL Synthetic Flow Pump, product code: XYZ. CAT is a word.</AbstractText></Abstract>', 1))
    catalog = tmp_path / 'catalog.ttl'
    catalog.write_text('''@prefix k: <https://example.org/pubmed-kg/v1/> .
        <urn:synthetic:pump> k:name "Synthetic Flow Pump" .
        <urn:synthetic:ambiguous> k:name "CAT" .
        <urn:synthetic:category> k:value "XYZ" .''')
    config = dict(name_predicates=[str(K.name)], identifier_predicates={'product-code': str(K.value)})
    output = tmp_path / 'links.ttl'
    report = link_source(source, catalog, config, output)
    g = Graph().parse(output)
    assert report['articles_scanned'] == 3 and report['verified_identity_links'] == 0
    assert (None, K.device, URIRef('urn:synthetic:pump')) in g
    assert (None, K.device, URIRef('urn:synthetic:category')) in g
    assert not (None, K.device, URIRef('urn:synthetic:ambiguous')) in g
    assert set(g.objects(None, K.status)) == {Literal('candidate')}
    assert (None, K.evidenceLocation, Literal('abstract')) in g
    assert list(g.triples((None, URIRef('http://www.w3.org/ns/prov#wasDerivedFrom'), None)))
    assert 'PRIVATE_LINK_SENTINEL' not in output.read_text()
    assert not list(g.triples((None, URIRef('http://www.w3.org/2002/07/owl#sameAs'), None)))


def test_name_dictionary_ignores_ambiguous_single_names():
    g = Graph()
    for name in ['CAT', 'longwordonly', 'Synthetic Device']:
        g.add((URIRef('urn:' + name.replace(' ', '-')), K.name, Literal(name)))
    index = name_index(g, {'name_predicates': [str(K.name)]})
    assert [term for term, _, _ in index] == ['synthetic device']


def test_book_product_code_and_trial_registry_link(tmp_path):
    source = tmp_path / 'book.xml'
    source.write_text('''<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID>900000050</PMID>
        <Book><BookTitle>Synthetic book</BookTitle></Book><ArticleTitle>FDA product code: XYZ; CAT; NCT00000001</ArticleTitle>
        </BookDocument></PubmedBookArticle></PubmedArticleSet>''')
    record = list(records(source))[0][1]
    assert [d['value'] for d in record['detections'] if d['scheme'] == 'product-code'] == ['XYZ']
    from pubmed_kg.graph import build
    g = build([('900000050', json.dumps(record), source.name, 1)])
    assert (iri('trial', 'NCT00000001'), RDFS.seeAlso, URIRef('https://clinicaltrials.gov/study/NCT00000001')) in g


def test_corpus_pubtator_resume_refresh_and_article_replacement(tmp_path):
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(ROOT / 'fixtures/baseline.xml', 1)
    first = enrich_corpus(store, None, limit=1, fixture=ROOT / 'fixtures/pubtator.json')
    assert first['statuses']['complete'] == 1
    assert verify_corpus(store)['enrichment']['annotation_batches'] == 1
    enrich_corpus(store, None, limit=1, refresh=True, fixture=ROOT / 'fixtures/pubtator.json')
    assert verify_corpus(store)['enrichment']['annotation_batches'] == 2
    store.ingest_delta(ROOT / 'fixtures/update.xml', 2)
    assert store.db.execute('SELECT COUNT(*) FROM enrichment').fetchone()[0] == 0
    assert store.db.execute('SELECT COUNT(*) FROM corpus_annotation_heads').fetchone()[0] == 0
    assert verify_corpus(store)['enrichment']['status'] == 'verified'
    # Follow the unified changefeed and apply graph clearing in transaction order.
    dataset = Dataset(default_union=True)
    for kind, reference in store.db.execute('SELECT kind,reference FROM corpus_changes ORDER BY seq'):
        if kind == 'metadata':
            folder = store.root / store.db.execute('SELECT folder FROM corpus_batches WHERE ord=?', (reference,)).fetchone()[0]
            for line in (folder / 'replace.jsonl').read_text().splitlines():
                for graph in json.loads(line)['clear_graphs']:
                    dataset.graph(graph).remove((None, None, None))
            files = folder.glob('*.nq')
        else:
            folder = store.root / store.db.execute('SELECT folder FROM corpus_annotation_batches WHERE id=?', (reference,)).fetchone()[0]
            manifest = json.loads((folder / 'manifest.json').read_text())
            for graph in manifest['clear_graphs']:
                dataset.graph(graph).remove((None, None, None))
            files = [folder / 'annotations.nq']
        for path in files:
            dataset.parse(path, format='nquads')
    assert not list(dataset.subjects(RDF.type, K.Annotation))
    assert len(list(dataset.subjects(RDF.type, K.Article))) == 2


def test_enrichment_failure_is_retryable_not_zero_entities(tmp_path):
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(ROOT / 'fixtures/baseline.xml', 1)
    class BrokenClient:
        def get(self, *args, **kwargs): raise RuntimeError('provider unavailable')
    report = enrich_corpus(store, BrokenClient(), limit=1)
    assert report['statuses']['failed'] == 1
    assert store.db.execute('SELECT status FROM enrichment').fetchone() == ('failed',)
    assert verify_corpus(store)['enrichment']['status'] == 'verified'
    # Fair scheduling visits the two unprocessed articles before retrying failure.
    for _ in range(3):
        enrich_corpus(store, None, limit=1, fixture=ROOT / 'fixtures/pubtator.json')
    assert store.db.execute("SELECT status FROM enrichment WHERE pmid='900000001'").fetchone() == ('complete',)


def test_pending_enrichment_after_disk_failure_resumes(tmp_path, monkeypatch):
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(ROOT / 'fixtures/baseline.xml', 1)
    from pubmed_kg import enrichment
    real = enrichment.verify_annotation
    monkeypatch.setattr(enrichment, 'verify_annotation', lambda *args: (_ for _ in ()).throw(OSError('disk failure')))
    with pytest.raises(OSError):
        enrich_corpus(store, None, limit=1, fixture=ROOT / 'fixtures/pubtator.json')
    assert store.db.execute('SELECT status FROM enrichment').fetchone() == ('pending',)
    assert store.db.execute('SELECT COUNT(*) FROM corpus_annotation_batches').fetchone()[0] == 0
    monkeypatch.setattr(enrichment, 'verify_annotation', real)
    enrich_corpus(store, None, limit=1, fixture=ROOT / 'fixtures/pubtator.json')
    assert verify_corpus(store)['enrichment']['pending'] == 0


def test_pubtator_only_entities_and_no_copied_text(tmp_path):
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(ROOT / 'fixtures/baseline.xml', 1)
    fixture = tmp_path / 'pubtator.json'
    data = {'PubTator3': [{'pmid': '900000001', 'passages': [{'text': 'PRIVATE_ANNOTATION_TEXT', 'infons': {'type': 'abstract'}, 'annotations': [
        {'text': 'PRIVATE_ENTITY_SNIPPET', 'infons': {'type': 'Chemical', 'identifier': 'MESH:D000001'}},
        {'infons': {'type': 'Disease', 'identifier': 'MESH:D000002'}},
        {'infons': {'type': 'Gene', 'identifier': 'NCBIGene:1'}}]}]}]}
    fixture.write_text(json.dumps(data))
    enrich_corpus(store, None, limit=1, fixture=fixture)
    assert verify_corpus(store)['enrichment']['status'] == 'verified'
    assert len(json.loads(store.db.execute('SELECT payload FROM enrichment').fetchone()[0])) == 3
    for p in store.root.rglob('*'):
        if p.suffix in ('.json', '.ttl', '.nq', '.jsonl'):
            assert 'PRIVATE_ANNOTATION_TEXT' not in p.read_text()
            assert 'PRIVATE_ENTITY_SNIPPET' not in p.read_text()
    with pytest.raises(ValueError):
        annotations({'PubTator3': [{'pmid': '1', 'passages': 'invalid'}]}, '1')


def test_api_key_topup_is_supplied_only_to_request(tmp_path, monkeypatch, capsys):
    from lxml import etree
    from pubmed_kg import cli
    from pubmed_kg.store import Store
    first = etree.parse(str(ROOT / 'fixtures/baseline.xml')).getroot()[0]
    content = b'<PubmedArticleSet>' + etree.tostring(first) + b'</PubmedArticleSet>'
    requested = []
    class Response:
        def __enter__(self): return self
        def __exit__(self, *args): pass
        def iter_content(self, size): yield content
    class Client:
        def __init__(self, **kwargs): pass
        def get(self, url, **kwargs):
            requested.append(kwargs['params'])
            return Response()
    store = Store(tmp_path / 'state')
    monkeypatch.setattr(cli, 'Client', Client)
    monkeypatch.setattr(cli, 'Store', lambda root: store)
    monkeypatch.setenv('NCBI_API_KEY', 'TEST_SECRET_API_KEY')
    monkeypatch.setenv('NCBI_EMAIL', 'synthetic@example.org')
    monkeypatch.setattr('sys.argv', ['pmkg', 'fetch', '900000001', '--order', '1'])
    cli.main()
    assert requested[0]['api_key'] == 'TEST_SECRET_API_KEY'
    captured = capsys.readouterr()
    assert 'TEST_SECRET_API_KEY' not in captured.out + captured.err
    for path in store.root.rglob('*'):
        if path.suffix in ('.json', '.jsonl', '.ttl'):
            assert 'TEST_SECRET_API_KEY' not in path.read_text()


def test_real_fda_bundle_and_category_recall_query():
    fda = ROOT / 'integration/fda'
    g = Graph().parse(fda / 'fda-sample.ttl')
    ontology = Graph().parse(fda / 'fda-ontology.ttl')
    assert len(ontology) > 100
    result = list(g.query('''PREFIX md: <http://medicaldevice.com/ontology/>
        SELECT ?number ?recall WHERE {
          ?submission md:k510Number ?number; md:hasProductCode ?category .
          ?tplc md:hasProductCode ?category; md:hasRecall ?recall .
        }'''))
    assert any(str(number) == 'K912346' for number, _ in result)


def test_unavailable_annotations_do_not_starve_unprocessed_articles(tmp_path):
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(ROOT / 'fixtures/baseline.xml', 1)
    empty = tmp_path / 'empty.json'
    empty.write_text('{"PubTator3": []}')
    for _ in range(3):
        enrich_corpus(store, None, limit=1, fixture=empty)
    assert store.db.execute('SELECT COUNT(*) FROM enrichment').fetchone()[0] == 3
    assert store.db.execute("SELECT COUNT(*) FROM enrichment WHERE status='unavailable'").fetchone()[0] == 3
    # Refresh rotates by oldest retrieval rather than repeating the lowest PMID.
    store.db.execute("UPDATE enrichment SET retrieved=CASE pmid WHEN '900000001' THEN '2020-01-01T00:00:00Z' ELSE '2021-01-01T00:00:00Z' END")
    store.db.commit()
    enrich_corpus(store, None, limit=1, refresh=True, fixture=empty)
    enrich_corpus(store, None, limit=1, refresh=True, fixture=empty)
    refreshed = store.db.execute("SELECT COUNT(*) FROM enrichment WHERE retrieved > '2025'").fetchone()[0]
    assert refreshed == 2
