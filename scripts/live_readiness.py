#!/usr/bin/env python3
"""Bounded live metadata test. Private XML cache is mandatory; never a full backfill."""
import argparse
import gzip
import hashlib
import json
import resource
import sys
import tempfile
import time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'src'))
from lxml import etree
from pubmed_kg.network import Client
from pubmed_kg.store import Store
from pubmed_kg.verification import verify_state

def read_json(path):return json.loads(path.read_text())

def execute():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--cache',type=Path,required=True,help='Private raw cache; may contain copyrighted abstracts')
    parser.add_argument('--output',type=Path,default=Path('reports/live-readiness'))
    parser.add_argument('--offline',action='store_true',help='Replay an existing cache without network requests')
    parser.add_argument('--require-full-corpus',action='store_true',help='Exit 2: this bounded test cannot certify full-corpus coverage')
    args=parser.parse_args();args.cache.mkdir(parents=True,exist_ok=True);args.output.mkdir(parents=True,exist_ok=True)
    c=Client(rate=2);strata=read_json(ROOT/'scripts/readiness_queries.json');selected=[];groups=[]
    def cached(url,params,path):
        if args.offline:return read_json(path)
        with c.get(url,params=params) as r:data=r.json()
        path.write_text(json.dumps(data));return data
    for name,query,limit in strata:
        data=cached('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esearch.fcgi',{'db':'pubmed','term':query,'retmode':'json','retmax':limit,'sort':'pub_date','tool':'pubmed-mdkg-readiness'},args.cache/(name+'-search.json'))
        result=data['esearchresult'];ids=result['idlist'];groups.append({'name':name,'query':query,'pmids':ids,'available_count':int(result['count'])})
        for pmid in ids:
            if pmid not in selected:selected.append(pmid)
    if not selected or len(selected)>400:raise ValueError('bounded test requires 1–400 records')
    for start in range(0,len(selected),150):
        path=args.cache/('fetch-'+str(start//150)+'.xml')
        if not args.offline:
            with c.get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',params={'db':'pubmed','id':','.join(selected[start:start+150]),'retmode':'xml','tool':'pubmed-mdkg-readiness'},stream=True) as r,path.with_suffix('.part').open('wb') as f:
                for block in r.iter_content(1024*1024):f.write(block)
            path.with_suffix('.part').replace(path)
    source=args.cache/'readiness-source.xml.gz';observed=[]
    with source.open('wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as f:
        f.write(b'<PubmedArticleSet>')
        for start in range(0,len(selected),150):
            batch=[]
            for _,e in etree.iterparse(str(args.cache/('fetch-'+str(start//150)+'.xml')),events=('end',),tag=('PubmedArticle','PubmedBookArticle'),resolve_entities=False,no_network=True):
                pmid=e.findtext('MedlineCitation/PMID') or e.findtext('BookDocument/PMID');observed.append(pmid);batch.append(pmid);f.write(etree.tostring(e));e.clear()
                while e.getprevious() is not None:del e.getparent()[0]
            if set(batch)!=set(selected[start:start+150]):raise ValueError('targeted retrieval PMID mismatch')
        f.write(b'</PubmedArticleSet>')
    if len(observed)!=len(selected):raise ValueError('duplicate/missing source events')
    with tempfile.TemporaryDirectory(prefix='pmkg-readiness-') as temp:
        store=Store(temp);started=time.monotonic()
        with store.lock():
            store.ingest(source,1,expected_pmids=selected,expected_count=len(selected));export_seconds=time.monotonic()-started
            report=verify_state(store);verify_seconds=time.monotonic()-started-export_seconds
            rows={p:json.loads(a) for p,a in store.db.execute('SELECT pmid,payload FROM articles')}
            screens=[{'name':g['name'],'records':len(g['pmids']),'candidate_records':sum(rows[p]['device_relevance']['status']=='candidate' for p in g['pmids']),'unassessed_pmids':[p for p in g['pmids'] if rows[p]['device_relevance']['status']=='unassessed']} for g in groups]
        summary={'sample_status':'passed','records':len(selected),'verified_records':report['final_articles'],'source_counts':report['sources'][0]['counts'],'eligible_metadata_retention':1.0,'record_event_retention':1.0,'serialized_triples':report['serialized_partition_triples'],'export_seconds':round(export_seconds,2),'verification_seconds':round(verify_seconds,2),'peak_rss_mib':round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024*1024 if sys.platform=='darwin' else 1024),1),'full_corpus_ready':False,'semantic_device_recall':'not established','sampling':'Purposive API strata; not independent representative device labels','production_limit':'Current snapshot exporter republishes the whole current graph per source; corpus-scale operation is not certified.'}
        (args.output/'summary.json').write_text(json.dumps(summary,indent=2));(args.output/'verification.json').write_text(json.dumps(report,indent=2));(args.output/'screening.json').write_text(json.dumps(screens,indent=2));(args.output/'queries-and-pmids.json').write_text(json.dumps(groups,indent=2))
        print(json.dumps(summary,indent=2))
    return 2 if args.require_full_corpus else 0

if __name__=='__main__':
    try:raise SystemExit(execute())
    except Exception as error:
        print('Readiness test failed: '+type(error).__name__+'; source content and URLs redacted.',file=sys.stderr);raise SystemExit(1)
