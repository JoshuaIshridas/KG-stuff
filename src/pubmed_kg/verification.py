"""Reproducible unattended acceptance and benchmark reporting."""
import hashlib
import json
import math
import sqlite3
import tempfile
from pathlib import Path
from rdflib import Graph,RDF
from .audit import VerificationError,json_inventory,reconcile,verify_rdf
from .graph import K,ROOT,check,build
from .model import extraction_fingerprint,mapping_fingerprint

def ontology_check(g):
    from rdflib import OWL,RDFS
    ontology=Graph().parse(ROOT/'ontology.ttl')
    declared={s for s in ontology.subjects(RDF.type,None)}
    missing={str(p) for _,p,_ in g if str(p).startswith(str(K)) and p not in declared}
    missing.update(str(c) for c in g.objects(None,RDF.type) if str(c).startswith(str(K)) and c not in declared)
    if missing:raise VerificationError('undeclared ontology terms: '+','.join(sorted(missing)))
    return {'declared_terms':len(declared),'undeclared_used_terms':0}

def verify_state(store):
    """Replay immutable JSON events into a scratch ledger; reconcile final RDF."""
    with tempfile.TemporaryDirectory(prefix='verify-',dir=store.root) as scratch:
        db=sqlite3.connect(str(Path(scratch)/'expected.sqlite'))
        try:
            db.executescript('CREATE TABLE expected(pmid TEXT PRIMARY KEY,payload TEXT,source TEXT,ord INTEGER); CREATE TABLE deleted(pmid TEXT PRIMARY KEY,ord INTEGER);CREATE TABLE seen(pmid TEXT PRIMARY KEY);')
            reports=[]
            for name,digest,order,status in store.db.execute('SELECT name,digest,ord,status FROM sources ORDER BY ord'):
                if status!='exported':raise VerificationError('unprocessed/failed source remains')
                folder=store.root/'audits'/digest
                completed=json.loads((folder/'03-rdf-verification.json').read_text())
                if completed.get('status')!='verified' or completed.get('implementation_sha256')!=extraction_fingerprint():raise VerificationError('source extraction contract changed or is unverified; rebuild state')
                source=json.loads((folder/'01-source-count.json').read_text())
                actual=json_inventory(folder/'records.jsonl');reconcile(source,actual)
                reports.append(dict(name=name,counts=actual['counts'],record_event_retention=1.0,eligible_metadata_retention=1.0))
                with (folder/'records.jsonl').open() as f:
                    for line in f:
                        event=json.loads(line);pmid=event['pmid'];a=event['record']
                        db.execute('DELETE FROM expected WHERE pmid=?',(pmid,));db.execute('DELETE FROM deleted WHERE pmid=?',(pmid,))
                        if a is None:db.execute('INSERT INTO deleted VALUES(?,?)',(pmid,order))
                        else:db.execute('INSERT INTO expected VALUES(?,?,?,?)',(pmid,json.dumps(a),name,order))
            # Compare values as well as counts without materializing the corpus.
            expected=db.execute('SELECT * FROM expected ORDER BY pmid')
            observed=store.db.execute('SELECT * FROM articles ORDER BY pmid')
            from itertools import zip_longest
            for a,b in zip_longest(expected,observed):
                if a!=b:raise VerificationError('JSON replay disagrees with article ledger')
            for a,b in zip_longest(db.execute('SELECT * FROM deleted ORDER BY pmid'),store.db.execute('SELECT * FROM tombstones ORDER BY pmid')):
                if a!=b:raise VerificationError('deletion replay disagrees with ledger')
            folder=store.root/'snapshots'/(store.root/'CURRENT').read_text().strip()
            manifest=json.loads((folder/'manifest.json').read_text())
            if manifest.get('mapping_sha256')!=mapping_fingerprint():raise VerificationError('snapshot mapping contract changed; export and verify again')
            if manifest['sources']!=[list(row) for row in store.db.execute('SELECT name,digest,ord FROM sources ORDER BY ord')]:raise VerificationError('snapshot source inventory disagrees with ledger')
            actual_parts={p.name for p in folder.glob('part-*.ttl')}
            if actual_parts!=set(manifest['partitions']):raise VerificationError('missing or unexpected Turtle partition')
            partitions=[];triples=0
            for name,sha256 in sorted(manifest['partitions'].items()):
                path=folder/name
                if hashlib.sha256(path.read_bytes()).hexdigest()!=sha256:raise VerificationError('Turtle partition checksum mismatch')
                g=Graph().parse(path);check(g);ontology_check(g);rows=[];annotations=[]
                for u in g.subjects(RDF.type,K.Article):
                    pmid=str(g.value(u,K.pmid));row=store.db.execute('SELECT * FROM articles WHERE pmid=?',(pmid,)).fetchone()
                    if not row:raise VerificationError('RDF contains unknown/deleted article')
                    try:db.execute('INSERT INTO seen VALUES(?)',(pmid,))
                    except sqlite3.IntegrityError:raise VerificationError('duplicate article across RDF partitions') from None
                    rows.append(row);annotations.extend(store.db.execute('SELECT * FROM enrichment WHERE pmid=?',(pmid,)))
                partitions.append(verify_rdf(g,rows,annotations));triples+=len(g)
            count=db.execute('SELECT COUNT(*) FROM expected').fetchone()[0]
            if db.execute('SELECT COUNT(*) FROM seen').fetchone()[0]!=count or manifest['articles']!=count:raise VerificationError('final RDF coverage count mismatch')
            deletion_manifest=json.loads((folder/'deletions.json').read_text())
            if deletion_manifest!=[r[0] for r in db.execute('SELECT pmid FROM deleted ORDER BY pmid')]:raise VerificationError('deletion manifest mismatch')
            report={'status':'verified','workflow':['verify-source-count','write-json','verify-json','write-turtle','verify-turtle'],'sources':reports,'final_articles':count,'deleted_pmids':len(deletion_manifest),'serialized_partition_triples':triples,'partitions':partitions,'record_event_retention':1.0,'eligible_metadata_retention':1.0,'device_recall':'not_measured','all_pubmed_coverage':'not_measured','limitations':['Retention covers supplied sources and declared safe metadata, not all facts in article text.','A full PubMed universe requires a reconciled complete baseline and all applicable updates.','Device recall requires an independently labeled representative benchmark.']}
            (store.root/'verification-report.json').write_text(json.dumps(report,indent=2))
            return report
        finally:db.close()

def evaluate(store,path,threshold=0.99):
    if not 0.99<=threshold<=1.0:raise VerificationError('near-100-percent recall threshold must be at least 0.99')
    benchmark=json.loads(Path(path).read_text());records=benchmark['records'];seen=set();tp=fn=fp=tn=0
    for label in records:
        pmid=str(label['pmid'])
        if pmid in seen or type(label['is_device']) is not bool:raise VerificationError('invalid benchmark label')
        seen.add(pmid);row=store.db.execute('SELECT payload FROM articles WHERE pmid=?',(pmid,)).fetchone()
        if not row:raise VerificationError('benchmark PMID absent from corpus')
        positive=json.loads(row[0])['device_relevance']['status']=='candidate';actual=label['is_device']
        if positive and actual:tp+=1
        elif actual:fn+=1
        elif positive:fp+=1
        else:tn+=1
    n=tp+fn;recall=tp/n if n else None;precision=tp/(tp+fp) if tp+fp else None
    z=1.96
    lower=((recall+z*z/(2*n)-z*math.sqrt(recall*(1-recall)/n+z*z/(4*n*n)))/(1+z*z/n)) if n else None
    eligible=bool(benchmark.get('representative') and benchmark.get('labeling_method') and benchmark.get('population'))
    accepted=eligible and n>0 and recall>=threshold and lower>=threshold
    report=dict(status='passed' if accepted else 'not_established',threshold=threshold,tp=tp,fn=fn,fp=fp,tn=tn,device_screening_recall=recall,precision=precision,recall_95_percent_lower_bound=lower,representativeness_operator_attested=eligible,labeling_method=benchmark.get('labeling_method'),population=benchmark.get('population'),near_100_percent_device_recall_established=accepted,note='A synthetic or unrepresentative set cannot substantiate population coverage. A benchmark result does not establish extraction of all facts or verified device identity.')
    (store.root/'device-benchmark-report.json').write_text(json.dumps(report,indent=2));return report

def inventory_check(store,manifest,baseline,updates):
    """Reconcile against independently discovered published files, not an attestation flag."""
    declared={s['name'] for s in manifest['sources']}
    required={name for name,_ in baseline}|{name for name,_ in updates}
    missing=sorted(required-declared);unexpected=sorted(declared-required)
    pending=[name for name in required if store.db.execute('SELECT status FROM sources WHERE name=?',(name,)).fetchone()!=('exported',)]
    report={'status':'verified' if required and not missing and not unexpected and not pending else 'failed','baseline_files':len(baseline),'update_files':len(updates),'manifest_files':len(declared),'missing_files':missing,'unexpected_files':unexpected,'not_exported':sorted(pending),'published_file_inventory_coverage':len(required & declared)/len(required) if required else None,'scope':'Published official baseline and update directory listings observed for this run; not real-time PubMed facts or device screening recall.'}
    (store.root/'inventory-verification.json').write_text(json.dumps(report,indent=2))
    if report['status']!='verified':raise VerificationError('complete published file inventory not established')
    return report
