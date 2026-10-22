import gzip
import hashlib
import json
from pathlib import Path

import pytest
from rdflib import Graph, RDF, URIRef

from pubmed_kg.audit import VerificationError
from pubmed_kg.corpus import CorpusStore, verify_corpus
from pubmed_kg.device_links import link_corpus, review_links
from pubmed_kg.fda import FdaIndex, MD, build_index, parse_rdf, schema_contract
from pubmed_kg.graph import ROOT, K
from pubmed_kg.integrated import approvals, run
from pubmed_kg.materialize import Materialized


def setup(tmp_path):
    path = tmp_path / 'fda.sqlite'
    build_index([ROOT / 'fixtures/fda-canonical-synthetic.ttl'], path)
    index, store = FdaIndex(path), CorpusStore(tmp_path / 'corpus')
    sources = {'baseline.xml': ROOT / 'fixtures/baseline.xml', 'update.xml': ROOT / 'fixtures/update.xml'}
    store.ingest_delta(sources['baseline.xml'], 1)
    return index, store, sources


def test_supplied_ontology_preserved_and_bindings_declared():
    contract = schema_contract(ROOT / 'integration/fda/ontology.ttl')
    assert contract['sha256'] == 'f9f455c15bd100e2c6f17fb197c01f6e6a49685e1e10daa4a36f3b6ada198ac2'
    assert contract['triples'] == 753
    assert contract['trial_predicate_declared'] is False


def test_streaming_index_gzip_rerun_projection_and_identifiers(tmp_path):
    source = tmp_path / 'fda.ttl.gz'
    raw = (ROOT / 'fixtures/fda-canonical-synthetic.ttl').read_text()
    raw += '\n<urn:synthetic:bad> a <http://medicaldevice.com/ontology/K510Record>; <http://medicaldevice.com/ontology/k510Number> "DEN000001"; <http://medicaldevice.com/ontology/narrative> "PRIVATE_FDA_NARRATIVE" .\n'
    source.write_bytes(gzip.compress(raw.encode()))
    path = tmp_path / 'index.sqlite'
    manifest = build_index([source], path)
    assert build_index([source], path)['identity'] == manifest['identity']
    index = FdaIndex(path)
    assert list(index.lookup('510k', 'K123456')) == ['https://example.org/synthetic-fda/k510']
    assert not list(index.lookup('510k', 'DEN000001'))
    assert not index.db.execute('SELECT 1 FROM triples WHERE p=?',(str(MD.narrative),)).fetchone()
    assert index.devices('https://example.org/synthetic-fda/k510') == ['https://example.org/synthetic-fda/device1']
    assert list(index.name_matches('Synthetic pump name only, used here.'))
    assert not list(index.name_matches('CAT ordinary word'))


def test_index_schema_and_source_failure_do_not_publish(tmp_path):
    bad = tmp_path / 'bad.ttl'
    bad.write_text('<relative> <relative> "bad" .')
    with pytest.raises(SyntaxError):
        build_index([bad], tmp_path / 'bad-index.sqlite')
    assert not (tmp_path / 'bad-index.sqlite').exists()
    ontology = tmp_path / 'wrong.ttl'
    ontology.write_text('@prefix md: <urn:wrong:> . md:MedicalDevice a <http://www.w3.org/2000/01/rdf-schema#Class> .')
    with pytest.raises(VerificationError, match='missing'):
        schema_contract(ontology)


def test_record_device_category_distinction_and_review(tmp_path):
    index, store, paths = setup(tmp_path)
    result = link_corpus(store, index, source_paths=paths)
    assert result['articles_linked'] == 3 and result['automatic_verified_links'] == 0
    candidates = [json.loads(row[0]) for row in store.db.execute('SELECT payload FROM link_candidates')]
    identity = [c for c in candidates if c['evidence']['scheme'] == '510k'][0]
    assert identity['target'] == 'https://example.org/synthetic-fda/k510'
    assert identity['device'] == 'https://example.org/synthetic-fda/device1'
    named = [c for c in candidates if c['evidence']['scheme'] == 'device-name'][0]
    decision = dict(candidate_id=named['id'], status='verified', reviewer='SYNTHETIC-reviewer',
                    reviewed_at='2026-10-22T00:00:00Z', evidence_method='independent-identifier-confirmation')
    with pytest.raises(VerificationError, match='alone'):
        review_links(store, index, [decision])
    review_links(store, index, approvals(store))
    assert verify_corpus(store)['device_links']['status'] == 'verified'
    # A scheduler rerun must skip the completed source and preserve approvals.
    assert link_corpus(store, index, source_paths=paths)['sources_scanned'] == 0
    assert any(json.loads(row[0])['review'] for row in store.db.execute('SELECT payload FROM link_candidates'))


def test_article_revision_clears_reviews_and_rejects_obsolete_decisions(tmp_path):
    index, store, paths = setup(tmp_path)
    link_corpus(store, index, source_paths=paths)
    decisions = approvals(store)
    review_links(store, index, decisions)
    store.ingest_delta(paths['update.xml'], 2)
    with pytest.raises(VerificationError, match='obsolete'):
        review_links(store, index, decisions)
    link_corpus(store, index, source_paths=paths)
    assert not any(json.loads(row[0])['review'] for row in store.db.execute('SELECT payload FROM link_candidates'))
    assert verify_corpus(store)['device_links']['status'] == 'verified'


def test_changed_fda_catalog_requires_rematch_without_rebuilding_metadata(tmp_path):
    index, store, paths = setup(tmp_path)
    link_corpus(store, index, source_paths=paths)
    review_links(store, index, approvals(store))
    changed = tmp_path / 'changed-fda.ttl'
    changed.write_text((ROOT / 'fixtures/fda-canonical-synthetic.ttl').read_text().replace('Synthetic Infusion Device', 'Synthetic Other Infusion Device'))
    second = tmp_path / 'second-index.sqlite'
    build_index([changed], second)
    updated = FdaIndex(second)
    link_corpus(store, updated, source_paths=paths)
    assert not any(json.loads(row[0])['review'] for row in store.db.execute('SELECT payload FROM link_candidates'))
    assert store.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] == 1
    assert verify_corpus(store)['device_links']['status'] == 'verified'


def test_materialization_crash_blocks_reads_and_resumes(tmp_path):
    index, store, paths = setup(tmp_path)
    with Materialized(tmp_path / 'rdf') as target:
        target.load_fda(index)
        with pytest.raises(OSError):
            target.apply(store, fail_before_swap=True)
        assert target.value('checkpoint') is None
        with pytest.raises(VerificationError, match='incomplete'):
            target.query('SELECT ?s WHERE { ?s ?p ?o } LIMIT 1')
        target.apply(store)
        assert target.verify_current(store)['current_articles'] == 3
        assert target.apply(store)['loaded_batches'] == 0
        store.ingest_delta(paths['update.xml'], 2)
        with pytest.raises(VerificationError, match='behind'):
            target.require_current(store)
        target.apply(store)
        assert target.verify_current(store)['current_articles'] == 2
        assert target.query('SELECT ?x WHERE { <https://example.org/synthetic-fda/k510> <http://medicaldevice.com/ontology/k510Number> ?x }')['results']['bindings']


def test_materializer_detects_corrupt_input_without_checkpoint_advance(tmp_path):
    _, store, _ = setup(tmp_path)
    folder = store.root / store.db.execute('SELECT folder FROM corpus_batches').fetchone()[0]
    target_file = next(folder.glob('*.nq'))
    original = target_file.read_bytes()
    target_file.write_bytes(original + b'\n')
    with Materialized(tmp_path / 'rdf') as target:
        with pytest.raises(VerificationError, match='checksum'):
            target.apply(store)
        assert target.value('checkpoint') is None
        target_file.write_bytes(original)
        target.apply(store)
        assert target.verify_current(store)['status'] == 'verified'


def test_integrated_canonical_queries_and_deletion(tmp_path):
    report = run(tmp_path / 'demo')
    assert report['queries_passed'] == 12 and report['current_articles'] == 2
    assert report['stale_links_and_annotations_cleared'] is True
    assert report['full_pubmed_tested'] is False


def test_generic_record_link_does_not_turn_manufacturer_into_device(tmp_path):
    source = tmp_path / 'fda.ttl'
    source.write_text((ROOT / 'fixtures/fda-canonical-synthetic.ttl').read_text() +
        '\n<urn:synthetic:manufacturer> a <http://medicaldevice.com/ontology/Manufacturer>; <http://medicaldevice.com/ontology/hasFDARecord> <https://example.org/synthetic-fda/k510> .\n')
    build_index([source], tmp_path / 'index.sqlite')
    index = FdaIndex(tmp_path / 'index.sqlite')
    assert index.devices('https://example.org/synthetic-fda/k510') == ['https://example.org/synthetic-fda/device1']


def test_product_code_matches_category_and_cannot_verify_device_identity(tmp_path):
    index, store, paths = setup(tmp_path)
    source = tmp_path / 'codes.xml'
    source.write_text((ROOT / 'fixtures/update.xml').read_text().replace('Synthetic revised evaluation', 'FDA product code: ZZZ; CAT ordinary word; Synthetic revised evaluation'))
    store.ingest_delta(source, 2)
    link_corpus(store, index, max_sources=2, source_paths=dict(paths, **{'codes.xml': source}))
    value = next(json.loads(row[0]) for row in store.db.execute('SELECT payload FROM link_candidates') if json.loads(row[0])['category'])
    assert value['target'] == 'https://example.org/synthetic-fda/category1'
    assert value['device'] is None
    decision = dict(candidate_id=value['id'], status='verified', reviewer='SYNTHETIC-reviewer',
                    reviewed_at='2026-10-22T00:00:00Z', evidence_method='independent-identifier-confirmation')
    with pytest.raises(VerificationError, match='alone'):
        review_links(store, index, [decision])


def test_pipeline_scheduler_offline_whole_inventory_and_safe_rerun(tmp_path, monkeypatch):
    from pubmed_kg import pipeline
    from pubmed_kg.corpus import make_plan
    fda = tmp_path / 'fda.sqlite'
    build_index([ROOT / 'fixtures/fda-canonical-synthetic.ttl'], fda)
    config = dict(state=str(tmp_path / 'corpus'), plan=str(tmp_path / 'plan.json'), fda_index=str(fda),
                  rdf_store=str(tmp_path / 'rdf'), fda_ontology=str(ROOT / 'integration/fda/ontology.ttl'),
                  annotations_per_run=0, reserve_gib=0.001, batch_size=2)
    inputs = {'pubmed26n0001.xml.gz': gzip.compress((ROOT / 'fixtures/baseline.xml').read_bytes()),
              'pubmed26n0002.xml.gz': gzip.compress((ROOT / 'fixtures/update.xml').read_bytes())}
    base = 'https://ftp.ncbi.nlm.nih.gov/pubmed/'
    plan = make_plan([('pubmed26n0001.xml.gz', base + 'baseline/pubmed26n0001.xml.gz')],
                     [('pubmed26n0002.xml.gz', base + 'updatefiles/pubmed26n0002.xml.gz')])
    class Response:
        def __init__(self, text): self.text = text
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Client:
        def get(self, url): return Response(hashlib.md5(inputs[Path(url).name.removesuffix('.md5')]).hexdigest())
        def download(self, url, target, md5):
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(inputs[Path(url).name])
    monkeypatch.setattr(pipeline, 'Client', Client)
    monkeypatch.setattr(pipeline, 'discover_plan', lambda client: plan)
    first = pipeline.update(config, max_files=None)
    assert first['current_articles'] == 2 and first['linking_pending'] == 0
    assert first['automatic_verified_device_links'] == 0
    report = pipeline.verify(config)
    assert report['materialized']['current_articles'] == 2
    second = pipeline.update(config, max_files=None)
    assert second['metadata_sources_processed_this_run'] == 0
    assert second['local_rdf_checkpoint'] == first['local_rdf_checkpoint']


def test_ontology_without_instance_data_cannot_initialize_a_linked_pipeline(tmp_path):
    from pubmed_kg.pipeline import initialize
    config = dict(fda_sources=[str(ROOT / 'integration/fda/ontology.ttl')], fda_index=str(tmp_path / 'index.sqlite'),
                  fda_ontology=str(ROOT / 'integration/fda/ontology.ttl'))
    with pytest.raises(VerificationError, match='instance identifiers'):
        initialize(config)


def test_trial_crosswalk_requires_real_device_type_and_provenance(tmp_path):
    from pubmed_kg.pipeline import load_contexts
    index, store, paths = setup(tmp_path)
    crosswalk = tmp_path / 'crosswalk.ttl'
    crosswalk.write_text('@prefix k: <https://example.org/pubmed-kg/v1/> . <https://example.org/pubmed-kg/v1/trial/NCT00000001> a k:ClinicalTrial; k:device <https://example.org/synthetic-fda/device1> .')
    with Materialized(tmp_path / 'rdf') as target:
        with pytest.raises(VerificationError, match='provenance'):
            load_contexts(target, index, {'trial_device_file': str(crosswalk)})
        crosswalk.write_text(crosswalk.read_text() + '\n<https://example.org/pubmed-kg/v1/trial/NCT00000001> <http://www.w3.org/ns/prov#wasDerivedFrom> <urn:synthetic:registry> .')
        load_contexts(target, index, {'trial_device_file': str(crosswalk)})
        crosswalk.write_text(crosswalk.read_text().replace('synthetic-fda/device1', 'synthetic-fda/k510'))
        with pytest.raises(VerificationError, match='MedicalDevice'):
            load_contexts(target, index, {'trial_device_file': str(crosswalk)})


def test_cli_query_blocks_unmatched_catalog_links(tmp_path, monkeypatch):
    from pubmed_kg import cli
    index, store, paths = setup(tmp_path)
    # Metadata is current, but FDA candidates have not been generated yet.
    config = tmp_path / 'pipeline.json'
    config.write_text(json.dumps(dict(state=str(store.root), plan=str(tmp_path/'plan.json'),
      fda_index=str(tmp_path/'fda.sqlite'), rdf_store=str(tmp_path/'rdf'),
      fda_ontology=str(ROOT/'integration/fda/ontology.ttl'),
      fda_sources=[str(ROOT/'fixtures/fda-canonical-synthetic.ttl')])))
    with Materialized(tmp_path/'rdf') as target:
        target.load_fda(index)
        target.apply(store)
    monkeypatch.setattr('sys.argv',['pmkg','pipeline-query','--config',str(config),str(ROOT/'queries/fda/01_verified_510k.rq')])
    with pytest.raises(VerificationError,match='linking is incomplete'):
        cli.main()
