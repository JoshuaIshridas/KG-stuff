#!/usr/bin/env python3
"""Build an attributed RDF schema snapshot and bounded real FDA sample offline."""
import json
from pathlib import Path
import sys
from rdflib import Graph, Literal, RDF, RDFS, URIRef
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'src'))
from pubmed_kg.graph import write

root = Path(__file__).resolve().parents[1] / 'integration/fda'
data = json.loads((root / 'registry-schema.json').read_text())
schema = Graph()
for uri, label, description, *_ in data['schema']['classes']['data']:
    node = URIRef(uri)
    schema.add((node, RDF.type, RDFS.Class))
    schema.add((node, RDFS.label, Literal(label)))
    schema.add((node, RDFS.comment, Literal(description)))
for uri, label, description, *_ in data['schema']['predicates']['data']:
    node = URIRef(uri)
    schema.add((node, RDF.type, RDF.Property))
    schema.add((node, RDFS.label, Literal(label)))
    schema.add((node, RDFS.comment, Literal(description)))
# Intentionally do not invent OWL axioms from the registry's tabular summaries.
write(schema, root / 'fda-ontology.ttl')
sample = Graph()
for filename in ['live-verification.json', 'connected-sample.json', 'category-sample.json']:
    document = json.loads((root / filename).read_text())
    for row in document.get('result', document)['rows']:
        if row['kind'] == 'iri':
            value = URIRef(row['o'])
        else:
            value = Literal(row['o'], lang=row.get('lang') or None,
                            datatype=URIRef(row['datatype']) if row.get('datatype') and not row.get('lang') else None)
        sample.add((URIRef(row['s']), URIRef(row['p']), value))
write(sample, root / 'fda-sample.ttl')
assert len(Graph().parse(root / 'fda-sample.ttl')) == len(sample)
assert len(Graph().parse(root / 'fda-ontology.ttl')) == len(schema)
print(json.dumps(dict(schema_triples=len(schema), sample_triples=len(sample), synthetic=False,
                     scope='Registry-derived RDF schema snapshot and bounded live lexical data extract; not an original ontology release or FDA dump.')))
