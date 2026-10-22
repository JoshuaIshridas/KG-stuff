"""One command for reproducible offline count/JSON/RDF/query acceptance."""
import json
from pathlib import Path
import tempfile
from .store import Store
from .graph import ROOT,check,write
from .network import annotations
from .verification import verify_state,evaluate,ontology_check

def run(output):
    output=Path(output);output.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='pubmed-acceptance-') as temp:
        store=Store(temp)
        with store.lock():
            store.ingest(ROOT/'fixtures/baseline.xml',1,expected_count=3)
            store.ingest(ROOT/'fixtures/update.xml',2,expected_count=1)
            data=json.loads((ROOT/'fixtures/pubtator.json').read_text())
            for pmid, in store.db.execute('SELECT pmid FROM articles ORDER BY pmid'):
                values,status=annotations(data,pmid)
                store.db.execute('INSERT OR REPLACE INTO enrichment VALUES(?,?,?,?)',(pmid,json.dumps(values),status,'2026-10-21T00:00:00+00:00'))
            store.db.commit();store.publish();report=verify_state(store)
            g=store.graph();g.parse(ROOT/'fixtures/adapter.ttl');check(g);ontology_check(g)
            expected={q['file']:q['count'] for q in json.loads((ROOT/'queries/local-results.json').read_text())}
            results=[]
            for query in sorted((ROOT/'queries').glob('*.rq')):
                response=g.query(query.read_text());data=json.loads(response.serialize(format='json'))
                count=len(data['results']['bindings'])
                if count!=expected[query.name]:raise ValueError('SPARQL expectation failed: '+query.name)
                results.append(dict(file=query.name,count=count,result=data))
            benchmark=evaluate(store,ROOT/'fixtures/benchmark.synthetic.json')
            if benchmark['near_100_percent_device_recall_established']:raise ValueError('synthetic benchmark improperly accepted')
            write(g,output/'sample.ttl')
            (output/'verification-report.json').write_text(json.dumps(report,indent=2))
            (output/'query-results.json').write_text(json.dumps(results,indent=2))
            (output/'device-benchmark-report.json').write_text(json.dumps(benchmark,indent=2))
            # Distributable JSONL is metadata-only synthetic data, never live text.
            with (output/'sample-events.jsonl').open('w') as f:
                for source,digest in store.db.execute('SELECT name,digest FROM sources ORDER BY ord'):
                    f.write((store.root/'audits'/digest/'records.jsonl').read_text())
            summary=dict(status='verified',synthetic=True,source_article_events=4,deletion_events=1,current_articles=report['final_articles'],structured_metadata_retention=report['eligible_metadata_retention'],record_event_retention=report['record_event_retention'],sparql_queries=len(results),triples=len(g),near_100_percent_device_recall_established=False,device_recall_status='representative benchmark required')
            (output/'summary.json').write_text(json.dumps(summary,indent=2));return summary
