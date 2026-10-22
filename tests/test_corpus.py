import gzip
import hashlib
import json
from pathlib import Path

import pytest
from rdflib import Dataset, RDF

from pubmed_kg.audit import VerificationError
from pubmed_kg.corpus import (BASE, CorpusStore, make_plan, validate_plan, run_plan,
                              verify_corpus, sha256)
from pubmed_kg.graph import ROOT, K, iri


def listing(kind, numbers):
    return [(f'pubmed26n{n:04}.xml.gz', BASE + kind + f'/pubmed26n{n:04}.xml.gz') for n in numbers]


def sources(tmp_path):
    baseline = tmp_path / 'pubmed26n0001.xml.gz'
    update = tmp_path / 'pubmed26n0002.xml.gz'
    baseline.write_bytes(gzip.compress((ROOT / 'fixtures/baseline.xml').read_bytes(), mtime=0))
    update.write_bytes(gzip.compress((ROOT / 'fixtures/update.xml').read_bytes(), mtime=0))
    return baseline, update


def test_plan_refuses_missing_reordered_mixed_inventories():
    good = make_plan(listing('baseline', [1, 2]), listing('updatefiles', [3, 4]))
    validate_plan(good)
    for baseline, updates in [(listing('baseline', [1, 3]), []),
                              (listing('baseline', [1]), listing('updatefiles', [3])),
                              (listing('baseline', [1]), listing('updatefiles', [1])),
                              ([], listing('updatefiles', [1]))]:
        with pytest.raises(VerificationError):
            make_plan(baseline, updates)
    good['sources'].pop()
    with pytest.raises(VerificationError):
        validate_plan(good)
    with pytest.raises(VerificationError):
        make_plan(listing('baseline', [1]), [('pubmed27n0002.xml.gz', BASE + 'updatefiles/pubmed27n0002.xml.gz')])


def test_deltas_keep_every_article_replace_delete_and_query(tmp_path, monkeypatch):
    baseline, update = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    # The large-corpus path must never invoke the full snapshot exporter.
    monkeypatch.setattr(store, 'publish', lambda **kw: pytest.fail('full export invoked'))
    store.ingest_delta(baseline, 1, batch_size=1)
    assert not store.ingest_delta(baseline, 1)
    assert store.db.execute('SELECT count(*) FROM articles').fetchone()[0] == 3
    store.ingest_delta(update, 2, batch_size=1)
    report = verify_corpus(store)
    assert report['verification'] == 'passed'
    assert report['source_upsert_events'] == 4 and report['source_deletion_events'] == 1
    assert report['current_articles'] == 2 and report['deleted_pmids'] == 1
    dataset = Dataset(default_union=True)
    for folder, in store.db.execute('SELECT folder FROM corpus_batches ORDER BY ord'):
        path = store.root / folder
        for line in (path / 'replace.jsonl').read_text().splitlines():
            dataset.graph(json.loads(line)['graph']).remove((None, None, None))
        for nq in path.glob('part-*.nq'):
            dataset.parse(nq, format='nquads')
    assert len(list(dataset.subjects(RDF.type, K.Article))) == 2
    assert not list(dataset.graph(iri('article-graph', '900000003')))
    assert 'revised' in str(dataset.value(iri('article', '900000001'), K.title))
    dataset.default_graph.parse(ROOT / 'fixtures/adapter.ttl')
    # Genuine multi-hop example still works with the new per-article graph ownership.
    assert len(list(dataset.query((ROOT / 'queries/02_device_recalls.rq').read_text()))) == 1
    assert len(list(dataset.query((ROOT / 'queries/03_trial_devices.rq').read_text()))) == 2


def test_failed_delta_rolls_back_and_resumes(tmp_path, monkeypatch):
    baseline, _ = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    real = store.write_delta
    def fail(*args):
        real(*args)
        raise OSError('interrupted after validated output, before commit')
    monkeypatch.setattr(store, 'write_delta', fail)
    with pytest.raises(OSError):
        store.ingest_delta(baseline, 1, batch_size=1)
    assert store.status()['verified_files'] == 0
    assert store.status()['current_articles'] == 0
    assert store.db.execute('SELECT count(*) FROM corpus_heads').fetchone()[0] == 0
    monkeypatch.setattr(store, 'write_delta', real)
    store.ingest_delta(baseline, 1, batch_size=2)
    assert verify_corpus(store)['current_articles'] == 3
    assert len(list((store.root / store.db.execute('SELECT folder FROM corpus_batches').fetchone()[0]).glob('*.ttl'))) == 2


def test_out_of_order_and_changed_identity_rejected(tmp_path):
    baseline, update = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    with pytest.raises(VerificationError, match='prefix'):
        store.ingest_delta(update, 2)
    store.ingest_delta(baseline, 1)
    baseline.write_bytes(b'changed')
    with pytest.raises(VerificationError, match='identity'):
        store.ingest_delta(baseline, 1)


def test_corruption_detected_and_no_coverage_claim_for_partial(tmp_path):
    baseline, _ = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    store.bind_plan(make_plan(listing('baseline', [1]), listing('updatefiles', [2])))
    store.ingest_delta(baseline, 1)
    status = store.status()
    assert status['file_inventory_fraction'] == 0.5
    assert status['record_coverage_of_frozen_inventory'] is None
    assert status['medical_device_recall'] == 'not_measured'
    folder = store.root / store.db.execute('SELECT folder FROM corpus_batches').fetchone()[0]
    nq = next(folder.glob('*.nq'))
    nq.write_text(nq.read_text() + '\n')
    with pytest.raises(VerificationError, match='checksum'):
        verify_corpus(store)


def test_run_plan_bounded_resume_checksum_and_inventory(tmp_path):
    baseline, update = sources(tmp_path)
    paths = {p.name: p for p in [baseline, update]}
    plan = make_plan(listing('baseline', [1]), listing('updatefiles', [2]))
    downloaded = []
    class Response:
        def __init__(self, text): self.text = text
        def __enter__(self): return self
        def __exit__(self, *args): pass
    class Client:
        def get(self, url):
            path = paths[Path(url).name.removesuffix('.md5')]
            return Response(hashlib.md5(path.read_bytes()).hexdigest())
        def download(self, url, target, md5):
            downloaded.append(url)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(paths[Path(url).name].read_bytes())
    store = CorpusStore(tmp_path / 'state')
    assert run_plan(store, plan, Client())['status'] == 'incomplete'
    report = run_plan(store, plan, Client())
    assert report['status'] == 'complete_for_frozen_inventory'
    assert report['record_coverage_of_frozen_inventory'] == 1.0
    assert len(downloaded) == 2
    assert run_plan(store, plan, Client())['files_processed_this_run'] == 0
    assert verify_corpus(store)['verification'] == 'passed'


def test_schema_drift_stops_without_losing_previous_records(tmp_path):
    baseline, update = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(baseline, 1)
    text = gzip.decompress(update.read_bytes()).decode().replace('</ArticleTitle>', '</ArticleTitle><UnknownFutureField/>', 1)
    update.write_bytes(gzip.compress(text.encode()))
    with pytest.raises(VerificationError, match='unknown'):
        store.ingest_delta(update, 2)
    assert store.status()['current_articles'] == 3
    assert store.status()['verified_files'] == 1


def test_changed_vocabulary_and_replaced_baseline_require_new_state(tmp_path):
    baseline, _ = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    plan = make_plan(listing('baseline', [1]), [])
    store.bind_plan(plan)
    store.ingest_delta(baseline, 1)
    with pytest.raises(VerificationError, match='baseline inventory'):
        store.bind_plan(make_plan(listing('baseline', [1, 2]), []))
    (store.root / 'device-mesh.json').write_text('{"descriptors":{}}')
    with pytest.raises(VerificationError, match='vocabulary changed'):
        verify_corpus(store)


def test_delete_only_and_duplicate_source_events(tmp_path):
    baseline, _ = sources(tmp_path)
    raw = gzip.decompress(baseline.read_bytes()).decode()
    inner = raw.removeprefix('<PubmedArticleSet>').removesuffix('</PubmedArticleSet>')
    baseline.write_bytes(gzip.compress(('<PubmedArticleSet>' + inner + inner + '</PubmedArticleSet>').encode()))
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(baseline, 1)
    deletion = tmp_path / 'pubmed26n0002.xml.gz'
    deletion.write_bytes(gzip.compress(b'<PubmedArticleSet><DeleteCitation><PMID>900000001</PMID></DeleteCitation></PubmedArticleSet>'))
    store.ingest_delta(deletion, 2)
    report = verify_corpus(store)
    assert report['source_upsert_events'] == 6
    assert report['current_articles'] == 2
    assert report['deleted_pmids'] == 1


def test_checksum_failure_never_completes_source(tmp_path):
    baseline, _ = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    with pytest.raises(VerificationError, match='checksum'):
        store.ingest_delta(baseline, 1, upstream_md5='0' * 32)
    assert store.status()['verified_files'] == 0
    assert store.status()['current_articles'] == 0


def test_abstract_is_transient_and_unassessed_records_survive(tmp_path):
    baseline, _ = sources(tmp_path)
    raw = gzip.decompress(baseline.read_bytes()).decode().replace(
        '</ArticleTitle>', '</ArticleTitle><Abstract><AbstractText>PRIVATE_CORPUS_SENTINEL K654321</AbstractText></Abstract>', 1)
    baseline.write_bytes(gzip.compress(raw.encode()))
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(baseline, 1)
    records = [json.loads(row[0]) for row in store.db.execute('SELECT payload FROM articles')]
    assert len(records) == 3
    assert any(a['device_relevance']['status'] == 'unassessed' for a in records)
    assert any(d['value'] == 'K654321' for a in records for d in a['detections'])
    for path in store.root.rglob('*'):
        if path.suffix in ('.ttl', '.nq', '.json', '.jsonl'):
            assert 'PRIVATE_CORPUS_SENTINEL' not in path.read_text()


def test_complete_local_inventory_without_upstream_checksums_is_not_certified(tmp_path):
    baseline, _ = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    store.bind_plan(make_plan(listing('baseline', [1]), []))
    store.ingest_delta(baseline, 1)
    assert store.status()['file_inventory_fraction'] == 1.0
    assert store.status()['status'] == 'incomplete'
    assert store.status()['record_coverage_of_frozen_inventory'] is None


def test_named_graph_ownership_checked_beyond_checksums(tmp_path):
    from pubmed_kg.corpus import verify_partition
    baseline, _ = sources(tmp_path)
    store = CorpusStore(tmp_path / 'state')
    store.ingest_delta(baseline, 1)
    folder = store.root / store.db.execute('SELECT folder FROM corpus_batches').fetchone()[0]
    nq = next(folder.glob('*.nq'))
    # Same triple count, valid RDF syntax, wrong graph for one article.
    nq.write_text(nq.read_text().replace(str(iri('article-graph', '900000001')), 'urn:wrong-graph'))
    with pytest.raises(VerificationError, match='named graph'):
        verify_partition(folder, nq.stem, store.db.execute('SELECT * FROM articles ORDER BY pmid').fetchall(), sha256(baseline))
