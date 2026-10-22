"""Local disk-backed SPARQL store with staged, recoverable changefeed loading.

Only local files are written. A pending marker blocks reads across crashes until
the same batch is replayed; no loader checkpoint advances before the RDF swap.
"""
import contextlib
import fcntl
import gc
import hashlib
import json
import os
from pathlib import Path
import sqlite3

import pyoxigraph as ox

from .audit import VerificationError
from .corpus import sha256
from .fda import parse_rdf
from .graph import K, ROOT


class Materialized:
    def __init__(self, path):
        self.root = Path(path)
        self.root.mkdir(parents=True, exist_ok=True)
        self.lock_file = (self.root / 'load.lock').open('w')
        try:
            fcntl.flock(self.lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            self.lock_file.close()
            raise RuntimeError('local RDF store is busy') from None
        try:
            self.ledger = sqlite3.connect(self.root / 'loading.sqlite')
            self.ledger.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT)')
            self.ledger.commit()
            self.rdf = ox.Store(str(self.root / 'rdf'))
        except Exception:
            self.lock_file.close()
            raise

    def __enter__(self): return self

    def __exit__(self, *args):
        self.rdf.flush()
        self.rdf = None
        self.ledger.close()
        gc.collect()
        fcntl.flock(self.lock_file, fcntl.LOCK_UN)
        self.lock_file.close()

    def value(self, key, default=None):
        row = self.ledger.execute('SELECT value FROM meta WHERE key=?', (key,)).fetchone()
        return row[0] if row else default

    def set(self, key, value):
        self.ledger.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', (key, str(value)))
        self.ledger.commit()

    def ready(self):
        if self.value('pending'):
            raise VerificationError('RDF load incomplete; resume loading before querying')

    def swap(self, quads, clear_graphs, token, bulk=True):
        """Stage streamed data, then atomically replace named graphs via SPARQL."""
        self.set('pending', token)
        prefix = 'urn:pmkg:stage:' + hashlib.sha256(token.encode()).hexdigest() + ':'
        clears = set(clear_graphs)
        # Recover only this batch's possible stages, without enumerating the
        # corpus's millions of named graphs.
        stages = [ox.NamedNode(prefix + hashlib.sha256(name.encode()).hexdigest()) for name in sorted(clears)]
        if stages:
            self.rdf.update(';\n'.join('DROP SILENT GRAPH ' + str(graph) for graph in stages))
        mapping = {}
        count = 0
        def staging():
            nonlocal count
            for quad in quads:
                if not isinstance(quad.graph_name, ox.NamedNode):
                    raise VerificationError('materializer requires explicit named graphs')
                name = quad.graph_name.value
                if name not in clears:
                    raise VerificationError('upsert graph missing from replacement manifest')
                if name not in mapping:
                    mapping[name] = ox.NamedNode(prefix + hashlib.sha256(name.encode()).hexdigest())
                count += 1
                yield ox.Quad(quad.subject, quad.predicate, quad.object, mapping[name])
        if bulk:
            self.rdf.bulk_extend(staging())
        else:
            self.rdf.extend(staging())
        # Hash-verified producer N-Quads contain no duplicates within a graph.
        if mapping:
            values = ' '.join(str(graph) for graph in mapping.values())
            actual = int(next(iter(self.rdf.query('SELECT (COUNT(*) AS ?n) WHERE { VALUES ?g { ' + values + ' } GRAPH ?g { ?s ?p ?o } }')))['n'].value)
            if actual != count:
                raise VerificationError('staged RDF count mismatch')
        if not set(mapping) <= clears:
            raise VerificationError('upsert graph missing from replacement manifest')
        statements = ['DROP SILENT GRAPH ' + str(ox.NamedNode(name)) for name in sorted(clears - set(mapping))]
        statements += ['MOVE SILENT GRAPH ' + str(stage) + ' TO GRAPH ' + str(ox.NamedNode(name)) for name, stage in sorted(mapping.items())]
        if statements:
            # Oxigraph guarantees that an entire SPARQL Update succeeds or rolls back.
            self.rdf.update(';\n'.join(statements))
        self.rdf.flush()

    def load_context(self, quads, graph, identity):
        name = str(graph)
        if self.value('context:' + name) == identity:
            if self.value('pending') == 'context:' + name + ':' + identity:
                self.ledger.execute("DELETE FROM meta WHERE key='pending'")
                self.ledger.commit()
            return False
        self.swap((ox.Quad(q.subject, q.predicate, q.object, ox.NamedNode(name)) for q in quads), [name], 'context:' + name + ':' + identity)
        self.set('context:' + name, identity)
        self.ledger.execute("DELETE FROM meta WHERE key='pending'")
        self.ledger.commit()
        return True

    def load_fda(self, index):
        def projection():
            for s, p, o in index.triples():
                # Parse each RDF term through the strict native parser; graph contents
                # stay bounded and literal escaping is handled by RDF serializers.
                yield from ox.parse((s.n3() + ' ' + p.n3() + ' ' + o.n3() + ' .').encode(), format=ox.RdfFormat.N_TRIPLES)
        self.load_context(projection(), 'urn:pmkg:fda', index.identity)
        for name, path in [('fda', ROOT / 'integration/fda/ontology.ttl'), ('pubmed', ROOT / 'ontology.ttl')]:
            from rdflib import Graph
            canonical = Graph().parse(path).serialize(format='nt').encode()
            self.load_context(ox.parse(canonical, format=ox.RdfFormat.N_TRIPLES), 'urn:pmkg:ontology:' + name, sha256(path))

    def apply(self, corpus, fail_before_swap=False):
        instance = corpus.db.execute("SELECT value FROM corpus_meta WHERE key='instance'").fetchone()[0]
        if self.value('instance') not in (None, instance):
            raise VerificationError('RDF target belongs to another corpus state')
        self.set('instance', instance)
        checkpoint = int(self.value('checkpoint', '0'))
        loaded = 0
        for seq, kind, reference in corpus.db.execute('SELECT seq,kind,reference FROM corpus_changes WHERE seq>? ORDER BY seq', (checkpoint,)):
            table, key = {'metadata': ('corpus_batches', 'ord'), 'annotation': ('corpus_annotation_batches', 'id'), 'links': ('corpus_link_batches', 'id')}[kind]
            entry = corpus.db.execute(f'SELECT folder,manifest_sha256 FROM {table} WHERE {key}=?', (reference,)).fetchone()
            folder = corpus.root / entry[0]
            if sha256(folder / 'manifest.json') != entry[1]:
                raise VerificationError('loader manifest checksum mismatch')
            manifest = json.loads((folder / 'manifest.json').read_text())
            files = manifest['files']
            for filename, digest in files.items():
                if sha256(folder / filename) != digest:
                    raise VerificationError('loader source file checksum mismatch')
            if kind == 'metadata':
                clears = []
                with (folder / 'replace.jsonl').open() as stream:
                    for line in stream:
                        clears.extend(json.loads(line)['clear_graphs'])
            else:
                clears = manifest['clear_graphs']
            def quads():
                for filename in sorted(files):
                    if filename.endswith('.nq'):
                        yield from parse_rdf(folder / filename)
            if fail_before_swap:
                self.set('pending', f'feed:{instance}:{seq}')
                raise OSError('simulated interrupted load')
            self.swap(quads(), clears, f'feed:{instance}:{seq}', bulk=kind == 'metadata')
            # A crash between RDF swap and this checkpoint is replayed idempotently.
            self.ledger.execute('INSERT OR REPLACE INTO meta VALUES(?,?)', ('checkpoint', str(seq)))
            self.ledger.execute("DELETE FROM meta WHERE key='pending'")
            self.ledger.commit()
            loaded += 1
        self.ready()
        return dict(loaded_batches=loaded, checkpoint=int(self.value('checkpoint', '0')))

    def verify_current(self, corpus):
        self.require_current(corpus)
        expected = corpus.db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
        actual = int(next(iter(self.rdf.query('SELECT (COUNT(DISTINCT ?a) AS ?n) WHERE { ?a a <' + str(K.Article) + '> }', use_default_graph_as_union=True)))['n'].value)
        if actual != expected:
            raise VerificationError('materialized current article count mismatch')
        return dict(current_articles=actual, status='verified')

    def require_current(self, corpus):
        self.ready()
        instance = corpus.db.execute("SELECT value FROM corpus_meta WHERE key='instance'").fetchone()[0]
        if self.value('instance') != instance:
            raise VerificationError('RDF target belongs to a different corpus state')
        if corpus.db.execute("SELECT 1 FROM sources WHERE status!='exported' LIMIT 1").fetchone():
            raise VerificationError('source ingestion incomplete; resume before querying')
        latest = corpus.db.execute('SELECT COALESCE(MAX(seq),0) FROM corpus_changes').fetchone()[0]
        if int(self.value('checkpoint', '0')) != latest:
            raise VerificationError('local RDF store is behind the corpus; run materialize first')

    def require_catalog(self, index):
        if self.value('context:urn:pmkg:fda') != index.identity:
            raise VerificationError('RDF store has a different FDA catalog; materialize the configured index')

    def query(self, query):
        self.ready()
        return json.loads(self.rdf.query(query, use_default_graph_as_union=True).serialize(format=ox.QueryResultsFormat.JSON))

    def export(self, path):
        self.ready()
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(path.suffix + '.part')
        self.rdf.dump(output=str(temporary), format=ox.RdfFormat.N_QUADS)
        actual = sum(1 for _ in ox.parse(path=temporary, format=ox.RdfFormat.N_QUADS))
        if actual != len(self.rdf):
            raise VerificationError('serialized current dataset count mismatch')
        os.replace(temporary, path)
