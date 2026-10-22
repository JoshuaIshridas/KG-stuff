"""Bounded PubTator3 enrichment with atomic corpus annotation change sets."""
import json
import sqlite3
import tempfile
from pathlib import Path

from rdflib import Dataset, Graph
from rdflib.compare import isomorphic

from .audit import VerificationError
from .corpus import atomic_json, sha256
from .graph import build, check, iri, write
from .network import annotations
from .store import now
from .verification import ontology_check

PUBTATOR = 'https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson'
FIELDS = {'entityType', 'namespace', 'normalizedIdentifier', 'rawIdentifier', 'scope'}


def verify_annotation(folder, manifest):
    if manifest['status'] not in ('complete', 'failed', 'unavailable'):
        raise VerificationError('invalid annotation status')
    for item in manifest['annotations']:
        if set(item) != FIELDS or any(not isinstance(value, str) for value in item.values()):
            raise VerificationError('unsupported annotation payload')
    if manifest['status'] != 'complete' and manifest['annotations']:
        raise VerificationError('failed/unavailable annotation cannot contain completed entities')
    actual_files = {path.name for path in folder.iterdir() if path.name != 'manifest.json'}
    if actual_files != set(manifest['files']):
        raise VerificationError('unexpected annotation export file')
    for name, checksum in manifest['files'].items():
        if sha256(folder / name) != checksum:
            raise VerificationError('annotation export checksum mismatch')
    row = (manifest['pmid'], json.dumps(manifest['annotations']), manifest['status'], manifest['retrieved'])
    expected = build([], [row])
    g = Graph().parse(folder / 'annotations.ttl', format='turtle')
    dataset = Dataset()
    dataset.parse(folder / 'annotations.nq', format='nquads')
    graph_id = iri('article-annotations', manifest['pmid'])
    if not isomorphic(expected, g) or not isomorphic(expected, dataset.graph(graph_id)):
        raise VerificationError('annotation JSON/RDF mismatch')
    if {context for _, _, _, context in dataset.quads()} != {graph_id}:
        raise VerificationError('annotation graph ownership mismatch')
    check(g)
    ontology_check(g)


def enrich_corpus(store, client, limit=10, refresh=False, fixture=None):
    store.check_contract()
    if not 1 <= limit <= 1000:
        raise ValueError('enrichment limit must be between 1 and 1000')
    rows = store.db.execute('''SELECT a.pmid,a.ord FROM articles a
        LEFT JOIN enrichment e ON a.pmid=e.pmid
        WHERE ? OR e.status IS NULL OR e.status!='complete'
        ORDER BY CASE WHEN e.status='pending' THEN 0 ELSE 1 END,
                 CASE WHEN ? AND e.retrieved IS NULL THEN 1 ELSE 0 END,
                 COALESCE(e.retrieved,''),a.pmid LIMIT ?''', (refresh, refresh, limit)).fetchall()
    counts = dict(complete=0, unavailable=0, failed=0)
    for pmid, source_order in rows:
        retrieved = now()
        # Pending survives process death and is selected on the next invocation.
        # The previous committed annotation head remains valid until replacement.
        store.db.execute('INSERT OR REPLACE INTO enrichment VALUES(?,?,?,?)', (pmid, '[]', 'pending', retrieved))
        store.db.commit()
        try:
            if fixture:
                data = json.loads(Path(fixture).read_text())
            else:
                with client.get(PUBTATOR, params={'pmids': pmid}) as response:
                    data = response.json()
            values, status = annotations(data, pmid)
        except (RuntimeError, ValueError, TypeError, KeyError, AttributeError):
            values, status = [], 'failed'
        try:
            store.db.execute('BEGIN')
            revision = store.db.execute('SELECT COALESCE(MAX(id),0)+1 FROM corpus_annotation_batches').fetchone()[0]
            folder = store.root / 'annotation-deltas' / f'{revision:09}-{pmid}'
            folder.mkdir(parents=True, exist_ok=True)
            row = (pmid, json.dumps(values), status, retrieved)
            g = build([], [row])
            write(g, folder / 'annotations.ttl')
            graph_id = iri('article-annotations', pmid).n3()
            with (folder / 'annotations.nq').open('w') as stream:
                for line in sorted(g.serialize(format='nt').splitlines()):
                    stream.write(line.rsplit('.', 1)[0].rstrip() + ' ' + graph_id + ' .\n')
            manifest = dict(format='pubmed-annotation-delta-v1', revision=revision, pmid=pmid,
                            source_order=source_order, source='PubTator3', source_url=PUBTATOR,
                            retrieved=retrieved, status=status, annotations=values,
                            clear_graphs=[str(iri('article-annotations', pmid))],
                            files={name: sha256(folder / name) for name in ['annotations.ttl', 'annotations.nq']})
            atomic_json(folder / 'manifest.json', manifest)
            verify_annotation(folder, manifest)
            store.db.execute('INSERT INTO corpus_annotation_batches VALUES(?,?,?)',
                             (revision, str(folder.relative_to(store.root)), sha256(folder / 'manifest.json')))
            store.db.execute('INSERT OR REPLACE INTO corpus_annotation_heads VALUES(?,?)', (pmid, revision))
            store.db.execute('INSERT OR REPLACE INTO enrichment VALUES(?,?,?,?)', (pmid, json.dumps(values), status, retrieved))
            store.db.execute("INSERT INTO corpus_changes(kind,reference) VALUES('annotation',?)", (revision,))
            store.db.commit()
            counts[status] += 1
        except Exception:
            store.db.rollback()
            raise
    report = dict(processed=len(rows), statuses=counts, source='PubTator3', metadata_unchanged=True,
                  refresh=refresh, next_action='Retry pending, failed or unavailable articles; complete articles are skipped unless --refresh.')
    atomic_json(store.root / 'corpus-enrichment-report.json', report)
    return report


def verify_enrichment(store):
    """Replay the unified metadata/annotation feed into a bounded disk-backed index."""
    with tempfile.TemporaryDirectory(prefix='annotation-verify-', dir=store.root) as scratch:
        db = sqlite3.connect(str(Path(scratch) / 'heads.sqlite'))
        try:
            db.executescript('CREATE TABLE articles(pmid TEXT PRIMARY KEY,ord INTEGER); CREATE TABLE heads(pmid TEXT PRIMARY KEY,revision INTEGER);')
            metadata = annotation_count = 0
            for kind, reference in store.db.execute('SELECT kind,reference FROM corpus_changes ORDER BY seq'):
                if kind == 'metadata':
                    metadata += 1
                    if reference != metadata:
                        raise VerificationError('metadata changefeed order mismatch')
                    row = store.db.execute('SELECT folder FROM corpus_batches WHERE ord=?', (reference,)).fetchone()
                    if not row:
                        raise VerificationError('changefeed references unknown metadata batch')
                    with (store.root / row[0] / 'replace.jsonl').open() as stream:
                        for line in stream:
                            event = json.loads(line)
                            db.execute('DELETE FROM articles WHERE pmid=?', (event['pmid'],))
                            db.execute('DELETE FROM heads WHERE pmid=?', (event['pmid'],))
                            if event['operation'] == 'upsert':
                                db.execute('INSERT INTO articles VALUES(?,?)', (event['pmid'], reference))
                elif kind == 'annotation':
                    annotation_count += 1
                    if reference != annotation_count:
                        raise VerificationError('annotation changefeed order mismatch')
                    entry = store.db.execute('SELECT folder,manifest_sha256 FROM corpus_annotation_batches WHERE id=?', (reference,)).fetchone()
                    if not entry:
                        raise VerificationError('missing annotation batch')
                    folder = store.root / entry[0]
                    if sha256(folder / 'manifest.json') != entry[1]:
                        raise VerificationError('annotation manifest checksum mismatch')
                    manifest = json.loads((folder / 'manifest.json').read_text())
                    if db.execute('SELECT ord FROM articles WHERE pmid=?', (manifest['pmid'],)).fetchone() != (manifest['source_order'],):
                        raise VerificationError('annotation belongs to an obsolete article revision')
                    verify_annotation(folder, manifest)
                    db.execute('INSERT OR REPLACE INTO heads VALUES(?,?)', (manifest['pmid'], reference))
                elif kind == 'links':
                    continue  # Independently replayed by device_links.verify_links.
                else:
                    raise VerificationError('unknown changefeed kind')
            if metadata != store.db.execute('SELECT COUNT(*) FROM corpus_batches').fetchone()[0] or annotation_count != store.db.execute('SELECT COUNT(*) FROM corpus_annotation_batches').fetchone()[0]:
                raise VerificationError('changefeed batch missing')
            from itertools import zip_longest
            for a, b in zip_longest(db.execute('SELECT * FROM heads ORDER BY pmid'), store.db.execute('SELECT * FROM corpus_annotation_heads ORDER BY pmid')):
                if a != b:
                    raise VerificationError('annotation heads disagree with changefeed')
            pending = 0
            for pmid, in db.execute('SELECT pmid FROM heads'):
                if not store.db.execute('SELECT 1 FROM enrichment WHERE pmid=?',(pmid,)).fetchone():
                    raise VerificationError('annotation head missing its enrichment row')
            for pmid, payload, status, retrieved in store.db.execute('SELECT * FROM enrichment'):
                if not db.execute('SELECT 1 FROM articles WHERE pmid=?', (pmid,)).fetchone():
                    raise VerificationError('enrichment remains for deleted article')
                if status == 'pending':
                    pending += 1
                    continue
                head = db.execute('SELECT revision FROM heads WHERE pmid=?', (pmid,)).fetchone()
                if not head:
                    raise VerificationError('enrichment has no committed export')
                folder = store.root / store.db.execute('SELECT folder FROM corpus_annotation_batches WHERE id=?', head).fetchone()[0]
                manifest = json.loads((folder / 'manifest.json').read_text())
                if (json.loads(payload), status, retrieved) != (manifest['annotations'], manifest['status'], manifest['retrieved']):
                    raise VerificationError('enrichment ledger disagrees with export')
            return dict(annotation_batches=annotation_count, pending=pending, status='verified')
        finally:
            db.close()
