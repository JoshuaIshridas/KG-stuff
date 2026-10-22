"""Loss-auditable safe structured metadata; unknown XML cannot disappear silently."""
import json
from collections import Counter
from pathlib import Path
CONTRACT=json.loads((Path(__file__).resolve().parents[2]/'schema-contract.json').read_text())
EXCLUDED=CONTRACT['excluded_subtrees']
KNOWN=set(CONTRACT['known_elements'])
INLINE=set(CONTRACT['inline_elements'])
TEXT=set(CONTRACT['text_containers'])

def fields(root):
    result=[]
    def visit(e,path):
        if not isinstance(e.tag,str):return
        if e.tag in EXCLUDED:return
        if e.tag.startswith('{http://www.w3.org/1998/Math/MathML}'):return
        if e.tag not in KNOWN:raise ValueError('unreviewed XML element: '+e.tag)
        if e.tag in INLINE:return
        if e.tag in TEXT and any(isinstance(c.tag,str) and c.tag not in INLINE and not c.tag.startswith('{http://www.w3.org/1998/Math/MathML}') for c in e.iterdescendants()):raise ValueError('unexpected child in metadata text field')
        value=''.join(e.itertext()).strip() if e.tag in TEXT else (e.text or '').strip()
        item={'path':path,'element':e.tag,'value':value,'attributes':dict(sorted(e.attrib.items()))}
        # Structural nodes are retained too, preserving parent and attribute context.
        result.append(item)
        counts=Counter()
        for child in e:
            if not isinstance(child.tag,str):continue
            counts[child.tag]+=1
            visit(child,path+'/'+child.tag+'['+str(counts[child.tag])+']')
    visit(root,root.tag+'[1]')
    return result

def add_metadata(out,e):
    out['schema_version']=2
    out['record_kind']='book' if e.tag=='PubmedBookArticle' else 'article'
    out['metadata_fields']=fields(e)
    citation=e.find('MedlineCitation')
    if citation is None:citation=e.find('BookDocument')
    out['keywords']=[dict(value=''.join(k.itertext()).strip(),major=k.get('MajorTopicYN')=='Y',owner=k.getparent().get('Owner','')) for k in citation.findall('KeywordList/Keyword')]
    out['chemicals']=[dict(identifier=x.find('NameOfSubstance').get('UI',''),label=''.join(x.find('NameOfSubstance').itertext()).strip(),registry=x.findtext('RegistryNumber','')) for x in citation.findall('ChemicalList/Chemical') if x.find('NameOfSubstance') is not None]
    out['supplementary']=[dict(identifier=x.get('UI',''),entityType=x.get('Type','unknown'),label=''.join(x.itertext()).strip()) for x in citation.findall('SupplMeshList/SupplMeshName')]
    out['corrections']=[dict(relation=x.get('RefType','unknown'),pmid=x.findtext('PMID','')) for x in citation.findall('CommentsCorrectionsList/CommentsCorrections')]
    out['references']=[dict(scheme=x.get('IdType','unknown'),value=''.join(x.itertext()).strip()) for x in e.findall('.//Reference/ArticleIdList/ArticleId')]
    out['languages']=[x.text or '' for x in citation.findall('.//Language')]
    out['publication_status']=e.findtext('PubmedData/PublicationStatus') or e.findtext('PubmedBookData/PublicationStatus') or ''
    return out
