"""Compatible response decoding, bundled with the desktop application."""
import hashlib
import re
import urllib.parse

def response_payload(response):
    return getattr(response,'http_response',response).json()

def safe(v):
    if isinstance(v,dict):return {k:safe(x) for k,x in v.items()}
    if isinstance(v,list):return [safe(x) for x in v]
    if isinstance(v,str):
        if len(v)>1000 and re.fullmatch(r'[A-Za-z0-9+/=\s]+',v):return {'base64_chars':len(v),'sha256':hashlib.sha256(v.encode()).hexdigest()}
        if v.startswith('data:image/'):return {'data_image_chars':len(v)}
        if v.startswith(('https://','http://')):return '[URL omitted; host='+str(urllib.parse.urlsplit(v).hostname)+']'
        return re.sub(r'sk-[A-Za-z0-9_-]+','[secret omitted]',v)
    return v

def images(v):
    results=[]
    if isinstance(v,dict):
        for k in ('b64_json','base64','image_base64'):
            if isinstance(v.get(k),str) and v[k]:results.append(('b64',v[k]))
        for k in ('url','image_url'):
            x=v.get(k)
            if isinstance(x,str) and x.startswith(('https://','http://','data:image/')):results.append(('url',x))
        for k,x in v.items():
            if k not in ('b64_json','base64','image_base64','url') and isinstance(x,(dict,list)):results+=images(x)
    elif isinstance(v,list):
        for x in v:results+=images(x)
    return list(dict.fromkeys(results))
