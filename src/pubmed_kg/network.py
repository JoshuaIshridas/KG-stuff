import hashlib
import os
import re
import time
from pathlib import Path
from urllib.parse import urljoin
import requests
from urllib3.exceptions import HTTPError as Urllib3HTTPError

class Client:
    def __init__(self,rate=3,attempts=4):
        self.session=requests.Session();self.interval=1/rate;self.last=0;self.attempts=attempts
    def get(self,url,**kw):
        for attempt in range(self.attempts):
            time.sleep(max(0,self.interval-(time.monotonic()-self.last)));self.last=time.monotonic()
            try:
                r=self.session.get(url,timeout=(10,120),**kw)
                if r.status_code==429 or r.status_code>=500:
                    r.close();raise requests.ConnectionError('retryable HTTP failure')
                r.raise_for_status();return r
            except requests.RequestException:
                if attempt+1==self.attempts:raise RuntimeError('network request failed (URL and response redacted)') from None
                time.sleep(min(30,2**attempt))
    def download(self,url,target,md5=None):
        target=Path(target);target.parent.mkdir(parents=True,exist_ok=True);part=target.with_suffix(target.suffix+'.part')
        # Keep the exact remote file bytes: HTTP Content-Encoding must not silently
        # decompress .gz resources before checksum validation and XML parsing.
        for attempt in range(self.attempts):
            try:
                with self.get(url,stream=True) as r,part.open('wb') as f:
                    for block in r.raw.stream(1024*1024,decode_content=False):f.write(block)
                h=hashlib.md5()
                with part.open('rb') as f:
                    for b in iter(lambda:f.read(1024*1024),b''):h.update(b)
                if md5 and h.hexdigest()!=md5:raise ValueError('checksum mismatch')
                os.replace(part,target);return target
            except (requests.RequestException,Urllib3HTTPError,ValueError):
                if attempt+1==self.attempts:raise RuntimeError('download failed') from None
                time.sleep(min(30,2**attempt))

def discover(client,base):
    with client.get(base) as r:
        names=sorted(set(re.findall(r'href="(pubmed\d+n\d+\.xml\.gz)"',r.text)))
    return [(n,urljoin(base,n)) for n in names]

def annotations(data,pmid):
    # BioC JSON documents contain passages; discard passage/mention text.
    if not isinstance(data,(list,dict)) or (isinstance(data,dict) and not any(k in data for k in ['PubTator3','documents'])):raise ValueError('unsupported annotation response')
    docs=data if isinstance(data,list) else data.get('PubTator3',data.get('documents',[]))
    if not isinstance(docs,list):raise ValueError('invalid documents collection')
    result=[];found=False
    for doc in docs:
        if not isinstance(doc,dict):raise ValueError('invalid annotation document')
        if str(doc.get('pmid',doc.get('id')))!=str(pmid):continue
        found=True
        if not isinstance(doc.get('passages',[]),list):raise ValueError('invalid passages')
        for passage in doc.get('passages',[]):
            if not isinstance(passage,dict) or not isinstance(passage.get('annotations',[]),list) or not isinstance(passage.get('infons',{}),dict):raise ValueError('invalid annotation passage')
            for a in passage.get('annotations',[]):
                if not isinstance(a,dict) or not isinstance(a.get('infons',{}),dict):raise ValueError('invalid annotation entity')
                info=a.get('infons',{});raw=str(info.get('identifier',''));typ=str(info.get('type','unknown'))
                namespace,sep,value=raw.partition(':')
                if not sep:namespace,value=(str(info['database']),raw) if info.get('database') else ('unmapped:'+typ,raw)
                result.append(dict(entityType=typ,namespace=namespace,normalizedIdentifier=value,rawIdentifier=raw,scope=str(passage.get('infons',{}).get('type','unspecified'))))
    return result,'complete' if found else 'unavailable'
