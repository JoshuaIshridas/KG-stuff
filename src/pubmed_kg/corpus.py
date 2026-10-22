"""Unfiltered, disk-backed baseline reconstruction and verified RDF deltas.

The ledger is the commit authority. Unreferenced output directories are orphans,
never completed batches. No corpus-sized Python collection or graph is built.
"""
import hashlib
import json
import os
import re
import sqlite3
import tempfile
import uuid
from pathlib import Path

from rdflib import Dataset, Graph, Literal, RDF
from rdflib.compare import isomorphic

from .audit import VerificationError, canonical, json_inventory, prepare, reconcile, verify_rdf
from .graph import K, P, build, check, iri, write
from .model import extraction_fingerprint, mapping_fingerprint
from .network import discover
from .store import Store, now
from .verification import ontology_check

BASE = 'https://ftp.ncbi.nlm.nih.gov/pubmed/'
FILENAME = re.compile(r'pubmed(\d+)n(\d+)\.xml\.gz')


def sha256(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(block)
    return h.hexdigest()


def atomic_json(path, value):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    temp = path.with_suffix(path.suffix + '.tmp')
    temp.write_text(json.dumps(value, indent=2) + '\n')
    os.replace(temp, path)


def plan_id(plan):
    return hashlib.sha256(canonical({k: v for k, v in plan.items() if k != 'plan_sha256'}).encode()).hexdigest()


def make_plan(baseline, updates):
    """Freeze independently discovered complete listings, with dependency checks."""
    sources, vintage = [], None
    for kind, listing in [('baseline', baseline), ('updatefiles', updates)]:
        numbered = []
        for name, url in listing:
            match = FILENAME.fullmatch(name)
            if not match or url != BASE + kind + '/' + name:
                raise VerificationError('nonofficial source in inventory')
            year, number = map(int, match.groups())
            if vintage is None:
                vintage = year
            if year != vintage:
                raise VerificationError('annual rollover requires a new state and plan')
            numbered.append((number, name, url))
        for number, name, url in sorted(numbered):
            if number != len(sources) + 1:
                raise VerificationError('missing, duplicate or overlapping baseline/update dependency')
            sources.append(dict(name=name, url=url, kind=kind, order=number))
        if kind == 'baseline' and not numbered:
            raise VerificationError('empty baseline inventory')
    result = dict(format='pubmed-corpus-plan-v1', observed_at=now(), vintage=vintage,
                  scope='All files in the observed official baseline and update listings; no relevance filter.',
                  sources=sources)
    result['plan_sha256'] = plan_id(result)
    return result


def validate_plan(plan):
    if plan.get('format') != 'pubmed-corpus-plan-v1' or plan.get('plan_sha256') != plan_id(plan):
        raise VerificationError('plan identity mismatch')
    sources = plan['sources']
    reconstructed = make_plan(
        [(s['name'], s['url']) for s in sources if s['kind'] == 'baseline'],
        [(s['name'], s['url']) for s in sources if s['kind'] == 'updatefiles'])
    if sources != reconstructed['sources'] or plan['vintage'] != reconstructed['vintage']:
        raise VerificationError('invalid source sequence')


def discover_plan(client):
    return make_plan(discover(client, BASE + 'baseline/'), discover(client, BASE + 'updatefiles/'))


def article_graph(row, digest, mapping_version):
    g = build([row])
    source = iri('source', row[2])
    run = iri('run', digest + '-' + mapping_version)
    g.add((source, K.sha256, Literal(digest)))
    g.add((run, RDF.type, P.Activity))
    g.add((iri('article', row[0]), P.wasGeneratedBy, run))
    return g


def verify_partition(folder, part, rows, digest):
    """Independently reparse both exports, including per-PMID graph ownership."""
    union = Graph().parse(folder / (part + '.ttl'), format='turtle')
    dataset = Dataset()
    dataset.parse(folder / (part + '.nq'), format='nquads')
    expected_union, expected_graphs = Graph(), set()
    mapping_version = mapping_fingerprint()
    for row in rows:
        graph_id = iri('article-graph', row[0])
        expected_graphs.add(graph_id)
        expected = article_graph(row, digest, mapping_version)
        if not isomorphic(expected, dataset.graph(graph_id)):
            raise VerificationError('named graph does not match article JSON')
        expected_union += expected
    actual_graphs = {context for _, _, _, context in dataset.quads()}
    if actual_graphs != expected_graphs or not isomorphic(union, expected_union):
        raise VerificationError('Turtle/N-Quads content or graph ownership mismatch')
    check(union)
    ontology_check(union)
    result = verify_rdf(union, rows)
    result['named_graphs'] = len(expected_graphs)
    return result


class CorpusStore(Store):
    def __init__(self, root):
        super().__init__(root)
        self.db.executescript('''
            CREATE TABLE IF NOT EXISTS corpus_batches(
                ord INTEGER PRIMARY KEY, digest TEXT, folder TEXT, manifest_sha256 TEXT, upstream_md5 TEXT);
            CREATE TABLE IF NOT EXISTS corpus_heads(
                pmid TEXT PRIMARY KEY, ord INTEGER, part TEXT);
            CREATE TABLE IF NOT EXISTS corpus_meta(key TEXT PRIMARY KEY, value TEXT);
            CREATE TABLE IF NOT EXISTS corpus_changes(seq INTEGER PRIMARY KEY,kind TEXT,reference INTEGER);
            CREATE TABLE IF NOT EXISTS corpus_annotation_batches(id INTEGER PRIMARY KEY,folder TEXT,manifest_sha256 TEXT);
            CREATE TABLE IF NOT EXISTS corpus_annotation_heads(pmid TEXT PRIMARY KEY,revision INTEGER);
            CREATE TABLE IF NOT EXISTS corpus_link_batches(id INTEGER PRIMARY KEY,folder TEXT,manifest_sha256 TEXT);
            CREATE TABLE IF NOT EXISTS corpus_link_heads(pmid TEXT PRIMARY KEY,revision INTEGER);
            CREATE INDEX IF NOT EXISTS link_head_revision ON corpus_link_heads(revision,pmid);
            CREATE TABLE IF NOT EXISTS link_status(pmid TEXT PRIMARY KEY,ord INTEGER,catalog TEXT);
            CREATE TABLE IF NOT EXISTS link_source_status(name TEXT PRIMARY KEY,ord INTEGER,catalog TEXT);
            CREATE TABLE IF NOT EXISTS link_candidates(id TEXT PRIMARY KEY,pmid TEXT,payload TEXT);
            CREATE INDEX IF NOT EXISTS candidate_pmid ON link_candidates(pmid,id);
            CREATE TABLE IF NOT EXISTS link_pending(pmid TEXT PRIMARY KEY,payload TEXT);
            CREATE INDEX IF NOT EXISTS articles_source_order ON articles(source,ord,pmid);
            CREATE INDEX IF NOT EXISTS articles_order ON articles(ord,pmid);
            CREATE INDEX IF NOT EXISTS tombstones_order ON tombstones(ord,pmid);
        ''')
        if self.db.execute('SELECT COUNT(*) FROM sources').fetchone()[0] and not self.db.execute('SELECT COUNT(*) FROM corpus_meta').fetchone()[0]:
            raise VerificationError('corpus mode requires a fresh state directory')
        self.db.execute("INSERT OR IGNORE INTO corpus_meta VALUES('engine','delta-v1')")
        self.db.execute("INSERT OR IGNORE INTO corpus_meta VALUES('instance',?)", (str(uuid.uuid4()),))
        self.db.commit()

    def check_contract(self):
        vocabulary = self.root / 'device-mesh.json'
        contract = canonical(dict(extraction=extraction_fingerprint(), mapping=mapping_fingerprint(),
                                  vocabulary=sha256(vocabulary) if vocabulary.exists() else 'bundled'))
        old = self.db.execute("SELECT value FROM corpus_meta WHERE key='contract'").fetchone()
        if old and old[0] != contract:
            raise VerificationError('extraction, mapping or vocabulary changed; start a fresh corpus state')
        if not old:
            self.db.execute("INSERT INTO corpus_meta VALUES('contract',?)", (contract,))
            self.db.commit()

    def bind_plan(self, plan):
        self.check_contract()
        validate_plan(plan)
        old = self.db.execute("SELECT value FROM corpus_meta WHERE key='plan'").fetchone()
        if old:
            previous = json.loads(old[0])
            if previous['vintage'] != plan['vintage'] or plan['sources'][:len(previous['sources'])] != previous['sources']:
                raise VerificationError('plan can only append same-vintage updates')
            if any(s['kind'] != 'updatefiles' for s in plan['sources'][len(previous['sources']):]):
                raise VerificationError('baseline inventory changed; start a fresh annual state')
        for name, order in self.db.execute('SELECT name,ord FROM sources'):
            if order < 1 or order > len(plan['sources']) or plan['sources'][order - 1]['name'] != name:
                raise VerificationError('existing state does not match the planned source prefix')
        self.db.execute("INSERT OR REPLACE INTO corpus_meta VALUES('plan',?)", (canonical(plan),))
        self.db.commit()

    def ingest_delta(self, path, order, batch_size=50, upstream_md5=None):
        self.check_contract()
        if not 1 <= batch_size <= 500:
            raise ValueError('batch size must be between 1 and 500')
        path = Path(path)
        name, digest = path.name, sha256(path)
        plan_row = self.db.execute("SELECT value FROM corpus_meta WHERE key='plan'").fetchone()
        if plan_row:
            sources = json.loads(plan_row[0])['sources']
            if order < 1 or order > len(sources) or sources[order - 1]['name'] != name:
                raise VerificationError('source does not match frozen plan')
        if upstream_md5 is not None:
            h = hashlib.md5()
            with path.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
            if h.hexdigest() != upstream_md5:
                raise VerificationError('upstream checksum mismatch')
        old = self.db.execute('SELECT digest,ord,status FROM sources WHERE name=?', (name,)).fetchone()
        if old:
            if old[:2] != (digest, order):
                raise VerificationError('source identity changed')
            if old[2] == 'exported':
                batch = self.db.execute('SELECT folder,manifest_sha256 FROM corpus_batches WHERE ord=?', (order,)).fetchone()
                if not batch or sha256(self.root / batch[0] / 'manifest.json') != batch[1]:
                    raise VerificationError('committed delta manifest missing or corrupted')
                manifest = json.loads((self.root / batch[0] / 'manifest.json').read_text())
                if manifest['extraction_sha256'] != extraction_fingerprint() or manifest['mapping_sha256'] != mapping_fingerprint():
                    raise VerificationError('contract changed; use a fresh corpus state')
                return False
        expected_order = self.db.execute('SELECT COALESCE(MAX(ord),0)+1 FROM corpus_batches').fetchone()[0]
        if order != expected_order:
            raise VerificationError('corpus sources must form a committed contiguous prefix starting at 1')
        collision = self.db.execute('SELECT name FROM sources WHERE ord=?', (order,)).fetchone()
        if collision and collision[0] != name:
            raise VerificationError('source order collision')
        self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,NULL)', (name, digest, order, 'downloaded'))
        self.db.commit()
        try:
            vocabulary_path = self.root / 'device-mesh.json'
            vocabulary = json.loads(vocabulary_path.read_text())['descriptors'] if vocabulary_path.exists() else None
            audit = self.root / 'audits' / digest
            json_path, report = prepare(path, audit, vocabulary)
            self.db.execute('BEGIN')
            with json_path.open() as stream:
                for line in stream:
                    event = json.loads(line)
                    pmid, record = event['pmid'], event['record']
                    self.db.execute('DELETE FROM articles WHERE pmid=?', (pmid,))
                    self.db.execute('DELETE FROM tombstones WHERE pmid=?', (pmid,))
                    self.db.execute('DELETE FROM enrichment WHERE pmid=?', (pmid,))
                    self.db.execute('DELETE FROM corpus_heads WHERE pmid=?', (pmid,))
                    self.db.execute('DELETE FROM corpus_annotation_heads WHERE pmid=?', (pmid,))
                    self.db.execute('DELETE FROM corpus_link_heads WHERE pmid=?', (pmid,))
                    self.db.execute('DELETE FROM link_status WHERE pmid=?', (pmid,))
                    self.db.execute('DELETE FROM link_candidates WHERE pmid=?', (pmid,))
                    if record is None:
                        self.db.execute('INSERT INTO tombstones VALUES(?,?)', (pmid, order))
                    else:
                        self.db.execute('INSERT INTO articles VALUES(?,?,?,?)', (pmid, json.dumps(record), name, order))
            self.db.execute("UPDATE sources SET status='parsed' WHERE name=?", (name,))
            folder = self.write_delta(name, digest, order, batch_size)
            manifest_path = folder / 'manifest.json'
            manifest = json.loads(manifest_path.read_text())
            manifest['upstream_md5'] = upstream_md5
            atomic_json(manifest_path, manifest)
            if upstream_md5:
                atomic_json(audit / 'upstream-checksum.json', dict(algorithm='MD5', value=upstream_md5, verified_at=now()))
            report.update(status='verified', delta=folder.name,
                          workflow=['verify-source-count', 'write-json', 'verify-json', 'write-turtle', 'verify-turtle'])
            atomic_json(audit / '03-rdf-verification.json', report)
            self.db.execute("UPDATE sources SET status='validated' WHERE name=?", (name,))
            self.db.execute('INSERT INTO corpus_batches VALUES(?,?,?,?,?)',
                            (order, digest, str(folder.relative_to(self.root)), sha256(folder / 'manifest.json'), upstream_md5))
            self.db.execute("INSERT INTO corpus_changes(kind,reference) VALUES('metadata',?)", (order,))
            self.db.execute("UPDATE sources SET status='exported',completed=? WHERE name=?", (now(), name))
            # This single SQLite commit is the completion marker. Files already exist
            # and have been reparsed/validated. No filesystem pointer can get ahead.
            self.db.commit()
            return True
        except Exception:
            self.db.rollback()
            self.db.execute("UPDATE sources SET status='failed' WHERE name=?", (name,))
            self.db.commit()
            raise

    def write_delta(self, name, digest, order, batch_size):
        folder = self.root / 'deltas' / f'{order:06}-{digest}'
        folder.mkdir(parents=True, exist_ok=True)
        # A failed attempt may have used a different partition size. Remove only
        # uncommitted attempt artifacts; no committed folder is rewritten.
        for path in folder.glob('part-*'):
            path.unlink()
        parts, reports = {}, []
        mapping_version = mapping_fingerprint()
        cursor = self.db.execute('SELECT * FROM articles WHERE source=? AND ord=? ORDER BY pmid', (name, order))
        while True:
            rows = cursor.fetchmany(batch_size)
            if not rows:
                break
            part = f'part-{len(reports):06}'
            union = Graph()
            with (folder / (part + '.nq')).open('w') as stream:
                for row in rows:
                    graph = article_graph(row, digest, mapping_version)
                    union += graph
                    graph_id = iri('article-graph', row[0]).n3()
                    for line in sorted(graph.serialize(format='nt').splitlines()):
                        stream.write(line.rsplit('.', 1)[0].rstrip() + ' ' + graph_id + ' .\n')
                    self.db.execute('INSERT INTO corpus_heads VALUES(?,?,?)', (row[0], order, part))
            write(union, folder / (part + '.ttl'))
            reports.append(verify_partition(folder, part, rows, digest))
            for suffix in ['.ttl', '.nq']:
                parts[part + suffix] = sha256(folder / (part + suffix))
        # Disk-backed replacement index, also includes deletion-only batches.
        with (folder / 'replace.jsonl').open('w') as stream:
            for pmid, operation in self.db.execute('''
                SELECT pmid,'upsert' FROM articles WHERE ord=?
                UNION ALL SELECT pmid,'delete' FROM tombstones WHERE ord=? ORDER BY pmid''', (order, order)):
                stream.write(canonical(replacement(pmid, operation)) + '\n')
        parts['replace.jsonl'] = sha256(folder / 'replace.jsonl')
        atomic_json(folder / 'manifest.json', dict(
            format='pubmed-article-delta-v1', source=name, source_sha256=digest, order=order,
            extraction_sha256=extraction_fingerprint(), mapping_sha256=mapping_fingerprint(),
            files=parts, partitions=reports, articles=sum(p['articles'] for p in reports),
            loading='In one transaction clear every graph in replace.jsonl, then load all .nq files. Never append without clearing.',
            text_policy='No abstracts or article bodies. All supplied citations retained, irrespective of device screening.'))
        return folder

    def status(self):
        row = self.db.execute("SELECT value FROM corpus_meta WHERE key='plan'").fetchone()
        plan = json.loads(row[0]) if row else None
        completed = self.db.execute('SELECT COUNT(*) FROM corpus_batches').fetchone()[0]
        total = len(plan['sources']) if plan else None
        checksummed = self.db.execute('SELECT COUNT(*) FROM corpus_batches WHERE upstream_md5 IS NOT NULL').fetchone()[0]
        complete = bool(total and completed == total and checksummed == total)
        return dict(status='complete_for_frozen_inventory' if complete else 'incomplete',
                    checked_at=now(), plan_sha256=plan['plan_sha256'] if plan else None,
                    inventory_observed_at=plan['observed_at'] if plan else None,
                    planned_files=total, verified_files=completed,
                    official_checksum_verified_files=checksummed,
                    file_inventory_fraction=completed / total if total else None,
                    current_articles=self.db.execute('SELECT COUNT(*) FROM articles').fetchone()[0],
                    deleted_pmids=self.db.execute('SELECT COUNT(*) FROM tombstones').fetchone()[0],
                    record_coverage_of_frozen_inventory=1.0 if complete else None,
                    medical_device_recall='not_measured',
                    scope='Ledger status only; corpus-verify independently replays JSON and checks exports. File fractions are not record fractions. A frozen inventory is not real-time PubMed.')


def run_plan(store, plan, client, max_files=1, batch_size=50):
    store.bind_plan(plan)
    processed = 0
    for source in plan['sources']:
        old = store.db.execute('SELECT status FROM sources WHERE name=?', (source['name'],)).fetchone()
        if old == ('exported',):
            continue
        if max_files is not None and processed >= max_files:
            break
        with client.get(source['url'] + '.md5') as response:
            match = re.search(r'\b[0-9a-fA-F]{32}\b', response.text)
        if not match:
            raise VerificationError('official source checksum unavailable')
        md5 = match.group().lower()
        target = store.root / 'raw-private' / source['name']
        cached = False
        if target.exists():
            h = hashlib.md5()
            with target.open('rb') as stream:
                for block in iter(lambda: stream.read(1024 * 1024), b''):
                    h.update(block)
            cached = h.hexdigest() == md5
        if not cached:
            client.download(source['url'], target, md5)
        # Confirm cached and downloaded files equally; never trust a mock or a
        # stale cache simply because a download helper returned successfully.
        h = hashlib.md5()
        with target.open('rb') as stream:
            for block in iter(lambda: stream.read(1024 * 1024), b''):
                h.update(block)
        if h.hexdigest() != md5:
            raise VerificationError('official MD5 mismatch')
        store.ingest_delta(target, source['order'], batch_size, upstream_md5=md5)
        processed += 1
    result = store.status()
    result['files_processed_this_run'] = processed
    atomic_json(store.root / 'corpus-status.json', result)
    return result


def verify_corpus(store):
    """Full independent on-disk replay; run explicitly, not after every update."""
    store.check_contract()
    with tempfile.TemporaryDirectory(prefix='corpus-verify-', dir=store.root) as scratch:
        expected = sqlite3.connect(str(Path(scratch) / 'expected.sqlite'))
        try:
            expected.executescript('''
                CREATE TABLE articles(pmid TEXT PRIMARY KEY,payload TEXT,source TEXT,ord INTEGER);
                CREATE TABLE tombstones(pmid TEXT PRIMARY KEY,ord INTEGER);
                CREATE TABLE heads(pmid TEXT PRIMARY KEY,ord INTEGER,part TEXT);
                CREATE TABLE batch_ids(pmid TEXT PRIMARY KEY,operation TEXT);
                CREATE TABLE seen(pmid TEXT PRIMARY KEY);
                CREATE INDEX articles_order ON articles(ord,pmid);
            ''')
            files = upserts = deletes = 0
            for name, digest, order, status in store.db.execute('SELECT name,digest,ord,status FROM sources ORDER BY ord'):
                if status != 'exported' or order != files + 1:
                    raise VerificationError('incomplete corpus source prefix')
                audit = store.root / 'audits' / digest
                report = json.loads((audit / '03-rdf-verification.json').read_text())
                if report['status'] != 'verified' or report['implementation_sha256'] != extraction_fingerprint():
                    raise VerificationError('source extraction contract mismatch')
                inventory = json.loads((audit / '01-source-count.json').read_text())
                observed = json_inventory(audit / 'records.jsonl')
                reconcile(inventory, observed)
                upserts += observed['counts'].get('article', 0) + observed['counts'].get('book', 0)
                deletes += observed['counts'].get('delete', 0)
                expected.execute('DELETE FROM batch_ids')
                expected.execute('DELETE FROM seen')
                with (audit / 'records.jsonl').open() as stream:
                    for line in stream:
                        event = json.loads(line)
                        pmid, record = event['pmid'], event['record']
                        for table in ['articles', 'tombstones', 'heads']:
                            expected.execute(f'DELETE FROM {table} WHERE pmid=?', (pmid,))
                        expected.execute('INSERT OR REPLACE INTO batch_ids VALUES(?,?)', (pmid, event['operation']))
                        if record is None:
                            expected.execute('INSERT INTO tombstones VALUES(?,?)', (pmid, order))
                        else:
                            expected.execute('INSERT INTO articles VALUES(?,?,?,?)', (pmid, json.dumps(record), name, order))
                entry = store.db.execute('SELECT folder,manifest_sha256,upstream_md5 FROM corpus_batches WHERE ord=? AND digest=?', (order, digest)).fetchone()
                if not entry:
                    raise VerificationError('missing committed delta')
                folder = store.root / entry[0]
                if sha256(folder / 'manifest.json') != entry[1]:
                    raise VerificationError('delta manifest checksum mismatch')
                manifest = json.loads((folder / 'manifest.json').read_text())
                if manifest.get('upstream_md5') != entry[2]:
                    raise VerificationError('upstream checksum provenance mismatch')
                if entry[2] and json.loads((audit / 'upstream-checksum.json').read_text())['value'] != entry[2]:
                    raise VerificationError('upstream checksum audit mismatch')
                if manifest['mapping_sha256'] != mapping_fingerprint() or manifest['extraction_sha256'] != extraction_fingerprint():
                    raise VerificationError('delta contract changed')
                if (manifest['source'], manifest['source_sha256'], manifest['order']) != (name, digest, order):
                    raise VerificationError('delta identity mismatch')
                actual_files = {p.name for p in folder.iterdir() if p.name != 'manifest.json'}
                if actual_files != set(manifest['files']):
                    raise VerificationError('unexpected or missing delta file')
                for filename, checksum in manifest['files'].items():
                    if sha256(folder / filename) != checksum:
                        raise VerificationError('delta file checksum mismatch')
                with (folder / 'replace.jsonl').open() as stream:
                    from itertools import zip_longest
                    for event, row in zip_longest((json.loads(line) for line in stream), expected.execute('SELECT * FROM batch_ids ORDER BY pmid')):
                        if not row or event != replacement(row[0], row[1]):
                            raise VerificationError('replacement/deletion manifest mismatch')
                for ttl in sorted(folder.glob('part-*.ttl')):
                    g = Graph().parse(ttl, format='turtle')
                    rows = []
                    for article in g.subjects(RDF.type, K.Article):
                        pmid = str(g.value(article, K.pmid))
                        row = expected.execute('SELECT * FROM articles WHERE pmid=? AND ord=?', (pmid, order)).fetchone()
                        if not row:
                            raise VerificationError('delta contains stale or deleted article')
                        try:
                            expected.execute('INSERT INTO seen VALUES(?)', (pmid,))
                        except sqlite3.IntegrityError:
                            raise VerificationError('duplicate delta article') from None
                        rows.append(row)
                        expected.execute('INSERT INTO heads VALUES(?,?,?)', (pmid, order, ttl.stem))
                    verify_partition(folder, ttl.stem, rows, digest)
                if expected.execute('SELECT COUNT(*) FROM seen').fetchone()[0] != expected.execute('SELECT COUNT(*) FROM articles WHERE ord=?', (order,)).fetchone()[0]:
                    raise VerificationError('missing delta article')
                files += 1
            from itertools import zip_longest
            if files != store.db.execute('SELECT COUNT(*) FROM corpus_batches').fetchone()[0]:
                raise VerificationError('orphan committed batch in ledger')
            for table, query in [('articles', 'SELECT * FROM articles ORDER BY pmid'), ('tombstones', 'SELECT * FROM tombstones ORDER BY pmid'), ('heads', 'SELECT * FROM corpus_heads ORDER BY pmid')]:
                for a, b in zip_longest(expected.execute(f'SELECT * FROM {table} ORDER BY pmid'), store.db.execute(query)):
                    if a != b:
                        raise VerificationError('independent replay disagrees with ' + table)
            from .enrichment import verify_enrichment
            enrichment_report = verify_enrichment(store)
            from .device_links import verify_links
            link_report = verify_links(store)
            result = dict(store.status(), verification='passed', enrichment=enrichment_report, device_links=link_report, source_upsert_events=upserts,
                          source_deletion_events=deletes, record_event_retention=1.0,
                          eligible_metadata_retention=1.0, graph_ownership='one named graph per PMID')
            atomic_json(store.root / 'corpus-verification.json', result)
            return result
        finally:
            expected.close()


def replacement(pmid, operation):
    return dict(pmid=pmid, operation=operation, graph=str(iri('article-graph', pmid)),
                clear_graphs=[str(iri('article-graph', pmid)), str(iri('article-annotations', pmid)), str(iri('article-links', pmid))])
