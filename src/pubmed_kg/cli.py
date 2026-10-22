import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from rdflib import Graph
from .store import Store,now
from .graph import ROOT,check,write
from .network import Client,discover,annotations

def main():
    p=argparse.ArgumentParser(description='Metadata-only PubMed RDF pipeline')
    p.add_argument('--state',default='state');sub=p.add_subparsers(dest='command',required=True)
    s=sub.add_parser('acceptance');s.add_argument('--output',default='acceptance-results')
    sub.add_parser('demo');s=sub.add_parser('ingest');s.add_argument('path');s.add_argument('--order',type=int,required=True);s.add_argument('--expected-count',type=int)
    s=sub.add_parser('download');s.add_argument('url');s.add_argument('--order',type=int,required=True);s.add_argument('--md5')
    s=sub.add_parser('update');s.add_argument('--manifest',required=True)
    s=sub.add_parser('discover');s.add_argument('--kind',choices=['baseline','updatefiles'],required=True)
    s=sub.add_parser('fetch');s.add_argument('pmids',nargs='+');s.add_argument('--order',type=int,required=True)
    s=sub.add_parser('enrich');s.add_argument('--refresh',action='store_true');s.add_argument('--limit',type=int,default=10);s.add_argument('--fixture')
    s=sub.add_parser('inspect-mdkg');s.add_argument('--config',required=True)
    s=sub.add_parser('match');s.add_argument('--config',required=True);s.add_argument('--external',required=True);s.add_argument('--output',required=True)
    s=sub.add_parser('link-source');s.add_argument('source');s.add_argument('--config',required=True);s.add_argument('--external',required=True);s.add_argument('--output',required=True)
    sub.add_parser('verify');s=sub.add_parser('benchmark');s.add_argument('labels');s.add_argument('--min-recall',type=float,default=0.99)
    s=sub.add_parser('mesh-index');s.add_argument('xml')
    s=sub.add_parser('corpus-plan',help='Freeze the full official baseline/update inventory; downloads no source files');s.add_argument('--output',required=True)
    s=sub.add_parser('corpus-run',help='Unfiltered ordered import with verified per-source RDF deltas');s.add_argument('--plan',required=True)
    limits=s.add_mutually_exclusive_group();limits.add_argument('--max-files',type=int,default=1);limits.add_argument('--all',action='store_true')
    s.add_argument('--batch-size',type=int,default=50)
    s=sub.add_parser('corpus-update',help='Refresh official inventory, then resume ordered delta ingestion');s.add_argument('--plan',required=True)
    limits=s.add_mutually_exclusive_group();limits.add_argument('--max-files',type=int,default=1);limits.add_argument('--all',action='store_true')
    s.add_argument('--batch-size',type=int,default=50)
    sub.add_parser('corpus-status');sub.add_parser('corpus-verify')
    s=sub.add_parser('corpus-enrich');s.add_argument('--limit',type=int,default=10);s.add_argument('--refresh',action='store_true');s.add_argument('--fixture')
    s=sub.add_parser('fda-index');s.add_argument('sources',nargs='+');s.add_argument('--output',required=True);s.add_argument('--ontology',default=str(ROOT/'integration/fda/ontology.ttl'));s.add_argument('--format',choices=['ttl','nt','nq','trig'])
    for name in ['pipeline-init','pipeline-status','pipeline-verify','materialize']:
        s=sub.add_parser(name);s.add_argument('--config',required=True)
    s=sub.add_parser('pipeline-update');s.add_argument('--config',required=True)
    limits=s.add_mutually_exclusive_group();limits.add_argument('--max-files',type=int,default=1);limits.add_argument('--all',action='store_true')
    s.add_argument('--frozen-plan',action='store_true')
    s=sub.add_parser('pipeline-link');s.add_argument('--config',required=True);s.add_argument('--max-sources',type=int,default=1)
    s=sub.add_parser('pipeline-enrich');s.add_argument('--config',required=True);s.add_argument('--limit',type=int,default=10);s.add_argument('--refresh',action='store_true')
    s=sub.add_parser('link-review');s.add_argument('--config',required=True);s.add_argument('--decisions',required=True)
    s=sub.add_parser('link-candidates');s.add_argument('--config',required=True);s.add_argument('--output',required=True);s.add_argument('--limit',type=int,default=100)
    s=sub.add_parser('pipeline-query');s.add_argument('--config',required=True);s.add_argument('query')
    s=sub.add_parser('pipeline-export');s.add_argument('--config',required=True);s.add_argument('--output',required=True)
    s=sub.add_parser('integrated-demo');s.add_argument('--output',required=True)
    sub.add_parser('export');sub.add_parser('validate');s=sub.add_parser('query');s.add_argument('query');s.add_argument('--adapter-fixture',action='store_true')
    args=p.parse_args()
    if args.command=='integrated-demo':
        from .integrated import run
        print(json.dumps(run(args.output),indent=2));return
    if args.command=='fda-index':
        from .fda import build_index
        print(json.dumps(build_index(args.sources,args.output,args.ontology,args.format),indent=2));return
    if args.command.startswith('pipeline-') or args.command in ('materialize','link-review','link-candidates'):
        from . import pipeline
        from .corpus import CorpusStore
        from .fda import FdaIndex
        from .materialize import Materialized
        from .device_links import link_corpus,review_links
        config=pipeline.profile(args.config)
        if args.command=='pipeline-init':report=pipeline.initialize(config)
        elif args.command=='pipeline-update':
            if args.max_files<1:raise ValueError('max-files must be positive')
            report=pipeline.update(config,None if args.all else args.max_files,not args.frozen_plan)
        elif args.command=='pipeline-verify':report=pipeline.verify(config)
        else:
            store,index=CorpusStore(config['state']),FdaIndex(config['fda_index'])
            with store.lock():
                store.check_contract()
                if args.command=='pipeline-status':report=pipeline.status(store,index)
                elif args.command=='link-candidates':
                    if not 1<=args.limit<=10000:raise ValueError('candidate limit must be 1..10000')
                    values=[json.loads(row[0]) for row in store.db.execute('SELECT payload FROM link_candidates ORDER BY id LIMIT ?',(args.limit,))]
                    Path(args.output).write_text(json.dumps(values,indent=2));report={'candidates':len(values),'output':args.output}
                else:
                    if args.command=='pipeline-link':report=link_corpus(store,index,args.max_sources)
                    elif args.command=='link-review':report=review_links(store,index,json.loads(Path(args.decisions).read_text()))
                    elif args.command=='pipeline-enrich':
                        from .enrichment import enrich_corpus
                        report=enrich_corpus(store,Client(),args.limit,args.refresh)
                    with Materialized(config['rdf_store']) as target:
                        if args.command in ('pipeline-query','pipeline-export'):
                            target.require_current(store);target.require_catalog(index)
                            if pipeline.status(store,index)['linking_pending']:
                                from .audit import VerificationError
                                raise VerificationError('FDA linking is incomplete for the configured catalog')
                            if args.command=='pipeline-query':report=target.query(Path(args.query).read_text())
                            else:target.export(args.output);report={'export':args.output,'format':'nquads'}
                        else:
                            pipeline.load_contexts(target,index,config)
                            loaded=target.apply(store)
                            if args.command=='materialize':report=loaded
        print(json.dumps(report,indent=2))
        if args.command=='pipeline-enrich' and report['statuses']['failed']:raise RuntimeError('annotation request failures recorded')
        if args.command=='pipeline-update' and report.get('annotation_run') and report['annotation_run']['statuses']['failed']:raise RuntimeError('metadata committed; annotation failures recorded for retry')
        return
    if args.command=='link-source':
        from .linking import link_source
        print(json.dumps(link_source(args.source,args.external,json.loads(Path(args.config).read_text()),args.output),indent=2));return
    if args.command=='corpus-plan':
        from .corpus import discover_plan,atomic_json
        plan=discover_plan(Client());atomic_json(args.output,plan)
        print(json.dumps({'plan':args.output,'files':len(plan['sources']),'plan_sha256':plan['plan_sha256'],'source_files_downloaded':0}));return
    if args.command.startswith('corpus-'):
        from .corpus import CorpusStore,run_plan,verify_corpus
        store=CorpusStore(args.state)
        with store.lock():
            if args.command in ('corpus-run','corpus-update'):
                if args.max_files<1:raise ValueError('max-files must be positive')
                client=Client()
                if args.command=='corpus-update':
                    from .corpus import discover_plan,atomic_json
                    plan=discover_plan(client);store.bind_plan(plan);atomic_json(args.plan,plan)
                else:plan=json.loads(Path(args.plan).read_text())
                report=run_plan(store,plan,client,None if args.all else args.max_files,args.batch_size)
            elif args.command=='corpus-verify':report=verify_corpus(store)
            elif args.command=='corpus-enrich':
                from .enrichment import enrich_corpus
                report=enrich_corpus(store,Client(),args.limit,args.refresh,args.fixture)
            else:report=store.status()
            print(json.dumps(report,indent=2))
            if args.command=='corpus-enrich' and report['statuses']['failed']:raise RuntimeError('annotation requests failed; retry recorded failures')
        return
    store=Store(args.state)
    if store.db.execute("SELECT name FROM sqlite_master WHERE name='corpus_meta'").fetchone():
        raise ValueError('corpus state requires corpus commands; use a separate state for the snapshot/demo commands')
    with store.lock():
        c=Client(rate=3)
        if args.command=='acceptance':
            from .acceptance import run
            print(json.dumps(run(args.output),indent=2))
        elif args.command=='demo':
            store.ingest(ROOT/'fixtures/baseline.xml',1);store.ingest(ROOT/'fixtures/update.xml',2)
            data=json.loads((ROOT/'fixtures/pubtator.json').read_text())
            for pmid, in store.db.execute('SELECT pmid FROM articles'):
                values,status=annotations(data,pmid);store.db.execute('INSERT OR REPLACE INTO enrichment VALUES(?,?,?,?)',(pmid,json.dumps(values),status,'2026-10-21T00:00:00+00:00'))
            store.db.commit();store.publish();g=store.graph();g.parse(ROOT/'fixtures/adapter.ttl');check(g);write(g,store.root/'demo.ttl')
            print(json.dumps({'demo':str(store.root/'demo.ttl'),'synthetic':True,'baseline_complete':False,'triples':len(g)}))
        elif args.command=='ingest':store.ingest(args.path,args.order,expected_count=args.expected_count)
        elif args.command=='discover':print(json.dumps(discover(c,'https://ftp.ncbi.nlm.nih.gov/pubmed/'+args.kind+'/'),indent=2))
        elif args.command=='download':
            name=Path(args.url).name
            if not re.fullmatch(r'pubmed\d+n\d+\.xml\.gz',name):raise ValueError('expected official PubMed source filename')
            target=c.download(args.url,store.root/'raw-private'/name,args.md5);store.ingest(target,args.order)
        elif args.command=='update':
            manifest=json.loads(Path(args.manifest).read_text());sources=manifest['sources']
            if not manifest.get('baseline_complete'):raise ValueError('complete baseline inventory required; use ingest for explicitly partial samples')
            if manifest.get('discover_updates'):
                if not sources:raise ValueError('baseline inventory required before update discovery')
                pattern=r'pubmed(\d+)n(\d+)\.xml\.gz'
                last=re.fullmatch(pattern,sources[-1]['name'])
                if not last:raise ValueError('official filenames required for automatic discovery')
                year,number=map(int,last.groups());order=sources[-1]['order']
                for name,url in discover(c,'https://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/'):
                    match=re.fullmatch(pattern,name);y,n=map(int,match.groups())
                    if y!=year:raise ValueError('annual rollover requires a fresh reviewed baseline')
                    if n<=number:continue
                    if n!=number+1:raise ValueError('missing daily update dependency')
                    order+=1;number=n
                    with c.get(url+'.md5') as response:
                        checksum=re.search(r'\b[0-9a-fA-F]{32}\b',response.text)
                    if not checksum:raise ValueError('official checksum unavailable')
                    sources.append(dict(name=name,url=url,order=order,md5=checksum.group().lower()))
                temp=Path(args.manifest).with_suffix('.tmp');temp.write_text(json.dumps(manifest,indent=2));os.replace(temp,args.manifest)
            orders=[s['order'] for s in sources]
            if len(set(orders))!=len(orders) or orders!=sorted(orders):raise ValueError('manifest must have unique increasing orders')
            if not sources or any(b!=a+1 for a,b in zip(orders,orders[1:])):raise ValueError('source order gap')
            for source in sources:
                prior=store.db.execute('SELECT status FROM sources WHERE name=?',(source['name'],)).fetchone()
                if prior and prior[0]=='exported':continue
                target=c.download(source['url'],store.root/'raw-private'/source['name'],source.get('md5'))
                store.ingest(target,source['order'])
            # Every scheduler invocation ends with independent JSON replay and serialized RDF checks.
            from .verification import verify_state
            verify_state(store)
            if manifest.get('verify_complete_inventory'):
                from .verification import inventory_check
                inventory_check(store,manifest,discover(c,'https://ftp.ncbi.nlm.nih.gov/pubmed/baseline/'),discover(c,'https://ftp.ncbi.nlm.nih.gov/pubmed/updatefiles/'))
        elif args.command=='fetch':
            if not all(re.fullmatch(r'[1-9]\d*',v) for v in args.pmids):raise ValueError('invalid PMID')
            for idx in range(0,len(args.pmids),200):
                params=dict(db='pubmed',retmode='xml',id=','.join(args.pmids[idx:idx+200]),tool='pubmed-mdkg')
                if os.getenv('NCBI_EMAIL'):params['email']=os.environ['NCBI_EMAIL']
                if os.getenv('NCBI_API_KEY'):params['api_key']=os.environ['NCBI_API_KEY']
                target=store.root/'raw-private'/f'fetch-{args.order+idx//200}.xml';target.parent.mkdir(exist_ok=True)
                with c.get('https://eutils.ncbi.nlm.nih.gov/entrez/eutils/efetch.fcgi',params=params,stream=True) as r, target.with_suffix('.part').open('wb') as f:
                    for b in r.iter_content(1024*1024):f.write(b)
                os.replace(target.with_suffix('.part'),target);store.ingest(target,args.order+idx//200,expected_pmids=args.pmids[idx:idx+200])
        elif args.command=='enrich':
            rows=store.db.execute('SELECT pmid FROM articles ORDER BY pmid');done=0
            for pmid, in rows:
                prior=store.db.execute('SELECT status FROM enrichment WHERE pmid=?',(pmid,)).fetchone()
                if prior and prior[0]=='complete' and not args.refresh:continue
                if done>=args.limit:break
                store.db.execute('INSERT OR REPLACE INTO enrichment VALUES(?,?,?,?)',(pmid,'[]','pending',now()));store.db.commit()
                try:
                    if args.fixture:data=json.loads(Path(args.fixture).read_text())
                    else:
                        with c.get('https://www.ncbi.nlm.nih.gov/research/pubtator3-api/publications/export/biocjson',params={'pmids':pmid}) as r:data=r.json()
                    values,status=annotations(data,pmid)
                except (RuntimeError,ValueError):values,status=[],'failed'
                store.db.execute('INSERT OR REPLACE INTO enrichment VALUES(?,?,?,?)',(pmid,json.dumps(values),status,now()));store.db.commit();done+=1
            store.publish()
        elif args.command=='inspect-mdkg':
            from .adapter import inspect
            print(json.dumps(inspect(json.loads(Path(args.config).read_text())),indent=2))
        elif args.command=='match':
            from .adapter import candidates
            g=candidates(store.graph(),Graph().parse(args.external),json.loads(Path(args.config).read_text()));check(g);write(g,Path(args.output))
        elif args.command=='verify':
            from .verification import verify_state
            print(json.dumps(verify_state(store),indent=2))
        elif args.command=='benchmark':
            from .verification import evaluate
            report=evaluate(store,args.labels,args.min_recall);print(json.dumps(report,indent=2))
            if not report['near_100_percent_device_recall_established']:raise SystemExit(2)
        elif args.command=='mesh-index':
            from .relevance import mesh_index
            print(json.dumps({'descriptors':mesh_index(args.xml,store.root/'device-mesh.json')}))
        elif args.command=='export':print(store.publish())
        elif args.command=='validate':
            from .verification import verify_state
            report=verify_state(store);print(json.dumps({'serialized_partition_triples':report['serialized_partition_triples'],'articles':report['final_articles'],'conforms':True}))
        elif args.command=='query':
            g=store.graph()
            if args.adapter_fixture:g.parse(ROOT/'fixtures/adapter.ttl')
            r=g.query(Path(args.query).read_text());print(r.serialize(format='json').decode())
def entrypoint():
    try:
        main()
        return 0
    except Exception as exc:
        # Provider messages and validation reports may contain restricted source values.
        print('Operation failed: '+type(exc).__name__+'; inspect ledger and retry using runbook.',file=sys.stderr)
        return 1

if __name__=='__main__':
    raise SystemExit(entrypoint())
