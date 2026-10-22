"""NLM equipment-topic screening; all records retained, no identity assertions."""
import json
import re
from functools import lru_cache
from pathlib import Path
from lxml import etree
TERMS=('medical device','infusion pump','catheter','pacemaker','ventilator','prosthesis','prostheses','stent','implantable device','surgical mesh','diagnostic equipment')

@lru_cache(maxsize=1)
def default_vocabulary():
    path=Path(__file__).resolve().parents[2]/'resources/device-mesh-2026.json'
    return json.loads(path.read_text())['descriptors']

def tokenize(value):return re.findall(r'[\w]+',value.casefold(),flags=re.UNICODE)

@lru_cache(maxsize=4)
def lexicon(serialized):
    vocabulary=json.loads(serialized);result={}
    for ui,entry in vocabulary.items():
        for term in [entry.get('label','')]+entry.get('terms',[]):
            tokens=tuple(tokenize(term))
            if not tokens or (len(tokens)==1 and len(tokens[0])<5):continue
            result.setdefault(tokens,set()).add(ui)
    return result,sorted({len(k) for k in result})

def assess(article,vocabulary=None,transient=()):
    vocabulary=default_vocabulary() if vocabulary is None else vocabulary
    signals=[]
    for d in article.get('mesh',[]):
        if d.get('id') in vocabulary:signals.append(dict(method='mesh-equipment-tree',identifier=d['id'],location='MeSH',classification='indexed-equipment-topic'))
    for d in article.get('detections',[]):
        if d['scheme'] in ('510k','pma','product-code'):signals.append(dict(method=d['method'],identifier=d['value'],location=d['location'],classification='explicit-regulatory-mention'))
    source_texts=[('title',article['title'])]+[('keyword',k['value']) for k in article.get('keywords',[])]+list(transient)
    terms,lengths=lexicon(json.dumps(vocabulary,sort_keys=True))
    for location,value in source_texts:
        words=tokenize(value)
        for length in lengths:
            found=set()
            for i in range(len(words)-length+1):found.update(terms.get(tuple(words[i:i+length]),()))
            for ui in sorted(found):signals.append(dict(method='mesh-device-term-screen',identifier=ui,location=location,classification='candidate-topic'))
        # Additional explicit general terms; no evidence snippets leave this function.
        for term in TERMS:
            if re.search(r'(?<!\w)'+re.escape(term)+r's?(?!\w)',value,re.I):signals.append(dict(method='device-term-screen',identifier=term,location=location,classification='candidate-topic'))
    # A descriptor can have aliases of different lengths; retain one identical signal.
    unique={json.dumps(s,sort_keys=True):s for s in signals};signals=list(unique.values())
    return {'status':'candidate' if signals else 'unassessed','signals':signals,'excludes_record':False}

def mesh_index(path,output):
    result={}
    context=etree.iterparse(str(path),events=('end',),tag='DescriptorRecord',resolve_entities=False,no_network=True)
    for _,e in context:
        trees=[x.text for x in e.findall('TreeNumberList/TreeNumber') if x.text]
        if any(t=='E07' or t.startswith('E07.') for t in trees):
            result[e.findtext('DescriptorUI')]={'label':e.findtext('DescriptorName/String'),'tree_numbers':trees,'terms':sorted(set(x.text for x in e.findall('ConceptList/Concept/TermList/Term/String') if x.text))}
        e.clear()
        while e.getprevious() is not None:del e.getparent()[0]
    if 'D004864' not in result:raise ValueError('Equipment and Supplies root missing from vocabulary')
    Path(output).write_text(json.dumps({'source':str(Path(path).name),'descriptors':result},indent=2));return len(result)
