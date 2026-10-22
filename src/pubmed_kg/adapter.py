"""Read-only schema inspection and candidate construction; no automatic verification."""
import json
from rdflib import URIRef,Literal,RDF
from .graph import K,iri
from .network import Client

def inspect(config):
    endpoint=config.get('endpoint')
    if not endpoint:raise ValueError('actual endpoint required')
    params={'query':'SELECT ?p (COUNT(*) AS ?n) WHERE { ?s ?p ?o } GROUP BY ?p ORDER BY DESC(?n) LIMIT 30','format':'json'}
    if config.get('graph'):params['default-graph-uri']=config['graph']
    with Client(rate=1).get(endpoint,params=params,headers={'Accept':'application/sparql-results+json'}) as r:return r.json()

def candidates(g,external,config):
    predicates=config.get('identifier_predicates',{})
    for article,detection in list(g.subject_objects(K.detection))+list(g.subject_objects(K.identifier)):
        scheme=str(g.value(detection,K.scheme));value=g.value(detection,K.value)
        if (article,K.identifier,detection) in g and g.value(detection,K.valid)!=Literal(True):continue
        if scheme not in predicates:continue
        # Explicit structured identifier equality only; never fuzzy names.
        for device in external.subjects(URIRef(predicates[scheme]),Literal(str(value))):
            n=iri('match',str(article)+'|'+str(detection)+'|'+str(device))
            g.add((article,K.link,n));g.add((n,RDF.type,K.LinkAssertion))
            confidence='category-match-review-required' if scheme=='product-code' else 'identifier-match-review-required'
            for p,v in [('device',device),('detection',detection),('status',Literal('candidate')),('method',Literal('configured-exact-identifier')),('confidence',Literal(confidence)),('scheme',Literal(scheme)),('evidenceLocation',g.value(detection,K.location))]:
                if v is not None:g.add((n,K[p],v))
    return g
