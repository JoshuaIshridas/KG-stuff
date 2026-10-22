#!/usr/bin/env python3
"""Check one official baseline and update file; transform 50-record samples only."""
import argparse,gzip,hashlib,json,random,re,sys,tempfile,time
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'src'))
from lxml import etree
from pubmed_kg.network import Client,discover
from pubmed_kg.store import Store
from pubmed_kg.verification import verify_state

def run():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--cache',type=Path,required=True);parser.add_argument('--output',type=Path,default=Path('reports/bulk-samples'));parser.add_argument('--offline',action='store_true');args=parser.parse_args()
    args.cache.mkdir(parents=True,exist_ok=True);args.output.mkdir(parents=True,exist_ok=True);c=Client(rate=2)
    inventory=args.cache/'bulk-inputs.json';inputs=[]
    if args.offline:inputs=json.loads(inventory.read_text())
    else:
        for kind,base in [('baseline','https://ftp.ncbi.nlm.nih.gov/pubmed/baseline/'),('update','https://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/')]:
            sources=discover(c,base);name,url=sources[0] if kind=='baseline' else sources[-1]
            with c.get(url+'.md5') as r:md5=re.search(r'\b[a-fA-F0-9]{32}\b',r.text).group().lower()
            inputs.append(dict(kind=kind,name=name,url=url,md5=md5))
        inventory.write_text(json.dumps(inputs,indent=2))
    results=[]
    for source in inputs:
        path=args.cache/source['name']
        if not path.exists():
            if args.offline:raise ValueError('private input missing')
            c.download(source['url'],path,source['md5'])
        checksum=hashlib.md5()
        with path.open('rb') as f:
            for chunk in iter(lambda:f.read(1024*1024),b''):checksum.update(chunk)
        if checksum.hexdigest()!=source['md5']:raise ValueError('official MD5 mismatch')
        rng=random.Random(20261021);sample=[];deletes=[];delete_ids=[];count=0;book_count=0
        with gzip.open(path,'rb') as f:
            for _,e in etree.iterparse(f,events=('end',),tag=('PubmedArticle','PubmedBookArticle','DeleteCitation'),resolve_entities=False,no_network=True):
                if e.tag=='DeleteCitation':deletes.append(etree.tostring(e));delete_ids.extend(p.text for p in e.findall('PMID'))
                else:
                    count+=1;book_count+=int(e.tag=='PubmedBookArticle');item=(count,etree.tostring(e))
                    if len(sample)<50:sample.append(item)
                    else:
                        index=rng.randrange(count)
                        if index<50:sample[index]=item
                e.clear()
                while e.getprevious() is not None:del e.getparent()[0]
        selected=args.cache/(source['kind']+'-bounded-sample.xml.gz')
        with selected.open('wb') as raw,gzip.GzipFile(fileobj=raw,mode='wb',mtime=0) as f:
            f.write(b'<PubmedArticleSet>')
            for _,record in sorted(sample):f.write(record)
            for notice in deletes:f.write(notice)
            f.write(b'</PubmedArticleSet>')
        with tempfile.TemporaryDirectory(prefix='pmkg-bulk-sample-') as temp:
            store=Store(temp);start=time.monotonic()
            with store.lock():store.ingest(selected,1,expected_count=len(sample));report=verify_state(store)
            if {p for p, in store.db.execute('SELECT pmid FROM tombstones')}!=set(delete_ids):raise ValueError('deletion identity mismatch')
            result=dict(kind=source['kind'],filename=source['name'],url=source['url'],official_md5=source['md5'],whole_article_records=count-book_count,whole_book_records=book_count,delete_notices=len(delete_ids),sample_upsert_events=len(sample),verified_current_articles=report['final_articles'],verified_tombstones=report['deleted_pmids'],seconds=round(time.monotonic()-start,2),sample_verified=True,full_corpus_ready=False,scope='Entire compressed file checksum and XML record counts; only 50 sampled upserts plus all deletion notices transformed to JSON and RDF.')
            results.append(result)
    (args.output/'results.json').write_text(json.dumps(results,indent=2));print(json.dumps(results,indent=2));return 0
if __name__=='__main__':
    try:raise SystemExit(run())
    except Exception as error:print('Bulk sample test failed: '+type(error).__name__+'; source content redacted.',file=sys.stderr);raise SystemExit(1)
