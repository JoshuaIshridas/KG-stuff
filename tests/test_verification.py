import gzip
import hashlib
import json
from pathlib import Path
import pytest
from rdflib import Graph, Literal,RDF
from pubmed_kg.graph import ROOT,K,build,check,iri
from pubmed_kg.store import Store
from pubmed_kg.audit import prepare,source_inventory,json_inventory,reconcile,VerificationError,verify_rdf
from pubmed_kg.verification import verify_state,evaluate,inventory_check,ontology_check
from pubmed_kg.parser import records


def fixture_path():return ROOT/'fixtures/baseline.xml'
def stage(tmp_path):
    return prepare(fixture_path(),tmp_path/'audit')

def test_count_json_verify_workflow(tmp_path):
    path,report=stage(tmp_path)
    assert report['record_event_retention']==1
    assert report['eligible_metadata_retention']==1
    assert report['source']['counts']['article']==3
    assert report['source']['counts']['metadata_nodes']>100
    assert path.exists() and not path.with_suffix('.part').exists()

def test_drop_record_detected(tmp_path):
    path,report=stage(tmp_path);lines=path.read_text().splitlines();path.write_text('\n'.join(lines[:-1])+'\n')
    with pytest.raises(VerificationError,match='count'):reconcile(report['source'],json_inventory(path))

def test_same_count_wrong_id_detected(tmp_path):
    path,report=stage(tmp_path);items=[json.loads(x) for x in path.read_text().splitlines()]
    items[0]['pmid']=items[0]['record']['pmid']='999999999'
    path.write_text(''.join(json.dumps(x)+'\n' for x in items))
    with pytest.raises(VerificationError):reconcile(report['source'],json_inventory(path))

def test_same_count_field_corruption_detected(tmp_path):
    path,report=stage(tmp_path);items=[json.loads(x) for x in path.read_text().splitlines()]
    items[0]['record']['metadata_fields'][0]['attributes']['corrupt']='yes'
    path.write_text(''.join(json.dumps(x)+'\n' for x in items))
    with pytest.raises(VerificationError):reconcile(report['source'],json_inventory(path))

def test_declared_source_count_and_requested_set(tmp_path):
    with pytest.raises(VerificationError,match='count'):prepare(fixture_path(),tmp_path/'bad',expected_count=4)
    with pytest.raises(VerificationError,match='PMID'):prepare(fixture_path(),tmp_path/'bad2',expected_pmids=['900000001'])

def test_schema_drift_stops_completion(tmp_path):
    raw=fixture_path().read_text().replace('</ArticleTitle>','</ArticleTitle><NewDeviceField>K444444</NewDeviceField>',1)
    p=tmp_path/'unknown.xml';p.write_text(raw);s=Store(tmp_path/'state')
    with pytest.raises(VerificationError,match='unknown'):s.ingest(p,1)
    assert not (s.root/'CURRENT').exists()
    assert s.db.execute('SELECT status FROM sources').fetchone()==('failed',)

def test_unsupported_top_level_and_empty(tmp_path):
    for content in ['<PubmedArticleSet><Unknown/></PubmedArticleSet>','<PubmedArticleSet/>','<ERROR>not a data source</ERROR>']:
        p=tmp_path/'x.xml';p.write_text(content)
        with pytest.raises(VerificationError):source_inventory(p)

def test_source_content_exclusion_and_new_fields(tmp_path):
    raw=fixture_path().read_text().replace('</MedlineCitation>','''<KeywordList Owner="NOTNLM"><Keyword MajorTopicYN="Y">catheter</Keyword></KeywordList><ChemicalList><Chemical><RegistryNumber>0</RegistryNumber><NameOfSubstance UI="D000002">Synthetic chemical</NameOfSubstance></Chemical></ChemicalList><SupplMeshList><SupplMeshName UI="C000001" Type="Protocol">Synthetic protocol</SupplMeshName></SupplMeshList><CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn"><RefSource>PRIVATE_REFERENCE</RefSource><PMID>900000009</PMID><Note>PRIVATE_NOTE</Note></CommentsCorrections></CommentsCorrectionsList><OtherAbstract Type="Publisher"><AbstractText>PRIVATE_ABSTRACT K777777</AbstractText></OtherAbstract></MedlineCitation>''',1)
    p=tmp_path/'extended.xml';p.write_text(raw);s=Store(tmp_path/'state');s.ingest(p,1)
    output=s.graph().serialize()
    for sentinel in ['PRIVATE_REFERENCE','PRIVATE_NOTE','PRIVATE_ABSTRACT']:assert sentinel not in output
    row=json.loads(s.db.execute('SELECT payload FROM articles WHERE pmid="900000001"').fetchone()[0])
    assert row['keywords'][0]['value']=='catheter';assert row['chemicals'][0]['identifier']=='D000002'
    assert row['supplementary'][0]['entityType']=='Protocol';assert row['corrections'][0]['relation']=='RetractionIn'
    assert 'K777777' in output
    assert verify_state(s)['status']=='verified'

def test_book_supported(tmp_path):
    p=tmp_path/'book.xml';p.write_text('''<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID Version="1">900000050</PMID><ArticleIdList><ArticleId IdType="doi">10.1234/book</ArticleId></ArticleIdList><Book><Publisher><PublisherName>Synthetic Press</PublisherName></Publisher><BookTitle>Synthetic Medical Device Handbook</BookTitle><PubDate><Year>2023</Year><Season>Winter</Season></PubDate></Book><ArticleTitle>Synthetic catheter chapter</ArticleTitle></BookDocument></PubmedBookArticle></PubmedArticleSet>''')
    s=Store(tmp_path/'state');s.ingest(p,1);g=s.graph()
    assert (iri('article','900000050'),RDF.type,K.BookArticle) in g
    assert verify_state(s)['final_articles']==1

def test_duplicate_events_count_not_unique_pmids(tmp_path):
    raw=fixture_path().read_text();content=raw.removeprefix('<PubmedArticleSet>').removesuffix('</PubmedArticleSet>')
    p=tmp_path/'dup.xml';p.write_text('<PubmedArticleSet>'+content+content+'</PubmedArticleSet>')
    s=Store(tmp_path/'state');s.ingest(p,1);r=verify_state(s)
    assert r['sources'][0]['counts']['article']==6
    assert r['final_articles']==3

def test_drop_turtle_field_detected(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1);g=s.graph();rows=list(s.db.execute('SELECT * FROM articles'))
    node=next(g.objects(None,K.metadataField));g.remove((node,K.fieldValue,None))
    with pytest.raises(VerificationError):verify_rdf(g,rows)

def test_edited_turtle_blocks_state_verification(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1);folder=s.root/'snapshots'/(s.root/'CURRENT').read_text()
    part=next(folder.glob('*.ttl'));part.write_text(part.read_text()+'\n# changed\n')
    with pytest.raises(VerificationError,match='checksum'):verify_state(s)

def test_ledger_corruption_detected(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1);s.db.execute('DELETE FROM articles WHERE pmid="900000001"');s.db.commit()
    with pytest.raises(VerificationError,match='ledger'):verify_state(s)

def test_old_tombstones_audited(tmp_path):
    s=Store(tmp_path);s.ingest(ROOT/'fixtures/update.xml',2);s.ingest(fixture_path(),1)
    report=verify_state(s);assert report['final_articles']==2 and report['deleted_pmids']==1

def test_ontology_vocabulary_and_forbidden_source_text(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1);g=s.graph();assert ontology_check(g)['undeclared_used_terms']==0
    node=next(g.objects(None,K.metadataField));g.set((node,K.element,Literal('AbstractText')))
    with pytest.raises(ValueError):check(g)
    g.add((node,K.inventedUnreviewedPredicate,Literal('x')))
    with pytest.raises(VerificationError,match='undeclared'):ontology_check(g)

def test_small_perfect_benchmark_does_not_establish_population_recall(tmp_path):
    s=Store(tmp_path/'state');s.ingest(fixture_path(),1)
    labels=tmp_path/'labels.json';labels.write_text(json.dumps({'representative':False,'population':'synthetic tests','labeling_method':'synthetic test labels','records':[{'pmid':'900000001','is_device':True},{'pmid':'900000002','is_device':False}]}))
    result=evaluate(s,labels)
    assert result['device_screening_recall']==1.0
    assert result['near_100_percent_device_recall_established'] is False
    assert result['recall_95_percent_lower_bound']<0.99

def test_unflagged_device_is_false_negative_not_dropped(tmp_path):
    s=Store(tmp_path/'state');s.ingest(fixture_path(),1)
    labels=tmp_path/'labels.json';labels.write_text(json.dumps({'records':[{'pmid':'900000002','is_device':True}]}))
    r=evaluate(s,labels);assert r['fn']==1 and r['device_screening_recall']==0
    assert s.db.execute('SELECT COUNT(*) FROM articles').fetchone()[0]==3

def test_inventory_missing_file_blocks_claim(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1)
    with pytest.raises(VerificationError):inventory_check(s,{'sources':[{'name':'baseline.xml'}]},[('baseline.xml','u'),('missing.xml','u')],[])
    result=inventory_check(s,{'sources':[{'name':'baseline.xml'}]},[('baseline.xml','u')],[])
    assert result['published_file_inventory_coverage']==1.0

def test_mesh_index_descendants(tmp_path):
    from pubmed_kg.relevance import mesh_index,assess
    out=tmp_path/'mesh.json';assert mesh_index(ROOT/'fixtures/mesh.synthetic.xml',out)==2
    vocabulary=json.loads(out.read_text())['descriptors']
    a=list(records(fixture_path()))[1][1]
    a['mesh']=[{'id':'SYNTHETIC-EQUIPMENT'}]
    assert assess(a,vocabulary)['status']=='candidate'
    assert assess(a)['status']=='unassessed'

def test_changed_normalized_title_fails_json_verification(tmp_path):
    path,_=stage(tmp_path);items=[json.loads(x) for x in path.read_text().splitlines()]
    items[0]['record']['title']='wrong title'
    path.write_text(''.join(json.dumps(x)+'\n' for x in items))
    with pytest.raises(VerificationError,match='title'):json_inventory(path)

def test_rdf_partition_drop_without_count_change_detected(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1);g=s.graph();rows=list(s.db.execute('SELECT * FROM articles'))
    u=iri('article','900000001');n=next(g.objects(u,K.date));g.set((n,K.Year,Literal('2099')))
    with pytest.raises(VerificationError,match='normalized'):verify_rdf(g,rows)

def test_changed_normalized_identifier_fails_json_verification(tmp_path):
    path,_=stage(tmp_path);items=[json.loads(x) for x in path.read_text().splitlines()]
    items[0]['record']['identifiers'][0]['value']='10.0000/wrong'
    path.write_text(''.join(json.dumps(x)+'\n' for x in items))
    with pytest.raises(VerificationError,match='normalization'):json_inventory(path)

def test_changed_normalized_date_fails_json_verification(tmp_path):
    path,_=stage(tmp_path);items=[json.loads(x) for x in path.read_text().splitlines()]
    items[0]['record']['dates'][0]['parts']['Year']='2099'
    path.write_text(''.join(json.dumps(x)+'\n' for x in items))
    with pytest.raises(VerificationError,match='date'):json_inventory(path)

def test_changed_mesh_ui_fails_json_verification(tmp_path):
    path,_=stage(tmp_path);items=[json.loads(x) for x in path.read_text().splitlines()]
    items[0]['record']['mesh'][0]['id']='D999999'
    path.write_text(''.join(json.dumps(x)+'\n' for x in items))
    with pytest.raises(VerificationError,match='MeSH'):json_inventory(path)

def test_abstract_in_malformed_title_never_leaks(tmp_path):
    raw=fixture_path().read_text().replace('</ArticleTitle>','<Abstract>DO_NOT_EXPORT</Abstract></ArticleTitle>',1)
    p=tmp_path/'bad-title.xml';p.write_text(raw);s=Store(tmp_path/'state')
    with pytest.raises(ValueError):s.ingest(p,1)
    assert not (s.root/'CURRENT').exists()
    for file in s.root.rglob('*.jsonl'):assert 'DO_NOT_EXPORT' not in file.read_text()

def test_book_history_and_electronic_identifier(tmp_path):
    p=tmp_path/'book.xml';p.write_text('''<PubmedArticleSet><PubmedBookArticle><BookDocument><PMID Version="1">900000051</PMID><ArticleIdList><ArticleId IdType="pubmed">900000051</ArticleId></ArticleIdList><Book><Publisher><PublisherName>Synthetic Press</PublisherName></Publisher><BookTitle>Synthetic book</BookTitle><PubDate><Year>2023</Year></PubDate><ELocationID EIdType="doi">10.1234/book2</ELocationID></Book></BookDocument><PubmedBookData><History><PubMedPubDate PubStatus="pubmed"><Year>2024</Year><Month>01</Month><Day>02</Day></PubMedPubDate></History><PublicationStatus>ppublish</PublicationStatus><ArticleIdList><ArticleId IdType="doi">10.1234/book2</ArticleId></ArticleIdList></PubmedBookData></PubmedBookArticle></PubmedArticleSet>''')
    s=Store(tmp_path/'state');s.ingest(p,1);assert verify_state(s)['final_articles']==1

def test_json_schema_types_fail_before_export(tmp_path):
    path,_=stage(tmp_path);items=[json.loads(x) for x in path.read_text().splitlines()]
    items[0]['record']['mesh'][0]['major']='true'
    path.write_text(''.join(json.dumps(x)+'\n' for x in items))
    with pytest.raises(VerificationError,match='schema'):json_inventory(path)

def test_annotated_identifier_corruption_detected(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1)
    a=[{'entityType':'Gene','namespace':'ncbi_gene','normalizedIdentifier':'672','rawIdentifier':'672','scope':'title'}]
    s.db.execute('INSERT INTO enrichment VALUES(?,?,?,?)',('900000001',json.dumps(a),'complete','2026-10-21'));s.db.commit();s.publish()
    g=s.graph();n=next(g.objects(None,K.annotation));g.set((n,K.normalizedIdentifier,Literal('wrong')))
    with pytest.raises(VerificationError,match='annotation'):verify_rdf(g,list(s.db.execute('SELECT * FROM articles')),list(s.db.execute('SELECT * FROM enrichment')))

def test_real_source_grant_acronym_declared(tmp_path):
    raw=fixture_path().read_text().replace('<Agency>Synthetic Funding Agency</Agency>','<Acronym>SYN</Acronym><Agency>Synthetic Funding Agency</Agency>',1)
    p=tmp_path/'grant.xml';p.write_text(raw);s=Store(tmp_path/'state');s.ingest(p,1)
    assert ontology_check(s.graph())['undeclared_used_terms']==0
    assert verify_state(s)['final_articles']==3

def test_entities_are_separate_typed_resources(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1)
    a=[{'entityType':'Gene','namespace':'ncbi_gene','normalizedIdentifier':'672','rawIdentifier':'672','scope':'title'}]
    s.db.execute('INSERT INTO enrichment VALUES(?,?,?,?)',('900000001',json.dumps(a),'complete','2026-10-21'));s.db.commit();s.publish()
    g=s.graph();n=next(g.objects(None,K.annotation));entity=g.value(n,K.entity)
    assert (entity,RDF.type,K.BiomedicalEntity) in g
    assert g.value(entity,K.normalizedIdentifier)==Literal('672')
    assert verify_state(s)['status']=='verified'

def test_missing_annotation_identifier_not_fabricated(tmp_path):
    s=Store(tmp_path);s.ingest(fixture_path(),1)
    a=[{'entityType':'Unknown','namespace':'unmapped:Unknown','normalizedIdentifier':'','rawIdentifier':'','scope':'unspecified'}]
    s.db.execute('INSERT INTO enrichment VALUES(?,?,?,?)',('900000001',json.dumps(a),'complete','2026-10-21'));s.db.commit();s.publish()
    g=s.graph();n=next(g.objects(None,K.annotation));assert g.value(n,K.entity) is None
    assert verify_state(s)['status']=='verified'

def test_changed_mapping_uses_new_immutable_generation(tmp_path,monkeypatch):
    s=Store(tmp_path);s.ingest(fixture_path(),1);old=(s.root/'CURRENT').read_text();old_bytes=(s.root/'snapshots'/old/'part-00000.ttl').read_bytes()
    monkeypatch.setattr('pubmed_kg.store.mapping_fingerprint',lambda:'new-contract')
    s.publish();new=(s.root/'CURRENT').read_text()
    assert new!=old and (s.root/'snapshots'/old/'part-00000.ttl').read_bytes()==old_bytes

def test_ontology_rdfs_inference_remains_shacl_consistent(tmp_path):
    from pyshacl import validate
    raw=fixture_path().read_text().replace('</MedlineCitation>','<CommentsCorrectionsList><CommentsCorrections RefType="RetractionIn"><RefSource>Excluded reference text</RefSource><PMID>900000099</PMID></CommentsCorrections></CommentsCorrectionsList></MedlineCitation>',1)
    p=tmp_path/'rdfs.xml';p.write_text(raw)
    s=Store(tmp_path/'state');s.ingest(p,1);g=s.graph();g.parse(ROOT/'fixtures/adapter.ttl')
    ok,_,report=validate(g,shacl_graph=str(ROOT/'shapes.ttl'),ont_graph=str(ROOT/'ontology.ttl'),inference='rdfs')
    assert ok,report

def test_console_entrypoint_redacts_provider_and_validation_text(monkeypatch,capsys):
    from pubmed_kg import cli
    def fail():raise ValueError('PRIVATE_BODY api_key=SECRET')
    monkeypatch.setattr(cli,'main',fail)
    assert cli.entrypoint()==1
    captured=capsys.readouterr()
    assert 'ValueError' in captured.err
    assert 'PRIVATE_BODY' not in captured.err and 'SECRET' not in captured.err

def test_crash_between_pointer_and_commit_blocks_reads_and_resumes(tmp_path,monkeypatch):
    s=Store(tmp_path/'state');s.ingest(fixture_path(),1)
    real_write=Path.write_text
    def fail_report(self,*a,**kw):
        if self.name=='03-rdf-verification.json':raise OSError('simulated crash after pointer publication')
        return real_write(self,*a,**kw)
    monkeypatch.setattr(Path,'write_text',fail_report)
    with pytest.raises(OSError):s.ingest(ROOT/'fixtures/update.xml',2)
    with pytest.raises(VerificationError,match='incomplete'):s.graph()
    monkeypatch.setattr(Path,'write_text',real_write)
    s.ingest(ROOT/'fixtures/update.xml',2)
    assert len(list(s.graph().subjects(RDF.type,K.Article)))==2
    assert verify_state(s)['status']=='verified'
