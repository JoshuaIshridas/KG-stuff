import gzip
import re
from lxml import etree
from .metadata import add_metadata
from .relevance import assess

def text(e):
    return ''.join(e.itertext()).strip() if e is not None else ''

def norm(scheme, value):
    value = value.strip()
    if scheme == 'doi':
        value = re.sub(r'^(https?://(dx\.)?doi.org/|doi:\s*)', '', value, flags=re.I).lower()
        return value if re.fullmatch(r'10\.\d{4,9}/\S+', value) else None
    patterns = {'pmid': r'[1-9]\d*', 'pmc': r'PMC\d+', 'nct': r'NCT\d{8}', '510k': r'K\d{6}', 'pma': r'P\d{6}'}
    if scheme not in patterns:return value or None
    return value.upper() if re.fullmatch(patterns[scheme], value.upper()) else None

def article(e,vocabulary=None):
    citation = e.find('MedlineCitation')
    if citation is None: raise ValueError('unsupported citation')
    pmid = norm('pmid', text(citation.find('PMID')))
    if not pmid: raise ValueError('invalid PMID')
    a = citation.find('Article')
    if a is None: raise ValueError('missing Article')
    out = dict(pmid=pmid, title=text(a.find('ArticleTitle')), version=citation.find('PMID').get('Version', '1'), identifiers=[], dates=[], mesh=[], grants=[], types=[], detections=[])
    def identifier(s, v, location):
        n = norm(s, v)
        if v: out['identifiers'].append(dict(scheme=s, raw=v, value=n or v, valid=bool(n), location=location))
    for x in e.findall('.//PubmedData/ArticleIdList/ArticleId'):
        identifier(x.get('IdType', 'unknown').lower(), text(x), 'ArticleIdList')
    for x in a.findall('ELocationID'):
        identifier(x.get('EIdType', 'unknown').lower(), text(x), 'ELocationID')
    for b in a.findall('DataBankList/DataBank'):
        for x in b.findall('AccessionNumberList/AccessionNumber'):
            identifier('nct' if text(b.find('DataBankName')).lower() == 'clinicaltrials.gov' else 'databank:'+text(b.find('DataBankName')), text(x), 'DataBankList')
    for x in citation.findall('OtherID'):
        identifier('otherid:'+x.get('Source','unknown'),text(x),'OtherID')
    j = a.find('Journal')
    out['journal'] = dict(name=text(j.find('Title')) if j is not None else '', ids=[(x.tag,text(x),x.get('IssnType','')) for x in (j.findall('ISSN') if j is not None else [])])
    for tag in ['NlmUniqueID','ISSNLinking']:
        v=text(citation.find('MedlineJournalInfo/'+tag))
        if v: out['journal']['ids'].append((tag,v,''))
    datepaths=[('journal',a.findall('Journal/JournalIssue/PubDate')),('article',a.findall('ArticleDate')),('revision',citation.findall('DateRevised')),('completed',citation.findall('DateCompleted')),('history',e.findall('PubmedData/History/PubMedPubDate'))]
    for kind, nodes in datepaths:
        for x in nodes:
            out['dates'].append(dict(kind=kind, meaning=x.get('DateType') or x.get('PubStatus') or kind, parts={y.tag:text(y) for y in x}))
    for x in citation.findall('MeshHeadingList/MeshHeading'):
        d=x.find('DescriptorName')
        if d is not None: out['mesh'].append(dict(id=d.get('UI'),label=text(d),major=d.get('MajorTopicYN')=='Y',qualifiers=[dict(id=q.get('UI'),label=text(q),major=q.get('MajorTopicYN')=='Y') for q in x.findall('QualifierName')]))
    for x in a.findall('GrantList/Grant'):
        out['grants'].append({c.tag:text(c) for c in x})
    out['types']=[dict(id=x.get('UI'),label=text(x)) for x in a.findall('PublicationTypeList/PublicationType')]
    # Text is transient; export only identifier values and evidence locations.
    out['detections']=detect_identifiers([('title',out['title'])]+[('abstract',text(x)) for x in a.findall('Abstract/AbstractText')+citation.findall('OtherAbstract/AbstractText')])
    return finish(out,e,vocabulary)

def records(path,vocabulary=None):
    opener=gzip.open if str(path).endswith('.gz') else open
    with opener(path,'rb') as f:
        context=etree.iterparse(f,events=('end',),tag=('PubmedArticle','DeleteCitation','PubmedBookArticle'),resolve_entities=False,no_network=True,huge_tree=False)
        for _, e in context:
            if e.tag=='DeleteCitation':
                for p in e.findall('PMID'):
                    v=norm('pmid',text(p))
                    if not v: raise ValueError('invalid deletion PMID')
                    yield v,None
            elif e.tag=='PubmedBookArticle':
                a=book(e,vocabulary);yield a['pmid'],a
            else:
                a=article(e,vocabulary); yield a['pmid'],a
            e.clear()
            while e.getprevious() is not None: del e.getparent()[0]


def book(e,vocabulary=None):
    citation=e.find('BookDocument')
    if citation is None:raise ValueError('missing BookDocument')
    pmid=norm('pmid',text(citation.find('PMID')))
    if not pmid:raise ValueError('invalid book PMID')
    title=text(citation.find('ArticleTitle')) or text(citation.find('Book/BookTitle'))
    out=dict(pmid=pmid,title=title,version=citation.find('PMID').get('Version','1'),journal={'name':'','ids':[]},identifiers=[],dates=[],mesh=[],grants=[],types=[],detections=[])
    for x in citation.findall('ArticleIdList/ArticleId')+e.findall('PubmedBookData/ArticleIdList/ArticleId'):
        scheme=x.get('IdType','unknown').lower();raw=text(x);value=norm(scheme,raw)
        out['identifiers'].append(dict(scheme=scheme,raw=raw,value=value or raw,valid=bool(value),location='BookArticleIdList'))
    out['types']=[dict(id=x.get('UI'),label=text(x)) for x in citation.findall('PublicationType')+citation.findall('PublicationTypeList/PublicationType')]
    for x in citation.findall('Book/ELocationID'):
        scheme=x.get('EIdType','unknown').lower();raw=text(x);value=norm(scheme,raw)
        out['identifiers'].append(dict(scheme=scheme,raw=raw,value=value or raw,valid=bool(value),location='ELocationID'))
    for x in citation.findall('OtherID'):
        raw=text(x);out['identifiers'].append(dict(scheme='otherid:'+x.get('Source','unknown'),raw=raw,value=raw,valid=bool(raw),location='OtherID'))
    for d in e.findall('PubmedBookData/History/PubMedPubDate'):
        out['dates'].append(dict(kind='history',meaning=d.get('PubStatus') or 'history',parts={x.tag:text(x) for x in d}))
    for kind,path in [('book-publication','Book/PubDate'),('revision','DateRevised'),('contribution','ContributionDate'),('book-beginning','Book/BeginningDate'),('book-ending','Book/EndingDate')]:
        for d in citation.findall(path):out['dates'].append(dict(kind=kind,meaning=kind,parts={x.tag:text(x) for x in d}))
    for x in citation.findall('GrantList/Grant'):out['grants'].append({c.tag:text(c) for c in x})
    out['detections']=detect_identifiers([('title',title)]+[('abstract',text(x)) for x in citation.findall('Abstract/AbstractText')])
    return finish(out,e,vocabulary)


def detect_identifiers(texts):
    result=[]
    for location,value in texts:
        for scheme,pattern in [('510k',r'\bK\d{6}\b'),('pma',r'\bP\d{6}\b'),('nct',r'\bNCT\d{8}\b')]:
            for v in sorted(set(re.findall(pattern,value,re.I))):result.append(dict(scheme=scheme,value=v.upper(),location=location,method='explicit-identifier-regex'))
        for v in sorted(set(re.findall(r'\b(?i:FDA product code|product code)(?:\s*[:=]\s*|\s+)([A-Z]{3})\b',value))):
            result.append(dict(scheme='product-code',value=v,location=location,method='explicit-labeled-product-code'))
    return result


def finish(out,e,vocabulary):
    out=add_metadata(out,e)
    transient=[('abstract',text(x)) for x in e.findall('.//AbstractText')]
    out['device_relevance']=assess(out,vocabulary,transient)
    return out
