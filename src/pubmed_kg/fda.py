"""Streaming, disk-backed projection of FDA RDF using the supplied ontology."""
import gzip
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import fcntl

import pyoxigraph as ox
from rdflib import Graph, Literal, Namespace, RDF, RDFS, URIRef
from rdflib.util import from_n3

from .audit import VerificationError, canonical
from .corpus import atomic_json, sha256
from .graph import ROOT
from .parser import norm
from .store import now

MD = Namespace('http://medicaldevice.com/ontology/')
NAMES = ['deviceName', 'brandName', 'tradeName', 'deviceBrandName', 'proprietaryName']
DEVICE_RECORD = ['has510kRecord', 'hasPMARecord', 'hasFDARecord', 'hasAccessGUDIDRecord', 'hasTPLCRecord', 'hasDeNovoRecord']
EDGES = DEVICE_RECORD + ['linkedMedicalDevice', 'involvesDevice', 'hasProductCode', 'linkedProductCode',
                       'relatedProductCode', 'hasRecallRecord', 'hasRecall', 'hasClassificationRecord']
KEYS = {'510k': 'k510Number', 'pma': 'pmaNumber', 'product-code': 'productCode'}
FIELDS = NAMES + EDGES + list(KEYS.values()) + ['recallNumber', 'recallClass', 'recallClassification',
        'recallDate', 'recallInitiationDate', 'deviceClass', 'sourceDataset', 'sourceUrl']
PREDICATES = {str(MD[name]) for name in FIELDS} | {str(RDF.type), str(RDFS.label)}


def schema_contract(path):
    g = Graph().parse(path, format='turtle')
    required = {str(MD[name]) for name in FIELDS} | {str(MD[name]) for name in ['MedicalDevice', 'K510Record', 'PMARecord', 'ProductCode', 'RecallRecord', 'TPLCRecord']}
    missing = required - {str(s) for s in g.subjects()}
    if missing:
        raise VerificationError('required FDA ontology terms missing: ' + ', '.join(sorted(missing)))
    return dict(sha256=sha256(path), triples=len(g), namespace=str(MD),
                classes=len(set(g.subjects(RDF.type, RDFS.Class))),
                properties=len(set(g.subjects(RDF.type, RDF.Property))),
                trial_predicate_declared=False)


def parse_rdf(path, format=None):
    path = Path(path)
    suffix = path.with_suffix('').suffix if path.suffix == '.gz' else path.suffix
    formats = {'.ttl': ox.RdfFormat.TURTLE, '.nt': ox.RdfFormat.N_TRIPLES,
               '.nq': ox.RdfFormat.N_QUADS, '.trig': ox.RdfFormat.TRIG}
    rdf_format = formats.get(suffix) if format is None else formats['.' + format]
    if rdf_format is None:
        raise ValueError('FDA source format must be ttl, nt, nq or trig, optionally gzip')
    opener = gzip.open if path.suffix == '.gz' else open
    with opener(path, 'rb') as stream:
        # Oxigraph parses from a stream, unlike RDFLib's full-buffer Turtle parser.
        # Undeclared relative IRIs must not acquire machine-dependent file: IRIs.
        yield from ox.parse(stream, format=rdf_format)


def rdf_term(term):
    return from_n3(str(term))


def build_index(paths, target, ontology=ROOT / 'integration/fda/ontology.ttl', format=None):
    target = Path(target)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.with_suffix(target.suffix + '.lock').open('w') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise RuntimeError('FDA index build already running') from None
        try:
            return _build_index(paths, target, ontology, format)
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def _build_index(paths, target, ontology, format):
    paths, target = [Path(p) for p in paths], Path(target)
    if not paths:
        raise ValueError('at least one FDA RDF file required')
    schema = schema_contract(ontology)
    inputs = [dict(name=p.name, sha256=sha256(p), bytes=p.stat().st_size) for p in paths]
    implementation = hashlib.sha256(Path(__file__).read_bytes() + (ROOT / 'src/pubmed_kg/parser.py').read_bytes()).hexdigest()
    identity = hashlib.sha256(canonical(dict(schema=schema, sources=inputs, format=format, index_version=1, implementation=implementation)).encode()).hexdigest()
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.exists():
        current = FdaIndex(target)
        if current.manifest['identity'] != identity:
            raise VerificationError('FDA index is immutable; build a new index path for a changed release')
        return current.manifest
    temporary = target.with_suffix(target.suffix + '.building')
    db = sqlite3.connect(temporary)
    try:
        db.executescript('''
            CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY,value TEXT);
            CREATE TABLE IF NOT EXISTS files(ordinal INTEGER PRIMARY KEY,sha256 TEXT,parsed INTEGER,retained INTEGER);
            CREATE TABLE IF NOT EXISTS triples(s TEXT,p TEXT,o TEXT,PRIMARY KEY(s,p,o)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS triple_po ON triples(p,o,s);
            CREATE TABLE IF NOT EXISTS identifiers(scheme TEXT,value TEXT,resource TEXT,PRIMARY KEY(scheme,value,resource)) WITHOUT ROWID;
            CREATE TABLE IF NOT EXISTS aliases(alias TEXT,first_token TEXT,resource TEXT,PRIMARY KEY(alias,resource)) WITHOUT ROWID;
            CREATE INDEX IF NOT EXISTS alias_token ON aliases(first_token,alias,resource);
        ''')
        prior = db.execute("SELECT value FROM meta WHERE key='identity'").fetchone()
        if prior and prior[0] != identity:
            raise VerificationError('unfinished FDA index has different inputs; select a new output path')
        db.execute("INSERT OR IGNORE INTO meta VALUES('identity',?)", (identity,))
        db.commit()
        for ordinal, path in enumerate(paths):
            if db.execute('SELECT sha256 FROM files WHERE ordinal=?', (ordinal,)).fetchone() == (inputs[ordinal]['sha256'],):
                continue
            parsed = retained = 0
            db.execute('BEGIN')
            for quad in parse_rdf(path, format):
                parsed += 1
                predicate = quad.predicate.value
                if predicate not in PREDICATES or not isinstance(quad.subject, ox.NamedNode):
                    continue
                subject, obj = quad.subject.value, rdf_term(quad.object)
                if not isinstance(obj, (URIRef, Literal)):
                    continue
                db.execute('INSERT OR IGNORE INTO triples VALUES(?,?,?)', (subject, predicate, obj.n3()))
                retained += 1
                for scheme, name in KEYS.items():
                    if predicate == str(MD[name]) and isinstance(obj, Literal):
                        value = norm(scheme, str(obj)) if scheme != 'product-code' else str(obj).strip().upper()
                        if value and (scheme != 'product-code' or re.fullmatch(r'[A-Z]{3}', value)):
                            db.execute('INSERT OR IGNORE INTO identifiers VALUES(?,?,?)', (scheme, value, subject))
                if predicate in {str(MD[n]) for n in NAMES} and isinstance(obj, Literal):
                    alias = ' '.join(re.findall(r'\w+', str(obj).casefold()))
                    if len(alias) >= 8 and len(alias.split()) >= 2:
                        db.execute('INSERT OR IGNORE INTO aliases VALUES(?,?,?)', (alias, alias.split()[0], subject))
            db.execute('INSERT INTO files VALUES(?,?,?,?)', (ordinal, inputs[ordinal]['sha256'], parsed, retained))
            db.commit()
        manifest = dict(identity=identity, implementation_sha256=implementation, completed_at=now(), ontology=schema, sources=inputs,
                        parsed_triples=db.execute('SELECT SUM(parsed) FROM files').fetchone()[0],
                        retained_triples=db.execute('SELECT COUNT(*) FROM triples').fetchone()[0],
                        aliases=db.execute('SELECT COUNT(*) FROM aliases').fetchone()[0],
                        scope='FDA identifier/name/device-record/category/recall projection; excludes narrative/free-text fields.')
        db.execute("INSERT OR REPLACE INTO meta VALUES('manifest',?)", (canonical(manifest),))
        db.commit()
    finally:
        db.close()
    os.replace(temporary, target)
    atomic_json(target.with_suffix(target.suffix + '.json'), manifest)
    return manifest


class FdaIndex:
    def __init__(self, path):
        self.path = Path(path)
        self.db = sqlite3.connect(self.path.resolve().as_uri() + '?mode=ro', uri=True)
        row = self.db.execute("SELECT value FROM meta WHERE key='manifest'").fetchone()
        if not row:
            raise VerificationError('FDA index has no completed manifest')
        self.manifest = json.loads(row[0])
        self.identity = self.manifest['identity']
        implementation = hashlib.sha256(Path(__file__).read_bytes() + (ROOT / 'src/pubmed_kg/parser.py').read_bytes()).hexdigest()
        if self.manifest.get('implementation_sha256') != implementation:
            raise VerificationError('FDA indexing contract changed; build a new index path')

    def lookup(self, scheme, value):
        rows = self.db.execute('SELECT resource FROM identifiers WHERE scheme=? AND value=? ORDER BY resource', (scheme, value))
        for row, in rows:
            if scheme == 'product-code' and not any(self.has_type(row, cls) for cls in ['ProductCode', 'ProductClassificationRecord']):
                continue
            if scheme == '510k' and not self.has_type(row, 'K510Record'):
                continue
            if scheme == 'pma' and not self.has_type(row, 'PMARecord'):
                continue
            yield row

    def has_type(self, resource, local):
        return self.db.execute('SELECT 1 FROM triples WHERE s=? AND p=? AND o=?',
                               (resource, str(RDF.type), MD[local].n3())).fetchone() is not None

    def devices(self, record):
        result = set()
        if self.has_type(record, 'MedicalDevice'):
            result.add(record)
        for predicate in DEVICE_RECORD:
            for resource, in self.db.execute('SELECT s FROM triples WHERE p=? AND o=?', (str(MD[predicate]), URIRef(record).n3())):
                # hasFDARecord has no MedicalDevice domain in the supplied schema.
                if predicate != 'hasFDARecord' or self.has_type(resource, 'MedicalDevice'):
                    result.add(resource)
        for predicate in ['linkedMedicalDevice', 'involvesDevice']:
            for value, in self.db.execute('SELECT o FROM triples WHERE s=? AND p=?', (record, str(MD[predicate]))):
                node = from_n3(value)
                if isinstance(node, URIRef):
                    result.add(str(node))
        return sorted(result)

    def name_matches(self, value):
        words = re.findall(r'\w+', value.casefold())
        normalized = ' ' + ' '.join(words) + ' '
        seen = set()
        unique = sorted(set(words))
        for offset in range(0, len(unique), 400):
            batch = unique[offset:offset + 400]
            marks = ','.join('?' for _ in batch)
            for alias, resource in self.db.execute('SELECT alias,resource FROM aliases WHERE first_token IN (' + marks + ')', batch):
                if ' ' + alias + ' ' in normalized and (alias, resource) not in seen:
                    seen.add((alias, resource))
                    yield alias, resource

    def triples(self):
        for s, p, o in self.db.execute('SELECT s,p,o FROM triples ORDER BY s,p,o'):
            yield URIRef(s), URIRef(p), from_n3(o)
