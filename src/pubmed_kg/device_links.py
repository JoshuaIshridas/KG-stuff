"""Versioned FDA candidate/review change sets in the corpus commit feed."""
import datetime
import gzip
import hashlib
import json
import sqlite3
import tempfile
from pathlib import Path

from lxml import etree
from rdflib import Dataset, Graph, Literal, RDF, URIRef, XSD
from rdflib.compare import isomorphic

from .audit import VerificationError, canonical
from .corpus import atomic_json, sha256
from .fda import FdaIndex
from .graph import K, P, check, iri, write
from .parser import article, book, text
from .store import now
from .verification import ontology_check


def candidates_for(record, texts, index, context):
    result = {}
    mentions = [dict(d) for d in record['detections'] if d['scheme'] in ('510k', 'pma', 'product-code')]
    mentions += [dict(d, method='structured-identifier') for d in record['identifiers'] if d['valid'] and d['scheme'] in ('510k', 'pma', 'product-code')]
    for location, value in texts:
        for alias, resource in index.name_matches(value):
            mentions.append(dict(scheme='device-name', value=alias, location=location,
                                 method='fda-catalog-token-name-exact', resource=resource))
    for evidence in mentions:
        targets = [evidence['resource']] if 'resource' in evidence else index.lookup(evidence['scheme'], evidence['value'])
        for target in targets:
            devices = index.devices(target) if evidence['scheme'] != 'product-code' else []
            for device in devices or [None]:
                candidate = dict(context, target=target, device=device,
                                 category=evidence['scheme'] == 'product-code' or index.has_type(target, 'ProductCode') or index.has_type(target, 'ProductClassificationRecord'),
                                 evidence={k: evidence[k] for k in ['scheme', 'value', 'location', 'method']})
                key = hashlib.sha256(canonical(candidate).encode()).hexdigest()
                candidate['id'] = str(iri('device-link', key))
                candidate['review'] = None
                result[key] = candidate
                if len(result) > 10000:
                    raise VerificationError('ambiguous FDA catalog produced too many candidates; refine catalog inputs')
    return sorted(result.values(), key=lambda item: item['id'])


def article_link_graph(item):
    g = Graph()
    article_uri = iri('article', item['pmid'])
    g.add((article_uri, K.linkingStatus, Literal('complete')))
    g.add((article_uri, K.catalogIdentity, Literal(item['catalog'])))
    source = iri('source', item['source'])
    g.add((source, RDF.type, K.Source))
    g.add((source, K.filename, Literal(item['source'])))
    g.add((source, K.sha256, Literal(item['source_sha256'])))
    catalog = iri('fda-catalog', item['catalog'])
    g.add((catalog, RDF.type, P.Entity))
    g.add((catalog, K.catalogIdentity, Literal(item['catalog'])))
    for candidate in item['candidates']:
        node, evidence = URIRef(candidate['id']), candidate['evidence']
        g.add((article_uri, K.link, node))
        g.add((node, RDF.type, K.LinkAssertion))
        g.add((node, RDF.type, K.DeviceLinkAssertion))
        g.add((node, K.matchedResource, URIRef(candidate['target'])))
        if candidate['category']:
            g.add((node, K.categoryMatch, URIRef(candidate['target'])))
        elif candidate['device']:
            g.add((node, K.device, URIRef(candidate['device'])))
            if candidate['target'] != candidate['device']:
                g.add((node, K.fdaRecord, URIRef(candidate['target'])))
        else:
            g.add((node, K.fdaRecord, URIRef(candidate['target'])))
        review = candidate['review']
        status = review['status'] if review else 'candidate'
        confidence = 'identifier-confirmed-by-review' if status == 'verified' else (
            'category-association-review-required' if candidate['category'] else
            'name-only-review-required' if evidence['scheme'] == 'device-name' else 'identifier-match-review-required')
        for predicate, value in [('status', status), ('confidence', confidence), ('scheme', evidence['scheme']),
                                 ('method', 'fda-catalog-token-name-exact' if evidence['scheme'] == 'device-name' else 'fda-index-exact-identifier'), ('evidenceLocation', evidence['location']),
                                 ('articleRevision', item['source_order']), ('catalogIdentity', item['catalog']),
                                 ('articlePayloadSHA256', item['article_sha256'])]:
            g.add((node, K[predicate], Literal(value)))
        detection = iri('link-evidence', candidate['id'])
        g.add((node, K.detection, detection))
        g.add((detection, RDF.type, K.IdentifierDetection))
        for key, value in evidence.items():
            g.add((detection, K[key], Literal(value)))
        g.add((node, P.wasDerivedFrom, source))
        g.add((node, P.wasDerivedFrom, catalog))
        if review:
            g.add((node, K.reviewer, Literal(review['reviewer'])))
            g.add((node, K.reviewedAt, Literal(review['reviewed_at'], datatype=XSD.dateTime)))
            g.add((node, K.reviewMethod, Literal(review['evidence_method'])))
    return g


def verify_link_artifact(folder, manifest):
    if {p.name for p in folder.iterdir() if p.name != 'manifest.json'} != set(manifest['files']):
        raise VerificationError('unexpected device-link artifact file')
    for filename, digest in manifest['files'].items():
        if sha256(folder / filename) != digest:
            raise VerificationError('device-link artifact checksum mismatch')
    actual = Graph().parse(folder / 'links.ttl', format='turtle')
    dataset = Dataset()
    dataset.parse(folder / 'links.nq', format='nquads')
    expected, graph_ids = Graph(), set()
    for item in manifest['articles']:
        graph_id = iri('article-links', item['pmid'])
        graph_ids.add(graph_id)
        g = article_link_graph(item)
        if not isomorphic(g, dataset.graph(graph_id)):
            raise VerificationError('device-link graph ownership or content mismatch')
        expected += g
    if {context for _, _, _, context in dataset.quads()} != graph_ids or not isomorphic(expected, actual):
        raise VerificationError('device-link JSON/RDF mismatch')
    if manifest['clear_graphs'] != sorted(str(node) for node in graph_ids):
        raise VerificationError('device-link clear manifest mismatch')
    check(actual)
    ontology_check(actual)


def commit_links(store, items):
    if not items:
        return
    try:
        store.db.execute('BEGIN')
        revision = store.db.execute('SELECT COALESCE(MAX(id),0)+1 FROM corpus_link_batches').fetchone()[0]
        folder = store.root / 'link-deltas' / f'{revision:09}'
        folder.mkdir(parents=True, exist_ok=True)
        union = Graph()
        with (folder / 'links.nq').open('w') as stream:
            for item in items:
                g = article_link_graph(item)
                union += g
                graph_name = iri('article-links', item['pmid']).n3()
                for line in sorted(g.serialize(format='nt').splitlines()):
                    stream.write(line.rsplit('.', 1)[0].rstrip() + ' ' + graph_name + ' .\n')
        write(union, folder / 'links.ttl')
        manifest = dict(format='pubmed-device-links-v1', revision=revision, created_at=now(), articles=items,
                        clear_graphs=sorted(str(iri('article-links', item['pmid'])) for item in items),
                        files={name: sha256(folder / name) for name in ['links.ttl', 'links.nq']})
        atomic_json(folder / 'manifest.json', manifest)
        verify_link_artifact(folder, manifest)
        for item in items:
            pmid = item['pmid']
            store.db.execute('DELETE FROM link_candidates WHERE pmid=?', (pmid,))
            for candidate in item['candidates']:
                store.db.execute('INSERT INTO link_candidates VALUES(?,?,?)', (candidate['id'], pmid, canonical(candidate)))
            store.db.execute('INSERT OR REPLACE INTO link_status VALUES(?,?,?)', (pmid, item['source_order'], item['catalog']))
            store.db.execute('INSERT OR REPLACE INTO corpus_link_heads VALUES(?,?)', (pmid, revision))
        store.db.execute('INSERT INTO corpus_link_batches VALUES(?,?,?)',
                         (revision, str(folder.relative_to(store.root)), sha256(folder / 'manifest.json')))
        store.db.execute("INSERT INTO corpus_changes(kind,reference) VALUES('links',?)", (revision,))
        store.db.commit()
    except Exception:
        store.db.rollback()
        raise


def link_corpus(store, index, max_sources=1, source_paths=None, batch_size=25):
    store.check_contract()
    if max_sources < 1 or not 1 <= batch_size <= 100:
        raise ValueError('positive source limit and link batch size 1..100 required')
    source_paths = source_paths or {}
    vocabulary_path = store.root / 'device-mesh.json'
    vocabulary = json.loads(vocabulary_path.read_text())['descriptors'] if vocabulary_path.exists() else None
    sources = store.db.execute('''SELECT s.name,s.digest,s.ord FROM sources s
        LEFT JOIN link_source_status l ON s.name=l.name
        WHERE s.status='exported' AND (l.name IS NULL OR l.ord!=s.ord OR l.catalog!=?)
        AND EXISTS(SELECT 1 FROM articles a WHERE a.ord=s.ord) ORDER BY s.ord LIMIT ?''',
        (index.identity, max_sources)).fetchall()
    processed = candidates = 0
    for name, digest, order in sources:
        path = Path(source_paths.get(name, store.root / 'raw-private' / name))
        if not path.exists() or sha256(path) != digest:
            raise VerificationError('matching private raw source needed for abstract/name evidence: ' + name)
        store.db.execute('DELETE FROM link_pending')
        store.db.commit()
        opener = gzip.open if path.suffix == '.gz' else open
        with opener(path, 'rb') as stream:
            for _, element in etree.iterparse(stream, events=('end',), tag=('PubmedArticle', 'PubmedBookArticle'), resolve_entities=False, no_network=True):
                pmid = element.findtext('MedlineCitation/PMID') or element.findtext('BookDocument/PMID')
                current = store.db.execute('SELECT payload FROM articles WHERE pmid=? AND ord=?', (pmid, order)).fetchone()
                if current:
                    record = book(element, vocabulary) if element.tag == 'PubmedBookArticle' else article(element, vocabulary)
                    # Match the final current version when a source repeats a PMID.
                    if canonical(record) == canonical(json.loads(current[0])):
                        context = dict(pmid=pmid, source_order=order, article_sha256=hashlib.sha256(current[0].encode()).hexdigest(), catalog=index.identity)
                        texts = [('title', record['title'])] + [('abstract', text(node)) for node in element.findall('.//AbstractText')]
                        values = candidates_for(record, texts, index, context)
                        for value in values:
                            prior = store.db.execute('SELECT payload FROM link_candidates WHERE id=?', (value['id'],)).fetchone()
                            if prior:
                                value['review'] = json.loads(prior[0])['review']
                        item = dict(context, source=name, source_sha256=digest, candidates=values)
                        store.db.execute('INSERT OR REPLACE INTO link_pending VALUES(?,?)', (pmid, canonical(item)))
                element.clear()
                while element.getprevious() is not None:
                    del element.getparent()[0]
        store.db.commit()
        expected = store.db.execute('SELECT COUNT(*) FROM articles WHERE ord=?', (order,)).fetchone()[0]
        if store.db.execute('SELECT COUNT(*) FROM link_pending').fetchone()[0] != expected:
            raise VerificationError('current source records could not be reconciled for device linking')
        cursor = store.db.execute('SELECT payload FROM link_pending ORDER BY pmid')
        while rows := cursor.fetchmany(batch_size):
            items = [json.loads(row[0]) for row in rows]
            commit_links(store, items)
            processed += len(items)
            candidates += sum(len(item['candidates']) for item in items)
        store.db.execute('INSERT OR REPLACE INTO link_source_status VALUES(?,?,?)', (name, order, index.identity))
        store.db.commit()
    result = dict(sources_scanned=len(sources), articles_linked=processed, candidates=candidates,
                  catalog=index.identity, automatic_verified_links=0)
    atomic_json(store.root / 'device-link-report.json', result)
    return result


def review_links(store, index, decisions):
    items = {}
    if len({decision['candidate_id'] for decision in decisions}) != len(decisions):
        raise VerificationError('duplicate review candidate')
    for decision in decisions:
        if set(decision) != {'candidate_id', 'status', 'reviewer', 'reviewed_at', 'evidence_method'}:
            raise VerificationError('review schema requires exact non-textual evidence fields')
        if decision['status'] not in ('verified', 'rejected') or not decision['reviewer'].strip():
            raise VerificationError('review status/reviewer invalid')
        date = datetime.datetime.fromisoformat(decision['reviewed_at'].replace('Z', '+00:00'))
        if date.tzinfo is None:
            raise VerificationError('review timestamp requires timezone')
        row = store.db.execute('SELECT pmid,payload FROM link_candidates WHERE id=?', (decision['candidate_id'],)).fetchone()
        if not row:
            raise VerificationError('review candidate missing or obsolete')
        pmid, payload = row
        candidate = json.loads(payload)
        current = store.db.execute('SELECT payload,ord FROM articles WHERE pmid=?', (pmid,)).fetchone()
        if not current or candidate['source_order'] != current[1] or candidate['article_sha256'] != hashlib.sha256(current[0].encode()).hexdigest() or candidate['catalog'] != index.identity:
            raise VerificationError('review does not match current article/catalog revision')
        if decision['status'] == 'verified':
            evidence = candidate['evidence']
            if evidence['scheme'] not in ('510k', 'pma') or candidate['target'] not in set(index.lookup(evidence['scheme'], evidence['value'])) or decision['evidence_method'] != 'independent-identifier-confirmation':
                raise VerificationError('name/category alone cannot verify identity; confirm an exact FDA submission identifier')
        if pmid not in items:
            head = store.db.execute('SELECT revision FROM corpus_link_heads WHERE pmid=?', (pmid,)).fetchone()[0]
            folder = store.root / store.db.execute('SELECT folder FROM corpus_link_batches WHERE id=?', (head,)).fetchone()[0]
            manifest = json.loads((folder / 'manifest.json').read_text())
            items[pmid] = next(item for item in manifest['articles'] if item['pmid'] == pmid)
        target = next(c for c in items[pmid]['candidates'] if c['id'] == decision['candidate_id'])
        target['review'] = {key: value for key, value in decision.items() if key != 'candidate_id'}
    values = list(items.values())
    for offset in range(0, len(values), 25):
        commit_links(store, values[offset:offset + 25])
    return dict(reviewed=len(decisions), articles=len(items))


def verify_links(store):
    # Replay only current revision/head identifiers in a scratch SQLite table.
    with tempfile.TemporaryDirectory(prefix='device-link-verify-', dir=store.root) as root:
        expected = sqlite3.connect(str(Path(root) / 'heads.sqlite'))
        try:
            expected.execute('CREATE TABLE heads(pmid TEXT PRIMARY KEY,revision INTEGER)')
            return _verify_links(store, expected)
        finally:
            expected.close()


def _verify_links(store, expected):
    revision = 0
    for kind, reference in store.db.execute('SELECT kind,reference FROM corpus_changes ORDER BY seq'):
        if kind == 'metadata':
            folder = store.root / store.db.execute('SELECT folder FROM corpus_batches WHERE ord=?', (reference,)).fetchone()[0]
            with (folder / 'replace.jsonl').open() as stream:
                for line in stream:
                    expected.execute('DELETE FROM heads WHERE pmid=?', (json.loads(line)['pmid'],))
        elif kind == 'links':
            revision += 1
            entry = store.db.execute('SELECT folder,manifest_sha256 FROM corpus_link_batches WHERE id=?', (reference,)).fetchone()
            if reference != revision or not entry:
                raise VerificationError('device-link changefeed sequence mismatch')
            folder = store.root / entry[0]
            if sha256(folder / 'manifest.json') != entry[1]:
                raise VerificationError('device-link manifest checksum mismatch')
            manifest = json.loads((folder / 'manifest.json').read_text())
            verify_link_artifact(folder, manifest)
            for item in manifest['articles']:
                expected.execute('INSERT OR REPLACE INTO heads VALUES(?,?)', (item['pmid'], reference))
    from itertools import zip_longest
    if revision != store.db.execute('SELECT COUNT(*) FROM corpus_link_batches').fetchone()[0]:
        raise VerificationError('device-link batch absent from feed')
    for a, b in zip_longest(expected.execute('SELECT * FROM heads ORDER BY pmid'), store.db.execute('SELECT * FROM corpus_link_heads ORDER BY pmid')):
        if a != b:
            raise VerificationError('device-link heads disagree with replay')
    for table in ['link_candidates', 'link_status']:
        if store.db.execute(f'SELECT 1 FROM {table} l LEFT JOIN corpus_link_heads h ON l.pmid=h.pmid WHERE h.pmid IS NULL LIMIT 1').fetchone():
            raise VerificationError('orphan device-link ledger row')
    for head, in store.db.execute('SELECT DISTINCT revision FROM corpus_link_heads ORDER BY revision'):
        folder = store.root / store.db.execute('SELECT folder FROM corpus_link_batches WHERE id=?', (head,)).fetchone()[0]
        for item in json.loads((folder / 'manifest.json').read_text())['articles']:
            pmid = item['pmid']
            if store.db.execute('SELECT revision FROM corpus_link_heads WHERE pmid=?', (pmid,)).fetchone() != (head,):
                continue
            current = store.db.execute('SELECT payload,ord FROM articles WHERE pmid=?', (pmid,)).fetchone()
            if not current or current[1] != item['source_order'] or hashlib.sha256(current[0].encode()).hexdigest() != item['article_sha256']:
                raise VerificationError('stale device links retained for replaced/deleted article')
            if store.db.execute('SELECT ord,catalog FROM link_status WHERE pmid=?',(pmid,)).fetchone() != (item['source_order'],item['catalog']):
                raise VerificationError('device-link completion ledger differs from export')
            observed = [json.loads(row[0]) for row in store.db.execute('SELECT payload FROM link_candidates WHERE pmid=? ORDER BY id', (pmid,))]
            if observed != item['candidates']:
                raise VerificationError('device-link candidate ledger differs from export')
    return dict(status='verified', batches=revision)
