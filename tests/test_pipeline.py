import json
import gzip
from pathlib import Path
import pytest
import requests
from rdflib import Graph
from pubmed_kg.parser import records,norm
from pubmed_kg.store import Store
from pubmed_kg.graph import ROOT,K,check
from pubmed_kg.network import annotations,Client

def test_metadata():
    a=list(records(ROOT/'fixtures/baseline.xml'))[0][1]
    assert a['pmid']=='900000001'
    assert a['mesh'][0]['major'] is True
    assert a['mesh'][0]['qualifiers'][0]['major'] is False
    assert a['dates'][0]['parts']=={'Year':'2024'}
    assert a['identifiers'][0]['value']=='10.9999/synthetic.1'
    assert not list(records(ROOT/'fixtures/baseline.xml'))[1][1]['detections']
@pytest.mark.parametrize('scheme,value,expected',[('doi','DOI:10.1234/ABC','10.1234/abc'),('doi','bad',None),('nct','NCT123',None),('pmid','0',None),('pma','p123456','P123456')])
def test_norm(scheme,value,expected):assert norm(scheme,value)==expected

def test_compressed_dates_malformed(tmp_path):
    content=(ROOT/'fixtures/baseline.xml').read_text().replace('<Year>2024</Year>','<MedlineDate>2024 Winter</MedlineDate>')
    p=tmp_path/'x.gz';p.write_bytes(gzip.compress(content.encode()))
    assert list(records(p))[0][1]['dates'][0]['parts']=={'MedlineDate':'2024 Winter'}
    p.write_bytes(gzip.compress(b'<PubmedArticleSet><PubmedArticle>'))
    with pytest.raises(Exception):list(records(p))

def test_revisions_deletions_rerun(tmp_path):
    s=Store(tmp_path);assert s.ingest(ROOT/'fixtures/update.xml',2)
    assert s.ingest(ROOT/'fixtures/baseline.xml',1)
    assert not s.ingest(ROOT/'fixtures/baseline.xml',1)
    assert s.db.execute('SELECT count(*) FROM articles').fetchone()[0]==2
    assert 'revised' in s.db.execute('SELECT payload FROM articles WHERE pmid="900000001"').fetchone()[0]
    assert s.db.execute('SELECT pmid FROM tombstones').fetchone()[0]=='900000003'
    check(s.graph())

def test_interrupted_export(tmp_path,monkeypatch):
    s=Store(tmp_path);real=s.publish
    monkeypatch.setattr(s,'publish',lambda **kw:(_ for _ in ()).throw(OSError('simulated disk failure')))
    with pytest.raises(OSError):s.ingest(ROOT/'fixtures/baseline.xml',1)
    assert s.db.execute('SELECT count(*) FROM articles').fetchone()[0]==0
    assert s.db.execute('SELECT status FROM sources').fetchone()[0]=='failed'
    assert not (tmp_path/'CURRENT').exists()
    monkeypatch.setattr(s,'publish',real);s.ingest(ROOT/'fixtures/baseline.xml',1)
    assert s.db.execute('SELECT status FROM sources').fetchone()[0]=='exported'

def test_annotations():
    a,status=annotations(json.loads((ROOT/'fixtures/pubtator.json').read_text()),'900000001')
    assert status=='complete' and a[0]['namespace']=='MESH'
    assert a[2]['namespace']=='unmapped:Gene'
    assert annotations([], '1')==([], 'unavailable')

def test_retry(monkeypatch):
    monkeypatch.setattr('pubmed_kg.network.time.sleep',lambda _:None)
    c=Client();calls=[]
    def get(*a,**kw):
        calls.append(1)
        if len(calls)<3:raise requests.ConnectionError('secret body')
        r=requests.Response();r.status_code=200;return r
    monkeypatch.setattr(c.session,'get',get)
    assert c.get('https://example.org').status_code==200 and len(calls)==3
    monkeypatch.setattr(c.session,'get',lambda *a,**kw:(_ for _ in ()).throw(requests.Timeout('SECRET')))
    with pytest.raises(RuntimeError,match='redacted'):c.get('https://example.org?api_key=SECRET')

def test_queries_and_privacy(tmp_path):
    s=Store(tmp_path);s.ingest(ROOT/'fixtures/baseline.xml',1);s.ingest(ROOT/'fixtures/update.xml',2)
    data=json.loads((ROOT/'fixtures/pubtator.json').read_text());a,status=annotations(data,'900000001')
    s.db.execute('INSERT INTO enrichment VALUES(?,?,?,?)',('900000001',json.dumps(a),status,'2026-10-21'));s.db.commit();s.publish()
    g=s.graph();g.parse(ROOT/'fixtures/adapter.ttl');check(g)
    counts=[]
    for q in sorted((ROOT/'queries').glob('*.rq')):counts.append(len(list(g.query(q.read_text()))))
    assert counts==[1,1,2,2,1,1,1,2,1,2,2,4]
    text=g.serialize(format='turtle').lower();assert 'abstract' not in text and 'body' not in text

def test_transient_abstract(tmp_path):
    raw=(ROOT/'fixtures/baseline.xml').read_text().replace('</ArticleTitle>','</ArticleTitle><Abstract><AbstractText>PRIVATE_SENTINEL K654321</AbstractText></Abstract>',1)
    p=tmp_path/'transient.xml';p.write_text(raw);s=Store(tmp_path/'state');s.ingest(p,1)
    assert 'PRIVATE_SENTINEL' not in s.graph().serialize()
    assert 'K654321' in s.graph().serialize()

def test_shacl_rejects():
    from rdflib import RDF,URIRef
    g=Graph();g.add((URIRef('urn:bad'),RDF.type,K.Article))
    with pytest.raises(ValueError):check(g)

def test_lock(tmp_path):
    a=Store(tmp_path);b=Store(tmp_path)
    with a.lock():
        with pytest.raises(RuntimeError):
            with b.lock():pass

def test_adapter_only_candidates(tmp_path):
    from pubmed_kg.adapter import candidates
    s=Store(tmp_path);s.ingest(ROOT/'fixtures/baseline.xml',1)
    external=Graph().parse(ROOT/'fixtures/adapter.ttl')
    g=candidates(s.graph(),external,{'identifier_predicates':{'510k':str(K.regulatoryIdentifier),'pma':str(K.regulatoryIdentifier)}})
    assert len(list(g.subjects(K.status, __import__('rdflib').Literal('candidate'))))==2
    assert not list(g.subjects(K.status,__import__('rdflib').Literal('verified')))

def test_changed_source_rejected(tmp_path):
    s=Store(tmp_path/'state');p=tmp_path/'baseline.xml';p.write_bytes((ROOT/'fixtures/baseline.xml').read_bytes());s.ingest(p,1)
    p.write_text(p.read_text().replace('Synthetic','Changed',1))
    with pytest.raises(ValueError,match='identity'):s.ingest(p,1)

def test_repeated_bad_identifiers(tmp_path):
    raw=(ROOT/'fixtures/baseline.xml').read_text().replace('</ArticleIdList>','<ArticleId IdType="doi">bad</ArticleId><ArticleId IdType="doi">bad</ArticleId></ArticleIdList>',1)
    p=tmp_path/'input.xml';p.write_text(raw)
    a=list(records(p))[0][1];bad=[i for i in a['identifiers'] if i['raw']=='bad']
    assert len(bad)==2 and all(not i['valid'] for i in bad)

def test_log_privacy(tmp_path,capsys,monkeypatch):
    from pubmed_kg.cli import main
    monkeypatch.setattr('sys.argv',['pmkg','--state',str(tmp_path),'ingest','nonexistent','--order','1'])
    with pytest.raises(FileNotFoundError):main()
    assert 'PRIVATE_SENTINEL' not in capsys.readouterr().out

def test_actual_bioc_wrapper():
    data={'PubTator3':[{'id':'internal-id','pmid':123,'passages':[{'infons':{'type':'abstract'},'annotations':[{'text':'DO_NOT_PERSIST','infons':{'type':'Gene','identifier':'672','database':'ncbi_gene'}}]}]}]}
    a,status=annotations(data,'123')
    assert status=='complete' and a[0]['namespace']=='ncbi_gene'
    assert 'DO_NOT_PERSIST' not in json.dumps(a)

def test_export_deterministic(tmp_path):
    s=Store(tmp_path);s.ingest(ROOT/'fixtures/baseline.xml',1)
    a=s.publish();before=(a/'part-00000.ttl').read_bytes();b=s.publish()
    assert a==b and before==(b/'part-00000.ttl').read_bytes()

def test_unknown_annotation_response():
    with pytest.raises(ValueError):annotations({'error':'failure'},'123')

def test_scheduled_update_discovers_and_resumes(tmp_path,monkeypatch):
    from pubmed_kg.cli import main
    root=tmp_path/'state';s=Store(root)
    baseline=tmp_path/'pubmed26n0001.xml.gz';baseline.write_bytes(gzip.compress((ROOT/'fixtures/baseline.xml').read_bytes()));s.ingest(baseline,1)
    manifest=tmp_path/'manifest.json';manifest.write_text(json.dumps({'baseline_complete':True,'discover_updates':True,'sources':[{'name':baseline.name,'order':1,'url':'https://example.org/'+baseline.name}]}))
    monkeypatch.setattr('pubmed_kg.cli.discover',lambda *a:[('pubmed26n0002.xml.gz','https://example.org/pubmed26n0002.xml.gz')])
    class Response:
        text='0'*32
        def __enter__(self):return self
        def __exit__(self,*a):pass
    monkeypatch.setattr(Client,'get',lambda *a,**kw:Response())
    calls=[]
    def download(self,url,target,md5=None):
        calls.append(url);target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(gzip.compress((ROOT/'fixtures/update.xml').read_bytes()));return target
    monkeypatch.setattr(Client,'download',download)
    monkeypatch.setattr('sys.argv',['pmkg','--state',str(root),'update','--manifest',str(manifest)])
    main();main()
    assert len(calls)==1
    assert len(json.loads(manifest.read_text())['sources'])==2
    assert Store(root).db.execute('SELECT count(*) FROM articles').fetchone()[0]==2

def test_unapproved_baseline_cannot_update(tmp_path,monkeypatch):
    from pubmed_kg.cli import main
    m=tmp_path/'m.json';m.write_text('{"baseline_complete":false,"sources":[]}')
    monkeypatch.setattr('sys.argv',['pmkg','--state',str(tmp_path/'state'),'update','--manifest',str(m)])
    with pytest.raises(ValueError,match='complete baseline'):main()
