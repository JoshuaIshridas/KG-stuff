import hashlib
import json
from urllib.parse import quote
from rdflib import Graph, Namespace, URIRef, Literal, RDF, RDFS
from pyshacl import validate
from pathlib import Path
K=Namespace('https://example.org/pubmed-kg/v1/')
P=Namespace('http://www.w3.org/ns/prov#')
ROOT=Path(__file__).resolve().parents[2]
def iri(kind,value): return K[kind+'/'+quote(str(value),safe='')]
def build(rows, annotations=()):
    g=Graph(); g.bind('k',K); g.bind('prov',P)
    def add(s,p,v):
        if v is not None and v!='': g.add((s,K[p],v if isinstance(v,URIRef) else Literal(v)))
    for pmid,payload,source,order in rows:
        a=json.loads(payload); u=iri('article',pmid)
        g.add((u,RDF.type,K.Article));
        if a.get('record_kind')=='book':g.add((u,RDF.type,K.BookArticle))
        add(u,'pmid',pmid); add(u,'title',a['title']); add(u,'sourceOrder',order); add(u,'sourceVersion',a['version'])
        s=iri('source',source); g.add((s,RDF.type,K.Source)); add(s,'filename',source)
        g.add((u,P.wasDerivedFrom,s))
        def node(kind,index):
            n=iri(kind,pmid+'/'+str(index)); add(u,kind,n)
            classes={'date':'PublicationDate','identifier':'Identifier','detection':'IdentifierDetection','mesh':'MeshAssignment','grant':'Grant','publicationType':'PublicationTypeAssignment','keyword':'KeywordAssignment','chemical':'ChemicalAssignment','supplementary':'SupplementaryAssignment','correction':'CorrectionLink','reference':'ReferenceIdentifier','metadataField':'SourceField','deviceSignal':'DeviceRelevanceSignal'}
            if kind in classes:g.add((n,RDF.type,K[classes[kind]]))
            return n
        j=a['journal']; jid=next((v for t,v,*_ in j['ids'] if t=='NlmUniqueID'),None)
        if a.get('record_kind')!='book' or j['name'] or j['ids']:
            ju=iri('journal',jid or 'unidentified/'+pmid);g.add((ju,RDF.type,K.Journal));add(ju,'name',j['name']);add(u,'journal',ju)
            for t,v,*rest in j['ids']:add(ju,t,v)
        for i,d in enumerate(a['dates']):
            n=node('date',i);add(n,'kind',d['kind']);add(n,'meaning',d['meaning'])
            for key,v in d['parts'].items(): add(n,key,v)
            add(n,'precision','day' if 'Day' in d['parts'] else 'month' if 'Month' in d['parts'] else 'year' if 'Year' in d['parts'] else 'text')
        for i,d in enumerate(a['identifiers']):
            n=node('identifier',i)
            for key,v in d.items(): add(n,key,v)
            if d['scheme']=='nct' and d['valid']:
                trial=iri('trial',d['value']);add(u,'trial',trial);g.add((trial,RDF.type,K.ClinicalTrial));g.add((trial,RDFS.seeAlso,URIRef('https://clinicaltrials.gov/study/'+d['value'])))
        for i,d in enumerate(a['detections']):
            n=node('detection',i)
            for key,v in d.items(): add(n,key,v)
            if d['scheme']=='nct':
                trial=iri('trial',d['value']);add(u,'trial',trial);g.add((trial,RDF.type,K.ClinicalTrial));g.add((trial,RDFS.seeAlso,URIRef('https://clinicaltrials.gov/study/'+d['value'])))
        for i,d in enumerate(a['mesh']):
            n=node('mesh',i);add(n,'descriptor',URIRef('http://id.nlm.nih.gov/mesh/'+d['id']) if d['id'] else None);add(n,'label',d['label']);add(n,'major',d['major'])
            for qidx,q in enumerate(d['qualifiers']):
                qn=iri('qualifier',pmid+'/'+str(i)+'/'+str(qidx));add(n,'qualifier',qn);g.add((qn,RDF.type,K.MeshQualifierAssignment));add(qn,'qualifierUI',q['id']);add(qn,'label',q['label']);add(qn,'major',q['major'])
        for kind, values in [('grant',a['grants']),('publicationType',a['types'])]:
            for i,d in enumerate(values):
                n=node(kind,i)
                for key,v in d.items(): add(n,key,v)
        for kind,key in [('keyword','keywords'),('chemical','chemicals'),('supplementary','supplementary'),('correction','corrections'),('reference','references')]:
            for i,d in enumerate(a.get(key,[])):
                n=node(kind,i)
                for prop,value in d.items():add(n,prop if prop!='identifier' else 'identifierValue',value)
                if kind=='correction' and d.get('pmid','').isascii() and d.get('pmid','').isdigit() and int(d['pmid']):add(n,'relatedArticle',iri('article',d['pmid']))
        for i,field in enumerate(a.get('metadata_fields',[])):
            n=node('metadataField',i)
            from .audit import canonical
            for prop,value in [('path',field['path']),('element',field['element']),('fieldValue',field['value']),('attributes',canonical(field['attributes']))]:g.add((n,K[prop],Literal(value)))
        add(u,'recordKind',a.get('record_kind','article'));add(u,'publicationStatus',a.get('publication_status',''))
        for language in a.get('languages',[]):add(u,'language',language)
        relevance=a.get('device_relevance',{'status':'unassessed','signals':[]})
        add(u,'deviceRelevanceStatus',relevance['status'])
        for i,signal in enumerate(relevance['signals']):
            n=node('deviceSignal',i)
            for prop,value in signal.items():add(n,prop if prop!='identifier' else 'identifierValue',value)
    for pmid,payload,status,retrieved in annotations:
        u=iri('article',pmid);add(u,'enrichmentStatus',status)
        for i,d in enumerate(json.loads(payload)):
            n=iri('annotation',pmid+'/'+str(i));add(u,'annotation',n);g.add((n,RDF.type,K.Annotation));add(n,'source','PubTator3');add(n,'retrieved',retrieved)
            for key,v in d.items():add(n,key,v)
            if d.get('normalizedIdentifier'):
                entity=iri('entity',d.get('namespace','unmapped')+'|'+d.get('entityType','unknown')+'|'+str(d['normalizedIdentifier']))
                add(n,'entity',entity);g.add((entity,RDF.type,K.BiomedicalEntity))
                for prop in ['namespace','entityType','normalizedIdentifier']:add(entity,prop,d.get(prop))
    return g

def check(g):
    ok,_,report=validate(g,shacl_graph=str(ROOT/'shapes.ttl'))
    if not ok: raise ValueError(report)
    return len(g)
def write(g,path):
    # Sorted N-Triples is also valid Turtle; no blank nodes, stable byte order.
    path.write_text(''.join(sorted(g.serialize(format='nt').splitlines(keepends=True))))
