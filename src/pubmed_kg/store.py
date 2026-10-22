import contextlib
import datetime
import fcntl
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import uuid
from rdflib import Graph
from .audit import prepare, verify_rdf, json_inventory, reconcile, VerificationError
from .model import extraction_fingerprint,mapping_fingerprint
from .graph import build, check, write, K, P, iri
from rdflib import Literal, RDF

def now():return datetime.datetime.now(datetime.timezone.utc).isoformat()
class Store:
    def __init__(self,root):
        self.root=Path(root);self.root.mkdir(parents=True,exist_ok=True)
        self.db=sqlite3.connect(self.root/'ledger.sqlite');self.db.executescript('''
        CREATE TABLE IF NOT EXISTS sources(name TEXT PRIMARY KEY, digest TEXT, ord INTEGER UNIQUE, status TEXT, completed TEXT);
        CREATE TABLE IF NOT EXISTS articles(pmid TEXT PRIMARY KEY, payload TEXT, source TEXT, ord INTEGER);
        CREATE TABLE IF NOT EXISTS tombstones(pmid TEXT PRIMARY KEY, ord INTEGER);
        CREATE TABLE IF NOT EXISTS enrichment(pmid TEXT PRIMARY KEY, payload TEXT, status TEXT, retrieved TEXT);
        ''');self.db.commit()
    @contextlib.contextmanager
    def lock(self):
        with (self.root/'run.lock').open('w') as f:
            try:fcntl.flock(f,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError:raise RuntimeError('another run holds lock') from None
            try:yield
            finally:fcntl.flock(f,fcntl.LOCK_UN)
    def ingest(self,path,order,expected_pmids=None,expected_count=None):
        path=Path(path);name=path.name;h=hashlib.sha256()
        with path.open('rb') as f:
            for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
        digest=h.hexdigest();old=self.db.execute('SELECT digest,ord,status FROM sources WHERE name=?',(name,)).fetchone()
        if old:
            if old[:2]!=(digest,order):raise ValueError('source identity changed')
            if old[2]=='exported':
                audit_path=self.root/'audits'/digest/'03-rdf-verification.json'
                if not audit_path.exists():raise VerificationError('legacy source lacks mandatory verification; rebuild in a fresh state directory')
                if json.loads(audit_path.read_text()).get('implementation_sha256')!=extraction_fingerprint():raise VerificationError('extraction contract changed; rebuild in a fresh state directory')
                return False
        other=self.db.execute('SELECT name FROM sources WHERE ord=?',(order,)).fetchone()
        if other and other[0]!=name:raise ValueError('source order collision')
        self.db.execute('INSERT OR REPLACE INTO sources VALUES(?,?,?,?,NULL)',(name,digest,order,'downloaded'));self.db.commit()
        try:
            vocabulary_path=self.root/'device-mesh.json'
            vocabulary=json.loads(vocabulary_path.read_text())['descriptors'] if vocabulary_path.exists() else None
            audit_dir=self.root/'audits'/digest
            json_path,report=prepare(path,audit_dir,vocabulary,expected_pmids,expected_count)
            self.db.execute('BEGIN')
            outcomes={'applied_upserts':0,'applied_deletes':0,'ignored_older_events':0}
            def events():
                with json_path.open() as f:
                    for line in f:
                        item=json.loads(line);yield item['pmid'],item['record']
            for pmid,a in events():
                prior=self.db.execute('SELECT max(ord) FROM (SELECT ord FROM articles WHERE pmid=? UNION ALL SELECT ord FROM tombstones WHERE pmid=?)',(pmid,pmid)).fetchone()[0]
                if prior is not None and prior>order:
                    outcomes['ignored_older_events']+=1;continue
                self.db.execute('DELETE FROM articles WHERE pmid=?',(pmid,));self.db.execute('DELETE FROM enrichment WHERE pmid=?',(pmid,))
                if a is None:
                    self.db.execute('INSERT OR REPLACE INTO tombstones VALUES(?,?)',(pmid,order));outcomes['applied_deletes']+=1
                else:
                    self.db.execute('DELETE FROM tombstones WHERE pmid=?',(pmid,));self.db.execute('INSERT INTO articles VALUES(?,?,?,?)',(pmid,json.dumps(a),name,order));outcomes['applied_upserts']+=1
            self.db.execute('UPDATE sources SET status=? WHERE name=?',('parsed',name))
            folder=self.publish(in_transaction=True)
            report.update(status='verified',workflow=['verify-source-count','write-json','verify-json','write-turtle','verify-turtle'],outcomes=outcomes,snapshot=folder.name)
            (audit_dir/'03-rdf-verification.json').write_text(json.dumps(report,indent=2))
            self.db.execute('UPDATE sources SET status=? WHERE name=?',('validated',name))
            self.db.execute('UPDATE sources SET status=?,completed=? WHERE name=?',('exported',now(),name));self.db.commit()
            return True
        except Exception:
            self.db.rollback();self.db.execute('UPDATE sources SET status=? WHERE name=?',('failed',name));self.db.commit();raise
    def publish(self,in_transaction=False):
        mapping_version=mapping_fingerprint()
        fingerprint=hashlib.sha256(mapping_version.encode())
        for query in ['SELECT name,digest,ord FROM sources ORDER BY ord','SELECT * FROM articles ORDER BY pmid','SELECT * FROM tombstones ORDER BY pmid','SELECT * FROM enrichment ORDER BY pmid']:
            for row in self.db.execute(query):fingerprint.update(json.dumps(row).encode())
        generation=fingerprint.hexdigest();folder=self.root/'snapshots'/generation;folder.mkdir(parents=True,exist_ok=True)
        articles=self.db.execute('SELECT * FROM articles ORDER BY pmid');count=0;part=0;batch=[];partition_reports=[]
        def flush(batch,part):
            ids=[r[0] for r in batch]; anns=[]
            for p in ids:anns.extend(self.db.execute('SELECT * FROM enrichment WHERE pmid=?',(p,)))
            if any(json.loads(row[1]).get('schema_version')!=2 for row in batch):raise VerificationError('legacy JSON schema; reingest into fresh state')
            g=build(batch,anns)
            run=iri('run',generation);g.add((run,RDF.type,P.Activity))
            for name,digest,order in self.db.execute('SELECT name,digest,ord FROM sources'):
                source=iri('source',name);g.add((source,K.sha256,Literal(digest)))
            for pmid,*_ in batch:g.add((iri('article',pmid),P.wasGeneratedBy,run))
            check(g);target=folder/f'part-{part:05}.ttl';write(g,target)
            reread=Graph().parse(target,format='turtle');check(reread)
            from rdflib.compare import isomorphic
            if not isomorphic(g,reread):raise VerificationError('serialized Turtle changed graph')
            partition_reports.append(verify_rdf(reread,batch,anns))
            from .verification import ontology_check
            ontology_check(reread)
        for row in articles:
            batch.append(row);count+=1
            if len(batch)==500:flush(batch,part);part+=1;batch=[]
        if batch or not count:flush(batch,part)
        (folder/'deletions.json').write_text(json.dumps([r[0] for r in self.db.execute('SELECT pmid FROM tombstones ORDER BY pmid')]))
        expected_articles=self.db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]
        if sum(p['articles'] for p in partition_reports)!=expected_articles:raise VerificationError('snapshot article count mismatch')
        verification=dict(status='verified',workflow=['verify-source-count','write-json','verify-json','write-turtle','verify-turtle'],partitions=partition_reports,article_count=expected_articles,tombstone_count=self.db.execute('SELECT COUNT(*) FROM tombstones').fetchone()[0],device_recall='not_measured',pubmed_universe_coverage='not_measured')
        (folder/'verification.json').write_text(json.dumps(verification,indent=2))
        partition_hashes={p.name:hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted(folder.glob('part-*.ttl'))}
        created=json.loads((folder/'manifest.json').read_text())['created'] if (folder/'manifest.json').exists() else now()
        manifest=dict(generation=generation,mapping_sha256=mapping_version,partitions=partition_hashes,articles=count,created=created,sources=list(self.db.execute('SELECT name,digest,ord FROM sources ORDER BY ord')),loading='replace entire dedicated PubMed graph; never append snapshots')
        (folder/'manifest.json').write_text(json.dumps(manifest,indent=2))
        # Publishing pointer is last. A crash before DB commit is repaired by replaying the source.
        pointer=self.root/'CURRENT.tmp';pointer.write_text(generation);os.replace(pointer,self.root/'CURRENT')
        return folder
    def graph(self):
        if self.db.execute("SELECT COUNT(*) FROM sources WHERE status!='exported'").fetchone()[0]:
            raise VerificationError('state has incomplete sources; resume before reading current graph')
        g=Graph();folder=self.root/'snapshots'/(self.root/'CURRENT').read_text().strip()
        manifest=json.loads((folder/'manifest.json').read_text())
        expected=[list(row) for row in self.db.execute('SELECT name,digest,ord FROM sources ORDER BY ord')]
        if manifest['sources']!=expected or manifest['articles']!=self.db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]:
            raise VerificationError('snapshot and committed ledger disagree; resume or export before reading')
        for p in sorted(folder.glob('*.ttl')):g.parse(p)
        return g
