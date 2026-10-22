"""Controlled FDA-name candidates and explicit identifier matching, without snippets.

This is a device-catalog lookup, not a biomedical chemical/disease/gene recognizer.
PubTator3 remains the source of biomedical annotations.
"""
import gzip
import hashlib
import json
import os
import re
from pathlib import Path

from lxml import etree
from rdflib import Graph, Literal, RDF, URIRef
from rdflib.compare import isomorphic

from .adapter import candidates
from .corpus import atomic_json, sha256
from .graph import K, P, check, iri
from .parser import article, book, text
from .store import now
from .verification import ontology_check


def name_index(external, config):
    allowed = {name.casefold() for name in config.get('allow_single_token_names', [])}
    index = {}
    for predicate in config.get('name_predicates', []):
        for resource, label in external.subject_objects(URIRef(predicate)):
            if not isinstance(resource, URIRef) or not isinstance(label, Literal):
                continue
            term = ' '.join(str(label).split()).casefold()
            # Reject short ordinary words and ambiguous single-token names by default.
            if len(term) < 8 or (len(re.findall(r'\w+', term)) < 2 and term not in allowed):
                continue
            index.setdefault(term, set()).add(resource)
    return [(term, re.compile(r'(?<!\w)' + r'\s+'.join(re.escape(x) for x in term.split()) + r'(?!\w)', re.I), targets)
            for term, targets in sorted(index.items())]


def link_source(path, external_path, config, output):
    path, output = Path(path), Path(output)
    external = Graph().parse(external_path, format='turtle')
    index = name_index(external, config)
    source_sha, external_sha = sha256(path), sha256(external_path)
    config_sha = hashlib.sha256(json.dumps(config, sort_keys=True).encode()).hexdigest()
    output.parent.mkdir(parents=True, exist_ok=True)
    temporary = output.with_suffix(output.suffix + '.part')
    articles = assertions = 0
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rb') as source, temporary.open('w') as sink:
        for _, element in etree.iterparse(source, events=('end',), tag=('PubmedArticle', 'PubmedBookArticle'), resolve_entities=False, no_network=True):
            record = book(element) if element.tag == 'PubmedBookArticle' else article(element)
            article_uri = iri('article', record['pmid'])
            g = Graph()
            for i, detection in enumerate(record['detections'] + [d for d in record['identifiers'] if d['valid']]):
                node = iri('link-evidence', source_sha + '/' + record['pmid'] + '/' + str(i))
                g.add((article_uri, K.detection, node))
                g.add((node, RDF.type, K.IdentifierDetection))
                for key in ['scheme', 'value', 'location']:
                    g.add((node, K[key], Literal(detection[key])))
                g.add((node, K.method, Literal(detection.get('method', 'structured-identifier'))))
            candidates(g, external, config)
            identifier_targets = {g.value(n, K.device) for n in g.objects(article_uri, K.link)}
            texts = [('title', record['title'])] + [('abstract', text(e)) for e in element.findall('.//AbstractText')]
            for location, value in texts:
                for term, pattern, targets in index:
                    if not pattern.search(value):
                        continue
                    for target in sorted(targets, key=str):
                        if target in identifier_targets:
                            continue
                        key = '|'.join([source_sha, external_sha, config_sha, record['pmid'], str(target), term, location])
                        node = iri('name-match', hashlib.sha256(key.encode()).hexdigest())
                        g.add((article_uri, K.link, node))
                        g.add((node, RDF.type, K.LinkAssertion))
                        for predicate, obj in [('device', target), ('status', Literal('candidate')),
                                               ('method', Literal('configured-catalog-name-exact')),
                                               ('confidence', Literal('name-only-review-required')),
                                               ('scheme', Literal('device-name')), ('value', Literal(term)),
                                               ('evidenceLocation', Literal(location))]:
                            g.add((node, K[predicate], obj))
            links = list(g.objects(article_uri, K.link))
            if links:
                source_node = iri('source', source_sha)
                catalog_node = iri('source', external_sha)
                for node, filename, digest in [(source_node, path.name, source_sha), (catalog_node, Path(external_path).name, external_sha)]:
                    g.add((node, RDF.type, K.Source))
                    g.add((node, K.filename, Literal(filename)))
                    g.add((node, K.sha256, Literal(digest)))
                for node in links:
                    g.add((node, P.wasDerivedFrom, source_node))
                    g.add((node, P.wasDerivedFrom, catalog_node))
                # Retain only evidence attached to a candidate, not unrelated mentions.
                used = {g.value(link, K.detection) for link in links}
                for detection in list(g.objects(article_uri, K.detection)):
                    if detection not in used:
                        g.remove((article_uri, K.detection, detection))
                        g.remove((detection, None, None))
                check(g)
                ontology_check(g)
                serialized = ''.join(sorted(g.serialize(format='nt').splitlines(keepends=True)))
                if not isomorphic(g, Graph().parse(data=serialized, format='turtle')):
                    raise ValueError('link Turtle round-trip failed')
                sink.write(serialized)
                assertions += len(links)
            articles += 1
            element.clear()
            while element.getprevious() is not None:
                del element.getparent()[0]
    os.replace(temporary, output)
    report = dict(checked_at=now(), source_sha256=source_sha, external_sha256=external_sha,
                  config_sha256=config_sha, articles_scanned=articles, candidate_assertions=assertions,
                  output_sha256=sha256(output), verified_identity_links=0,
                  review='All emitted matches require review; product codes describe categories. No text snippets exported.')
    atomic_json(output.with_suffix(output.suffix + '.json'), report)
    return report
