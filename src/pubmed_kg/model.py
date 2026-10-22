"""Content-address transformation contracts so changed mappings cannot reuse snapshots."""
import hashlib
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
def extraction_fingerprint():
    files=[ROOT/'src/pubmed_kg'/name for name in ['parser.py','metadata.py','relevance.py','audit.py','model.py']]+[ROOT/'record.schema.json',ROOT/'schema-contract.json',ROOT/'resources/device-mesh-2026.json']
    h=hashlib.sha256()
    for path in files:h.update(path.name.encode());h.update(path.read_bytes())
    return h.hexdigest()
def mapping_fingerprint():
    h=hashlib.sha256(extraction_fingerprint().encode())
    for path in [ROOT/'src/pubmed_kg/graph.py',ROOT/'src/pubmed_kg/store.py',ROOT/'src/pubmed_kg/corpus.py',ROOT/'src/pubmed_kg/enrichment.py',ROOT/'src/pubmed_kg/fda.py',ROOT/'src/pubmed_kg/device_links.py',ROOT/'ontology.ttl',ROOT/'shapes.ttl',ROOT/'integration/fda/ontology.ttl']:
        h.update(path.name.encode());h.update(path.read_bytes())
    return h.hexdigest()
