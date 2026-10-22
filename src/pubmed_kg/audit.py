"""Mandatory independent XML count -> JSONL verify -> serialized RDF verify gates."""
import gzip
import hashlib
import json
import os
import re
from collections import Counter
from pathlib import Path
from lxml import etree
from jsonschema import Draft202012Validator, ValidationError
from rdflib import Graph, Literal, RDF
from .metadata import CONTRACT,EXCLUDED,KNOWN,INLINE,TEXT
from .parser import records
from .relevance import assess

JSON_VALIDATOR=Draft202012Validator(json.loads((Path(__file__).resolve().parents[2]/'record.schema.json').read_text()))

class VerificationError(ValueError):pass

def canonical(value):return json.dumps(value,sort_keys=True,ensure_ascii=False,separators=(',',':'))
def digest_event(h,pmid,kind,fields):
    h.update(canonical([pmid,kind,fields]).encode());h.update(b'\n')

def independent_fields(root):
    """Separate flat iterator/path calculation, independent of recursive extraction."""
    result=[];excluded=Counter()
    for e in root.iter():
        if not isinstance(e.tag,str):continue
        ancestors=list(e.iterancestors());ancestors=ancestors[:ancestors.index(root)+1] if root in ancestors else []
        chain=[e]+ancestors
        if any(x.tag in EXCLUDED for x in chain):
            excluded[e.tag]+=1;continue
        if any(x.tag in INLINE or (isinstance(x.tag,str) and x.tag.startswith('{http://www.w3.org/1998/Math/MathML}')) for x in chain):continue
        if e.tag not in KNOWN:raise VerificationError('unknown XML element requires policy review: '+e.tag)
        segments=[]
        for node in reversed(chain):
            index=1+sum(1 for prev in node.itersiblings(preceding=True) if prev.tag==node.tag) if node is not root else 1
            segments.append(node.tag+'['+str(index)+']')
        value=''.join(e.itertext()).strip() if e.tag in TEXT else (e.text or '').strip()
        result.append(dict(path='/'.join(segments),element=e.tag,value=value,attributes=dict(sorted(e.attrib.items()))))
    return result,excluded

def source_inventory(path):
    counts=Counter();tags=Counter();excluded=Counter();h=hashlib.sha256();pmids=[]
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rb') as f:
        context=etree.iterparse(f,events=('start','end'),resolve_entities=False,no_network=True,huge_tree=False)
        root_seen=False
        for event,e in context:
            if event=='start':
                if not root_seen:
                    if e.tag not in ('PubmedArticleSet','PubmedBookArticleSet'):raise VerificationError('unexpected source root')
                    root_seen=True
                elif e.getparent() is not None and e.getparent().tag in ('PubmedArticleSet','PubmedBookArticleSet') and e.tag not in ('PubmedArticle','PubmedBookArticle','DeleteCitation'):
                    raise VerificationError('unsupported record element')
                continue
            if e.tag in ('PubmedArticle','PubmedBookArticle'):
                kind='book' if e.tag=='PubmedBookArticle' else 'article'
                path_id='BookDocument/PMID' if kind=='book' else 'MedlineCitation/PMID'
                pmid=(e.findtext(path_id) or '').strip()
                if not pmid.isascii() or not pmid.isdigit() or not int(pmid):raise VerificationError('missing/invalid source PMID')
                fs,omitted=independent_fields(e);excluded.update(omitted);tags.update(x['element'] for x in fs)
                counts[kind]+=1;counts['metadata_nodes']+=len(fs);counts['metadata_attributes']+=sum(len(x['attributes']) for x in fs)
                digest_event(h,pmid,kind,fs)
                # Only retain an optional small targeted set; global identity is a stream hash.
                if len(pmids)<1000:pmids.append(pmid)
                else:pmids=[];counts['large_source']=1
            elif e.tag=='DeleteCitation':
                for p in e.findall('PMID'):
                    pmid=(p.text or '').strip()
                    if not pmid.isascii() or not pmid.isdigit() or not int(pmid):raise VerificationError('invalid deletion PMID')
                    counts['delete']+=1;digest_event(h,pmid,'delete',[])
            else:continue
            e.clear()
            while e.getprevious() is not None:del e.getparent()[0]
    if not sum(counts[k] for k in ('article','book','delete')):raise VerificationError('source contains no supported record events')
    return {'counts':dict(counts),'metadata_elements':dict(tags),'excluded_elements':dict(excluded),'event_sha256':h.hexdigest(),'small_source_pmids':pmids if not counts['large_source'] else None,'policy_version':CONTRACT['version']}

def json_inventory(path):
    counts=Counter();tags=Counter();h=hashlib.sha256()
    with Path(path).open() as f:
        for line in f:
            item=json.loads(line)
            try:JSON_VALIDATOR.validate(item)
            except ValidationError:raise VerificationError('JSON schema validation failed') from None
            if set(item)!=set(['pmid','operation','record']):raise VerificationError('invalid JSON event envelope')
            pmid=item['pmid'];a=item['record']
            if not isinstance(pmid,str) or not pmid.isascii() or not pmid.isdigit() or not int(pmid):raise VerificationError('invalid JSON PMID')
            if item['operation']=='delete':
                if a is not None:raise VerificationError('deletion contains article')
                counts['delete']+=1;digest_event(h,pmid,'delete',[]);continue
            if item['operation']!='upsert' or not isinstance(a,dict) or a.get('pmid')!=pmid:raise VerificationError('invalid JSON article')
            required={'schema_version','record_kind','pmid','title','version','journal','identifiers','dates','mesh','grants','types','detections','metadata_fields','keywords','chemicals','supplementary','corrections','references','languages','publication_status','device_relevance'}
            if set(a)!=required:raise VerificationError('JSON schema drift')
            if a['schema_version']!=2 or a['record_kind'] not in ('article','book'):raise VerificationError('invalid schema version/kind')
            for key in ['metadata_fields','identifiers','dates','mesh','grants','types','detections','keywords','chemicals','supplementary','corrections','references','languages']:
                if not isinstance(a[key],list):raise VerificationError('invalid collection '+key)
            fs=a['metadata_fields'];paths=set()
            for field in fs:
                if set(field)!=set(['path','element','value','attributes']) or field['element'] not in KNOWN or field['element'] in EXCLUDED:raise VerificationError('invalid/forbidden JSON metadata')
                if field['path'] in paths:raise VerificationError('duplicate metadata path')
                paths.add(field['path'])
                if any(segment.split('[')[0] in EXCLUDED for segment in field['path'].split('/')):raise VerificationError('excluded source text in JSON')
            by_path={field['path']:field for field in fs}
            primary=('PubmedBookArticle[1]/BookDocument[1]' if a['record_kind']=='book' else 'PubmedArticle[1]/MedlineCitation[1]')
            primary_id=by_path.get(primary+'/PMID[1]')
            if not primary_id or primary_id['value']!=pmid or primary_id['attributes'].get('Version','1')!=a['version']:raise VerificationError('primary identifier/version mapping mismatch')
            title_path=primary+('/ArticleTitle[1]' if a['record_kind']=='book' else '/Article[1]/ArticleTitle[1]')
            title=by_path.get(title_path)
            if a['record_kind']=='book' and (not title or not title['value']):title=by_path.get(primary+'/Book[1]/BookTitle[1]')
            if (title['value'] if title else '')!=a['title']:raise VerificationError('title mapping mismatch')
            expected_collections={'mesh':'MeshHeading','keywords':'Keyword','chemicals':'Chemical','supplementary':'SupplMeshName','corrections':'CommentsCorrections','types':'PublicationType'}
            for key,tag in expected_collections.items():
                if len(a[key])!=sum(field['element']==tag for field in fs):raise VerificationError('normalized collection mapping mismatch: '+key)
            verify_semantic_json(a,by_path)
            counts[a['record_kind']]+=1;counts['metadata_nodes']+=len(fs);counts['metadata_attributes']+=sum(len(x['attributes']) for x in fs);tags.update(x['element'] for x in fs)
            digest_event(h,pmid,a['record_kind'],fs)
    return {'counts':dict(counts),'metadata_elements':dict(tags),'event_sha256':h.hexdigest()}

def reconcile(expected,actual):
    for key in ['article','book','delete','metadata_nodes','metadata_attributes']:
        if expected['counts'].get(key,0)!=actual['counts'].get(key,0):raise VerificationError('count reconciliation failed: '+key)
    if expected['event_sha256']!=actual['event_sha256'] or expected['metadata_elements']!=actual['metadata_elements']:raise VerificationError('source to JSON identity/content reconciliation failed')

def prepare(path,folder,vocabulary=None,expected_pmids=None,expected_count=None):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    source=source_inventory(path)
    (folder/'01-source-count.json').write_text(json.dumps(source,indent=2))
    if expected_count is not None and sum(source['counts'].get(k,0) for k in ['article','book'])!=expected_count:raise VerificationError('declared source count mismatch')
    if expected_pmids is not None and (source['small_source_pmids'] is None or set(source['small_source_pmids'])!=set(expected_pmids)):raise VerificationError('requested PMID set mismatch')
    output=folder/'records.jsonl';temp=folder/'records.jsonl.part'
    with temp.open('w') as f:
        for pmid,a in records(path,vocabulary):
            if a is not None and 'device_relevance' not in a:a['device_relevance']=assess(a,vocabulary)
            f.write(canonical(dict(pmid=pmid,operation='delete' if a is None else 'upsert',record=a))+'\n')
    actual=json_inventory(temp);reconcile(source,actual);os.replace(temp,output)
    from .model import extraction_fingerprint
    report={'implementation_sha256':extraction_fingerprint(),'workflow':['verify-source-count','write-json','verify-json'],'status':'json_verified','source':source,'json':actual,'eligible_metadata_retention':1.0,'record_event_retention':1.0,'excluded_policy':EXCLUDED,'device_recall':'not_measured','pubmed_universe_coverage':'not_measured'}
    (folder/'02-json-verification.json').write_text(json.dumps(report,indent=2))
    return output,report

def verify_rdf(g,rows,annotations=()):
    """Check per-record values/cardinalities against JSON, not merely triple totals."""
    from .graph import K,iri
    article_ids={str(g.value(u,K.pmid)) for u in g.subjects(RDF.type,K.Article)}
    expected_ids={r[0] for r in rows}
    if article_ids!=expected_ids:raise VerificationError('JSON to RDF PMID mismatch')
    total=0
    for pmid,payload,source,order in rows:
        a=json.loads(payload);u=iri('article',pmid)
        for pred,value in [('title',a['title']),('sourceOrder',order),('sourceVersion',a['version'])]:
            if value and g.value(u,K[pred])!=Literal(value):raise VerificationError('RDF article field mismatch: '+pred)
        links={'date':'dates','identifier':'identifiers','detection':'detections','mesh':'mesh','grant':'grants','publicationType':'types','keyword':'keywords','chemical':'chemicals','supplementary':'supplementary','correction':'corrections','reference':'references','metadataField':'metadata_fields'}
        for predicate,key in links.items():
            if len(list(g.objects(u,K[predicate])))!=len(a.get(key,[])):raise VerificationError('RDF collection count mismatch: '+predicate)
        relevance=a['device_relevance']
        if g.value(u,K.deviceRelevanceStatus)!=Literal(relevance['status']):raise VerificationError('device relevance status mismatch')
        if len(list(g.objects(u,K.deviceSignal)))!=len(relevance['signals']):raise VerificationError('device screening signal count mismatch')
        for i,signal in enumerate(relevance['signals']):
            n=iri('deviceSignal',pmid+'/'+str(i))
            for prop,value in signal.items():
                if g.value(n,K['identifierValue' if prop=='identifier' else prop])!=Literal(value):raise VerificationError('device screening evidence mismatch')
        if a['record_kind']=='book' and not a['journal']['name'] and not a['journal']['ids'] and list(g.objects(u,K.journal)):raise VerificationError('book was assigned a fabricated journal')
        for i,field in enumerate(a.get('metadata_fields',[])):
            n=iri('metadataField',pmid+'/'+str(i));total+=1
            for predicate,value in [('path',field['path']),('element',field['element']),('fieldValue',field['value']),('attributes',canonical(field['attributes']))]:
                if g.value(n,K[predicate])!=Literal(value):raise VerificationError('RDF source metadata value mismatch')
        for predicate,key in [('identifier','identifiers'),('detection','detections'),('date','dates'),('grant','grants'),('publicationType','types'),('keyword','keywords'),('chemical','chemicals'),('supplementary','supplementary'),('correction','corrections'),('reference','references')]:
            for i,values in enumerate(a.get(key,[])):
                n=iri(predicate,pmid+'/'+str(i))
                flat=dict(values)
                if predicate=='date':flat.update(flat.pop('parts'))
                for prop,value in flat.items():
                    if value is not None and value!='' and g.value(n,K[prop if prop!='identifier' else 'identifierValue'])!=Literal(value):raise VerificationError('RDF normalized field mismatch: '+predicate+'/'+prop)
        for i,d in enumerate(a['mesh']):
            n=iri('mesh',pmid+'/'+str(i))
            if g.value(n,K.major)!=Literal(d['major']):raise VerificationError('MeSH major mismatch')
            if len(list(g.objects(n,K.qualifier)))!=len(d['qualifiers']):raise VerificationError('MeSH qualifier count mismatch')
    for pmid,payload,status,retrieved in annotations:
        u=iri('article',pmid)
        if g.value(u,K.enrichmentStatus)!=Literal(status):raise VerificationError('enrichment state mismatch')
        values=json.loads(payload)
        if len(list(g.objects(u,K.annotation)))!=len(values):raise VerificationError('annotation count mismatch')
        for i,annotation in enumerate(values):
            n=iri('annotation',pmid+'/'+str(i))
            if g.value(n,K.retrieved)!=Literal(retrieved):raise VerificationError('annotation retrieval provenance mismatch')
            for prop,value in annotation.items():
                if value is not None and value!='' and g.value(n,K[prop])!=Literal(value):raise VerificationError('annotation identifier/value mismatch')
    return {'status':'verified','articles':len(rows),'metadata_nodes':total}


def verify_semantic_json(a,fields):
    """Independent source-field -> normalized-field consistency checks."""
    expected=[]
    for path,field in fields.items():
        tag=field['element'];attrs=field['attributes'];raw=field['value']
        if tag=='ArticleId' and '/Reference[' not in path:
            location='BookArticleIdList' if a['record_kind']=='book' else 'ArticleIdList'
            expected.append((attrs.get('IdType','unknown').lower(),raw,location))
        elif tag=='ELocationID':expected.append((attrs.get('EIdType','unknown').lower(),raw,'ELocationID'))
        elif tag=='AccessionNumber':
            bank=path.split('/AccessionNumberList[')[0]
            name=fields.get(bank+'/DataBankName[1]',{}).get('value','')
            expected.append(('nct' if name.lower()=='clinicaltrials.gov' else 'databank:'+name,raw,'DataBankList'))
        elif tag=='OtherID':expected.append(('otherid:'+attrs.get('Source','unknown'),raw,'OtherID'))
    actual=[(v['scheme'],v['raw'],v['location']) for v in a['identifiers']]
    if Counter(x for x in expected if x[1])!=Counter(actual):raise VerificationError('source identifier occurrence mapping mismatch')
    for identifier in a['identifiers']:
        raw=identifier['raw'].strip();scheme=identifier['scheme']
        if scheme=='doi':
            value=re.sub(r'^(https?://(?:dx\.)?doi.org/|doi:\s*)','',raw,flags=re.I).lower()
            valid=bool(re.fullmatch(r'10\.\d{4,9}/\S+',value))
        elif scheme in ('pmid','pmc','nct','510k','pma'):
            patterns={'pmid':r'[1-9]\d*','pmc':r'PMC\d+','nct':r'NCT\d{8}','510k':r'K\d{6}','pma':r'P\d{6}'}
            value=raw.upper();valid=bool(re.fullmatch(patterns[scheme],value))
        else:value=raw;valid=bool(raw)
        if identifier['value']!=(value if valid else raw) or identifier['valid']!=valid:raise VerificationError('identifier normalization mismatch')
    expected_mesh=[]
    for path,field in fields.items():
        if field['element']!='MeshHeading':continue
        descriptor=fields.get(path+'/DescriptorName[1]')
        if descriptor is None:raise VerificationError('missing MeSH descriptor')
        qualifiers=[]
        for qpath,q in fields.items():
            if qpath.rsplit('/',1)[0]==path and q['element']=='QualifierName':qualifiers.append(dict(id=q['attributes'].get('UI'),label=q['value'],major=q['attributes'].get('MajorTopicYN')=='Y'))
        expected_mesh.append(dict(id=descriptor['attributes'].get('UI'),label=descriptor['value'],major=descriptor['attributes'].get('MajorTopicYN')=='Y',qualifiers=qualifiers))
    if a['mesh']!=expected_mesh:raise VerificationError('MeSH descriptor/qualifier normalization mismatch')
    for key,tag in [('grants','Grant')]:
        expected_values=[]
        for path,field in fields.items():
            if field['element']!=tag:continue
            children=[c for p,c in fields.items() if p.rsplit('/',1)[0]==path]
            if len({c['element'] for c in children})!=len(children):raise VerificationError('repeated grant subfield')
            expected_values.append({c['element']:c['value'] for c in children})
        if a[key]!=expected_values:raise VerificationError('grant mapping mismatch')
    expected_dates=[]
    for path,field in fields.items():
        tag=field['element'];kind=None
        if tag=='PubDate' and '/JournalIssue[' in path:kind='journal'
        elif tag=='PubDate' and '/Book[' in path:kind='book-publication'
        elif tag=='ArticleDate':kind='article'
        elif tag=='DateRevised':kind='revision'
        elif tag=='DateCompleted':kind='completed'
        elif tag=='PubMedPubDate':kind='history'
        elif a['record_kind']=='book' and tag in ('ContributionDate','BeginningDate','EndingDate'):kind={'ContributionDate':'contribution','BeginningDate':'book-beginning','EndingDate':'book-ending'}[tag]
        if kind:
            children=[c for p,c in fields.items() if p.rsplit('/',1)[0]==path]
            if len({c['element'] for c in children})!=len(children):raise VerificationError('duplicate date component')
            parts={c['element']:c['value'] for c in children}
            expected_dates.append(dict(kind=kind,meaning=field['attributes'].get('DateType') or field['attributes'].get('PubStatus') or kind,parts=parts))
    if Counter(canonical(d) for d in a['dates'])!=Counter(canonical(d) for d in expected_dates):raise VerificationError('source date mapping mismatch')
    expected_types=[dict(id=f['attributes'].get('UI'),label=f['value']) for f in fields.values() if f['element']=='PublicationType']
    if a['types']!=expected_types:raise VerificationError('publication type mapping mismatch')
    expected_keywords=[];expected_chemicals=[];expected_supplementary=[];expected_corrections=[]
    for path,field in fields.items():
        if field['element']=='Keyword':
            parent=fields[path.rsplit('/',1)[0]]
            expected_keywords.append(dict(value=field['value'],major=field['attributes'].get('MajorTopicYN')=='Y',owner=parent['attributes'].get('Owner','')))
        elif field['element']=='Chemical':
            substance=fields.get(path+'/NameOfSubstance[1]')
            if not substance:raise VerificationError('missing substance')
            expected_chemicals.append(dict(identifier=substance['attributes'].get('UI',''),label=substance['value'],registry=fields.get(path+'/RegistryNumber[1]',{}).get('value','')))
        elif field['element']=='SupplMeshName':expected_supplementary.append(dict(identifier=field['attributes'].get('UI',''),entityType=field['attributes'].get('Type','unknown'),label=field['value']))
        elif field['element']=='CommentsCorrections':expected_corrections.append(dict(relation=field['attributes'].get('RefType','unknown'),pmid=fields.get(path+'/PMID[1]',{}).get('value','')))
    for key,expected in [('keywords',expected_keywords),('chemicals',expected_chemicals),('supplementary',expected_supplementary),('corrections',expected_corrections)]:
        if a[key]!=expected:raise VerificationError('source semantic mapping mismatch: '+key)
