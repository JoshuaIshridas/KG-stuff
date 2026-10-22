"""Regression cases motivated by the broad live readiness test."""
import gzip
import hashlib
import io
import requests
from urllib3.response import HTTPResponse
from pubmed_kg.network import Client

def test_gzip_download_preserves_wire_bytes_for_parsing_and_md5(tmp_path,monkeypatch):
    compressed=gzip.compress(b'<PubmedArticleSet/>')
    response=requests.Response();response.status_code=200
    response.headers['Content-Encoding']='gzip'
    response.raw=HTTPResponse(body=io.BytesIO(compressed),headers={'Content-Encoding':'gzip'},preload_content=False)
    client=Client();monkeypatch.setattr(client,'get',lambda *a,**kw:response)
    target=tmp_path/'source.xml.gz'
    client.download('https://example.org/source.xml.gz',target,hashlib.md5(compressed).hexdigest())
    assert target.read_bytes()==compressed
    assert gzip.decompress(target.read_bytes())==b'<PubmedArticleSet/>'

import json
from rdflib import RDF
from pubmed_kg.graph import ROOT,K,iri
from pubmed_kg.parser import records
from pubmed_kg.relevance import default_vocabulary,assess
from pubmed_kg.store import Store
from pubmed_kg.verification import verify_state

def test_complete_2026_equipment_tree_is_bundled():
    vocabulary=default_vocabulary()
    assert len(vocabulary)==345 and 'D004864' in vocabulary
    assert all(any(t=='E07' or t.startswith('E07.') for t in d['tree_numbers']) for d in vocabulary.values())
    assert sum(len(d['terms']) for d in vocabulary.values())==3683

def test_device_terms_only_in_abstract_are_screened_without_text_export(tmp_path):
    raw=(ROOT/'fixtures/baseline.xml').read_text().replace('</ArticleTitle>','</ArticleTitle><Abstract><AbstractText>PRIVATE_SENTINEL a cardiac pacemaker was used</AbstractText></Abstract>',1)
    path=tmp_path/'abstract-device.xml';path.write_text(raw)
    a=list(records(path))[0][1]
    assert any(s['location']=='abstract' and s['method'] in ('mesh-device-term-screen','device-term-screen') for s in a['device_relevance']['signals'])
    state=Store(tmp_path/'state');state.ingest(path,1)
    assert 'PRIVATE_SENTINEL' not in state.graph().serialize()
    for path in state.root.rglob('*.jsonl'):assert 'PRIVATE_SENTINEL' not in path.read_text()
    assert verify_state(state)['status']=='verified'

def test_book_direct_publication_type_and_no_fabricated_journal(tmp_path):
    path=tmp_path/'book.xml';path.write_text('''<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID Version="1">900000099</PMID><ArticleIdList><ArticleId IdType="pubmed">900000099</ArticleId></ArticleIdList><Book><Publisher><PublisherName>Synthetic Press</PublisherName></Publisher><BookTitle>Synthetic device book</BookTitle><PubDate><Year>2026</Year></PubDate></Book><PublicationType UI="D000072643">Books</PublicationType></BookDocument></PubmedBookArticle></PubmedArticleSet>''')
    state=Store(tmp_path/'state');state.ingest(path,1);g=state.graph();article=iri('article','900000099')
    assert len(list(g.objects(article,K.publicationType)))==1
    assert not list(g.objects(article,K.journal))
    assert verify_state(state)['status']=='verified'

def test_device_term_screen_does_not_establish_device_identity():
    a={'title':'An investigation of cardiac pacemakers','mesh':[],'detections':[],'keywords':[]}
    result=assess(a)
    assert result['status']=='candidate' and result['excludes_record'] is False
    assert all(s['classification']=='candidate-topic' for s in result['signals'])
    assert 'verified' not in json.dumps(result)

def test_device_word_boundaries_and_short_ambiguous_names():
    a={'title':'CAT and pump expression in codecatheter models','mesh':[],'detections':[],'keywords':[]}
    assert assess(a)['status']=='unassessed'

def test_screening_evidence_corruption_fails_rdf_verification(tmp_path):
    from rdflib import Literal
    from pubmed_kg.audit import verify_rdf,VerificationError
    import pytest
    state=Store(tmp_path);state.ingest(ROOT/'fixtures/baseline.xml',1)
    g=state.graph();signal=next(g.objects(None,K.deviceSignal));g.set((signal,K.method,Literal('wrong method')))
    with pytest.raises(VerificationError,match='screening evidence'):verify_rdf(g,list(state.db.execute('SELECT * FROM articles')))
